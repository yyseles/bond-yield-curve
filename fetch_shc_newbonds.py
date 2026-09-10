"""上清所新债抓取：金融债券交易流通要素公告（保险公司资本补充债/永续债）

背景：chinamoney 对 CI/本机 IP 全站 403 后，新债发行信息改从上清所官方栏目抓取。
栏目：https://www.shclearing.com.cn/xxpl/jyltysgg/jrz_547/ （静态 HTML，index_N.html 翻页，
覆盖 2023-11 至今共 67 页）。详情页 <td> 对含全称/简称/代码/发行额/起息日/到期日/票面/评级。

限流敏感（重要）：上清所对高频访问会返回 404/429 软封禁（实测）。
  - 页间/详情页间强制间隔 >=2.5s；
  - 每请求最多 3 次重试，退避 30s；
  - 任何失败都不阻塞 CI 主流程，报告落盘 _shc_report.json。

用法：
  python fetch_shc_newbonds.py             # 增量：翻页直到条目早于 7 天前（最多 4 页）
  python fetch_shc_newbonds.py --backfill  # 一次性回溯全部 67 页（CI 验证历史完整性用，约 8 分钟）
"""
import argparse
import json
import re
import sys
import time
from datetime import datetime, timedelta

import requests

BASE = "https://www.shclearing.com.cn/xxpl/jyltysgg/jrz_547/"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    "Referer": BASE + "index.html",
}
GAP = 2.5          # 两次请求最小间隔（限流保护）
RETRY = 3
RETRY_BACKOFF = 30

INS_PAT = re.compile(r"保险|人寿|财险|养老|再保")

LIST_PAT = re.compile(
    r'href="(\./\d{6}/t\d{8}_\d+\.html)"\s+target="_blank">\s*<p>\s*([^<]+?)\s*</p>'
    r'.*?pubdate\s*=\s*\'(\d{4}-\d{2}-\d{2})\'',
    re.S,
)

_last_req = [0.0]


def _pace():
    wait = GAP - (time.time() - _last_req[0])
    if wait > 0:
        time.sleep(wait)


def fetch(url):
    """带节奏控制与重试的 GET。失败返回 None（不抛异常）。"""
    for i in range(RETRY):
        _pace()
        try:
            _last_req[0] = time.time()
            r = requests.get(url, headers=HEADERS, timeout=25)
            if r.status_code == 200 and r.text:
                return r.text
            print(f"  [warn] {url[-60:]} -> HTTP {r.status_code} (第{i+1}次)")
        except Exception as e:
            print(f"  [warn] {url[-60:]} -> {str(e)[:60]} (第{i+1}次)")
        if i < RETRY - 1:
            time.sleep(RETRY_BACKOFF)
    return None


def parse_list(html):
    items = []
    for href, title, pubdate in LIST_PAT.findall(html):
        items.append({
            "url": BASE + href.lstrip("./"),
            "title": title.strip(),
            "pubdate": pubdate,
        })
    return items


def parse_detail(html):
    """详情页 <td> 标签对 -> 字段 dict。"""
    rows = re.findall(r"<td[^>]*>([^<]{1,80})</td>\s*<td[^>]*>([^<]{1,300})</td>", html)
    out = {}
    for k, v in rows:
        out[k.strip()] = v.strip()
    return out


def _first(token):
    """'AAA/AAA' -> 'AAA'（多评级机构取第一家）"""
    return token.split("/")[0].strip() if token else ""


def build_record(d, title):
    full = d.get("产品全称", "")
    short = d.get("产品简称", "")
    code = d.get("产品代码", "")
    if not (full and short and re.fullmatch(r"\d{9}", code)):
        return None
    if not INS_PAT.search(full + title):
        return None
    perpetual = ("无固定期限" in full) or ("永续" in title) or ("永续" in full)
    issue_date = d.get("发行（创设）日", "")
    value_date = d.get("登记日", "") or issue_date
    mrty = d.get("到期（兑付）日", "")
    try:
        vd = datetime.strptime(value_date, "%Y-%m-%d")
        call = (vd.replace(year=vd.year + 5)).strftime("%Y-%m-%d")
        if not mrty:
            mrty = (vd.replace(year=vd.year + 10)).strftime("%Y-%m-%d")
    except ValueError:
        return None
    if perpetual:
        bond_type, period = "永续债", "5+N年"
    else:
        bond_type, period = "资本补充债", "5+5年"
    debt = _first(d.get("产品评级", ""))    # 债项评级
    issuer_rating = _first(d.get("主体评级", ""))
    try:
        amt = round(float(d.get("发行（创设）总额（亿元）", "0")), 2)
        rate = round(float(d.get("票面年利率（%）", "0")), 4)
    except ValueError:
        return None
    if "财" in d.get("发行（创设）机构名称", ""):
        industry = "财险"
    elif "再保" in d.get("发行（创设）机构名称", ""):
        industry = "再保"
    else:
        industry = "寿险"
    return {
        "bondDefinedCode": "shc_" + code,
        "issuer": d.get("发行（创设）机构名称", ""),
        "bondShort": short,
        "bondFull": full,
        "bondCode": code,
        "bondType": bond_type,
        "industry": industry,
        "issueDate": issue_date,
        "valueDate": value_date,
        "mrtyDate": mrty,
        "bondPeriod": period,
        "planAmnt": amt,
        "issueAmnt": amt,
        "couponRate": rate,
        "couponType": "附息式固定利率" if "固定" in d.get("计息方式", "") else "附息式浮动利率",
        "couponFrqncy": "年",
        "debtRating": issuer_rating,
        "ratingStr": f"{debt}/{issuer_rating}",
        "exerciseFlag": "是",
        "callDate": call,
        "status": "存续",
        "source": "上清所公告",
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--backfill", action="store_true", help="回溯全部历史页（一次性）")
    ap.add_argument("--days", type=int, default=7, help="增量窗口天数")
    ap.add_argument("--max-pages", type=int, default=40,
                    help="增量模式最大翻页数（栏目约3-4条/天，7天≈30页，取40留余量）")
    args = ap.parse_args()

    report = {"runAt": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
              "mode": "backfill" if args.backfill else "incremental",
              "pagesFetched": 0, "itemsTotal": 0, "insFound": 0,
              "newBonds": [], "blocked": False, "errors": []}

    # 1. 拉列表页
    pages = 67 if args.backfill else args.max_pages
    cutoff = (datetime.now() - timedelta(days=args.days)).strftime("%Y-%m-%d")
    items, seen = [], set()
    for pg in range(pages):
        url = BASE + ("index.html" if pg == 0 else f"index_{pg}.html")
        html = fetch(url)
        if html is None:
            report["blocked"] = True
            report["errors"].append(f"page {pg} fetch failed")
            break
        report["pagesFetched"] += 1
        got = parse_list(html)
        if not got:
            break  # 翻到头
        items.extend(got)
        if not args.backfill and all(it["pubdate"] < cutoff for it in got):
            break  # 已翻过增量窗口
    # 去重
    for it in items:
        seen.add(it["url"])
    items = [it for it in items if it["url"] in seen]
    report["itemsTotal"] = len(items)

    # 2. 过滤保险类公告
    ins = [it for it in items if INS_PAT.search(it["title"])]
    report["insFound"] = len(ins)

    # 3. 与现有库比对：保险类公告全量进详情页确认代码后再判重（每周约5条保险公告，量小）
    todo = ins[:80 if args.backfill else 10]

    added = []
    with open("ins_bonds.json", encoding="utf-8") as f:
        data = json.load(f)
    bonds = data["bonds"]
    known = {b.get("bondCode") for b in bonds}
    for it in todo:
        html = fetch(it["url"])
        if html is None:
            report["errors"].append("detail fail: " + it["title"][:60])
            continue
        d = parse_detail(html)
        rec = build_record(d, it["title"])
        if rec is None:
            continue
        if rec["bondCode"] in known:
            continue
        bonds.append(rec)
        known.add(rec["bondCode"])
        added.append(rec)
        print(f"  [新债] {rec['bondShort']} ({rec['bondCode']}) {rec['issueDate']}")

    if added:
        data["count"] = len(bonds)
        data["generatedAt"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open("ins_bonds.json", "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
    report["newBonds"] = [b["bondShort"] for b in added]

    with open("_shc_report.json", "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)
    print(f"[shc] mode={report['mode']} pages={report['pagesFetched']} "
          f"ins={report['insFound']} 新增={len(added)} blocked={report['blocked']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
