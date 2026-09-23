"""上清所新债抓取：保险公司资本补充债/永续债

双栏目覆盖（解决"新债在发行后、上市前漏抓"问题）：
  1. 发行情况报告(新债，发行完成后挂载)  https://www.shclearing.com.cn/xxpl/fxqkbg/jrz01/
     详情页正文为 PDF，经下载网关
       /wcm/shch/pages/client/download/download.jsp?FileName=P0xxx.pdf
     用 pymupdf 解析发行要素（债券代码/简称/起息日/发行额/票面/期限）。
  2. 交易流通要素公告(上市后挂载)      https://www.shclearing.com.cn/xxpl/jyltysgg/jrz_547/
     详情页为 HTML <td> 表格，直接解析字段。

【去重铁律】同一只债可能先后出现在"发行情况报告"（发行完）与"交易流通要素
公告"（上市后）。两个栏目共用同一 known = {bondCode} 集合，以 bondCode 判重，
同一只债无论先以发行公告、后以流通公告出现，只入库一次，绝不重复。

限流敏感（重要）：上清所对高频访问会返回 404/429 软封禁（实测）。
  - 页间/详情页间/下载间强制间隔 >=2.5s；
  - 每请求最多 3 次重试，退避 30s；
  - 任何失败都不阻塞 CI 主流程，报告落盘 _shc_report.json。
  - PDF 下载经过 download.jsp 网关（直连 P0xxx.pdf 会 404），需正确 Referer。

用法：
  python fetch_shc_newbonds.py             # 增量（缺省 7 天窗口，两栏目都查）
  python fetch_shc_newbonds.py --backfill  # 回溯全部历史页（一次性, 约8-10分钟）
  python fetch_shc_newbonds.py --no-pdf    # 跳过 PDF 解析（缺 pymupdf 时自动）
"""
import argparse
import json
import re
import sys
import time
from datetime import datetime, timedelta

import requests

try:
    import pymupdf
    HAS_PYMUPDF = True
except Exception:  # pragma: no cover
    pymupdf = None
    HAS_PYMUPDF = False

# ---------------- 栏目配置 ----------------
CAT_LIUTONG = "https://www.shclearing.com.cn/xxpl/jyltysgg/jrz_547/"
CAT_FXQKBG = "https://www.shclearing.com.cn/xxpl/fxqkbg/jrz01/"
# 附件下载网关（直连 P0xxx.pdf 会 404，必须走此网关）
DOWNLOAD_JSP = "https://www.shclearing.com.cn/wcm/shch/pages/client/download/download.jsp"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    "Referer": CAT_LIUTONG + "index.html",
}
GAP = 2.5          # 两次请求最小间隔（限流保护）
RETRY = 3
RETRY_BACKOFF = 30

INS_PAT = re.compile(r"保险|人寿|财险|养老|再保")

# 交易流通栏目列表页（原有）
LIST_PAT_LT = re.compile(
    r'href="(\./\d{6}/t\d{8}_\d+\.html)"\s+target="_blank">\s*<p>\s*([^<]+?)\s*</p>'
    r'.*?pubdate\s*=\s*\'(\d{4}-\d{2}-\d{2})\'',
    re.S,
)
# 发行情况报告栏目列表页：<a href="./...+html"><p>标题</p><span>YYYY-MM-DD</span></a>
LIST_PAT_FX = re.compile(
    r'<a href="(\./\d{6}/t\d{8}_\d+\.html)"[^>]*>\s*<p>\s*([^<]+?)\s*</p>'
    r'\s*<span>\s*([\d-]+)\s*</span>',
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


def fetch_bytes(url, accept="application/pdf,*/*"):
    """下载二进制（PDF），走正确的 Header/Referer。失败返回 None。"""
    for i in range(RETRY):
        _pace()
        try:
            _last_req[0] = time.time()
            h = dict(HEADERS)
            h["Accept"] = accept
            r = requests.get(url, headers=h, timeout=40)
            if r.status_code == 200 and r.content[:5] == b"%PDF-":
                return r.content
            print(f"  [warn] dl {url[-60:]} -> HTTP {r.status_code}/len{len(r.content)} (第{i+1}次)")
        except Exception as e:
            print(f"  [warn] dl {url[-60:]} -> {str(e)[:60]} (第{i+1}次)")
        if i < RETRY - 1:
            time.sleep(RETRY_BACKOFF)
    return None


def parse_list(html, pattern):
    items = []
    for href, title, pubdate in pattern.findall(html):
        items.append({"url": href, "title": title.strip(), "pubdate": pubdate})
    return items


def parse_detail(html):
    """交易流通栏目详情页 <td> 标签对 -> 字段 dict。"""
    rows = re.findall(r"<td[^>]*>([^<]{1,80})</td>\s*<td[^>]*>([^<]{1,300})</td>", html)
    out = {}
    for k, v in rows:
        out[k.strip()] = v.strip()
    return out


def _first(token):
    return token.split("/")[0].strip() if token else ""


# ---------------- PDF 解析（发行情况报告栏目） ----------------
def extract_pdf_field(doc_txt_orig):
    """从发行情况公告 PDF 文本提取发行要素。

    返回 dict 或 None。文本先仅去除水平空白(保留换行)用于定位 '债券简称'，
    再全去空白用于键字段正则（PDF 排版常在数字与中文间插入空格）。
    """
    txt = re.sub(r"[ \t]+", "", doc_txt_orig)          # 去水平空白保留换行
    txt_ns = re.sub(r"\s+", "", doc_txt_orig)          # 全去空白

    def g1(pattern):
        m = re.search(pattern, txt_ns)
        return m.group(1) if m else None

    code = g1(r"债券代码\s*([0-9]{9})")
    if not code:
        return None

    rate_s = g1(r"票面利率\s*([\d.]+)\s*%")
    try:
        rate = round(float(rate_s), 4) if rate_s else None
    except ValueError:
        rate = None

    amt_s = g1(r"实际发行总额\s*([\d.]+)\s*亿元") or g1(r"计划发行总额\s*([\d.]+)\s*亿元")
    try:
        amt = round(float(amt_s), 2) if amt_s else None
    except ValueError:
        amt = None

    def _zh_date(raw):
        """'2026年9月24日' -> '2026-09-24'; None if bad。"""
        if not raw:
            return None
        try:
            y, m, d = re.findall(r"(\d+)年(\d+)月(\d+)日", raw)[0]
            return f"{int(y):04d}-{int(m):02d}-{int(d):02d}"
        except (IndexError, ValueError):
            return None

    vd = _zh_date(g1(r"起息日\s*([0-9年月日]+)"))
    # 发行日优先取"发行日"，其次"簿记建档日"，都没有则回退 None（由调用方用公告日兜底）
    idate = _zh_date(g1(r"发行日\s*([0-9年月日]+)")) or _zh_date(g1(r"簿记建档日\s*([0-9年月日]+)"))

    # 债券简称(定位需保留换行的原始文本)
    short = None
    m = re.search(r"债券简称\s*\n?([^\n]{4,24})", txt)
    if m:
        short = m.group(1).strip()
        short = re.split(r"债券代码|[0-9]{8,9}|票面利率", short)[0].strip()

    perpetual = "无固定期限" in txt_ns or "永续" in txt_ns
    has_redemption = "赎回" in txt_ns
    year10 = "10年期" in txt_ns or "10 年期" in txt_ns or "十年" in txt_ns

    if perpetual:
        bond_type, period = "永续债", "5+N年"
    else:
        bond_type, period = "资本补充债", "5+5年"

    return {
        "code": code,
        "short": short,
        "rate": rate,
        "amt": amt,
        "valueDate": vd,
        "issueDate": idate,
        "bondType": bond_type,
        "period": period,
        "perpetual": perpetual,
        "hasRedemption": has_redemption,
        "year10": year10,
        "fullRaw": txt_ns[:400],
    }


def download_and_parse_pdf(detail_url):
    """详情页 -> currentFile(P0xxx.pdf) -> 下载网关 -> 解析发行要素。"""
    html = fetch(detail_url)
    if html is None:
        return None, "detail page failed"
    m = re.search(r'currentFile\s*=\s*"([^"]+\.pdf)"', html)
    if not m:
        return None, "no pdf currentFile"
    pdf_rel = m.group(1)  # "./P0xxx.pdf"
    file_name = pdf_rel.rsplit("/", 1)[-1]

    # 下载时 Referer 指向详情页
    h = dict(HEADERS)
    h["Referer"] = detail_url
    data = None
    for i in range(RETRY):
        _pace()
        try:
            _last_req[0] = time.time()
            hh = dict(h); hh["Accept"] = "application/pdf,*/*"
            r = requests.get(DOWNLOAD_JSP, params={"FileName": file_name},
                             headers=hh, timeout=40)
            if r.status_code == 200 and r.content[:5] == b"%PDF-":
                data = r.content
                break
            print(f"  [warn] pdf {file_name} HTTP {r.status_code}/len{len(r.content)} (第{i+1}次)")
        except Exception as e:
            print(f"  [warn] pdf {file_name} -> {str(e)[:60]} (第{i+1}次)")
        if i < RETRY - 1:
            time.sleep(RETRY_BACKOFF)
    if data is None:
        return None, f"pdf download failed {file_name}"

    if not HAS_PYMUPDF:
        return None, "pymupdf missing"
    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
        text = "\n".join(p.get_text() for p in doc)
    except Exception as e:
        return None, f"pdf parse failed {str(e)[:60]}"
    return extract_pdf_field(text), None


def build_record_from_pdf(f, title):
    """发行情况报告 PDF 字段 -> 标准债券记录（评级沿用发行人历史，仅当无披露时）。
    f 为 extract_pdf_field 返回值。
    """
    full = title.replace("发行情况公告", "").strip()
    code = f["code"]
    short = f["short"] or ("-")
    issuer = re.sub(r"2026 ?年.*$", "", full).replace("保险股份有限公司", "保险股份有限公司").strip()
    # 更精确：发行人称谓来自标题，去掉"2026年...资本债券..."只留发行主体
    issuer = re.split(r"20\d\d ?年", full)[0].strip().replace("股份有限公司", "股份有限公司")
    if "再保" in issuer:
        industry = "再保"
    elif "财险" in issuer or "财产保险" in issuer:
        industry = "财险"
    else:
        industry = "寿险"

    value_date = f["valueDate"]
    if perpetual := f["perpetual"]:
        bond_type, period = "永续债", "5+N年"
        call = None
        mrty = None
        if value_date:
            try:
                vd = datetime.strptime(value_date, "%Y-%m-%d")
                call = vd.replace(year=vd.year + 5).strftime("%Y-%m-%d")
                mrty = call  # 永续债 mrty 用行权日口径
            except ValueError:
                pass
    else:
        bond_type, period = "资本补充债", "5+5年"
        call = None
        mrty = None
        if value_date:
            try:
                vd = datetime.strptime(value_date, "%Y-%m-%d")
                call = vd.replace(year=vd.year + 5).strftime("%Y-%m-%d")
                mrty = vd.replace(year=vd.year + 10).strftime("%Y-%m-%d")
            except ValueError:
                pass

    return {
        "bondDefinedCode": "shc_" + code,
        "issuer": issuer,
        "bondShort": short,
        "bondFull": full,
        "bondCode": code,
        "bondType": bond_type,
        "industry": industry,
        "issueDate": f["issueDate"] or None,  # 优先 PDF 内发行日/簿记建档日
        "valueDate": value_date,
        "mrtyDate": mrty,
        "bondPeriod": period,
        "planAmnt": f["amt"],
        "issueAmnt": f["amt"],
        "couponRate": f["rate"],
        "couponType": "附息式固定利率",
        "couponFrqncy": "年",
        "debtRating": None,
        "ratingStr": None,
        "exerciseFlag": "是" if f["hasRedemption"] else "否",
        "callDate": call,
        "status": "存续",
        "source": "上清所发行公告",
    }


def build_record(d, title):
    """交易流通栏目 HTML 表格 -> 标准债券记录（原有逻辑）。"""
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
    debt = _first(d.get("产品评级", ""))
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


def run_list_pages(list_url, page_count, pattern, cutoff, is_backfill, report):
    """通用列表页抓取：返回 items 列表。"""
    items, seen = [], set()
    for pg in range(page_count):
        url = list_url + ("index.html" if pg == 0 else f"index_{pg}.html")
        html = fetch(url)
        if html is None:
            report["blocked"] = True
            report["errors"].append(f"page {pg} fetch failed ({list_url})")
            break
        report["pagesFetched"] += 1
        got = parse_list(html, pattern)
        if not got:
            break
        items.extend(got)
        if not is_backfill and all(it["pubdate"] < cutoff for it in got):
            break
    uniq = {}
    for it in items:
        uniq[it["url"]] = it
    return list(uniq.values())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--backfill", action="store_true", help="回溯全部历史页（一次性）")
    ap.add_argument("--days", type=int, default=7, help="增量窗口天数")
    ap.add_argument("--max-pages", type=int, default=40, help="增量模式各栏目最大翻页数")
    ap.add_argument("--no-pdf", action="store_true", help="跳过发行情况报告 PDF 解析")
    args = ap.parse_args()

    report = {"runAt": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
              "mode": "backfill" if args.backfill else "incremental",
              "pagesFetched": 0, "itemsTotal": 0, "insFound": 0,
              "newBonds": [], "blocked": False, "errors": []}

    with open("ins_bonds.json", encoding="utf-8") as f:
        data = json.load(f)
    bonds = data["bonds"]
    known = {b.get("bondCode") for b in bonds}
    added = []
    cutoff = (datetime.now() - timedelta(days=args.days)).strftime("%Y-%m-%d")
    page_count = 67 if args.backfill else args.max_pages

    def do_add(rec, src_label):
        if rec is None:
            return None
        code = rec.get("bondCode")
        if not code or code in known:
            return None  # 已在库 -> 跳过（防重复）
        # 上级指标缺省（发行情况报告无评级时，沿用发行人历史评级）
        if rec.get("debtRating") is None or rec.get("ratingStr") is None:
            hist = next((b for b in bonds if b.get("issuer") == rec.get("issuer")
                         and b.get("debtRating")), None)
            if hist:
                rec["debtRating"] = hist.get("debtRating")
                rec["ratingStr"] = hist.get("ratingStr")
            else:
                rec["debtRating"] = rec["debtRating"] or "---"
                rec["ratingStr"] = rec["ratingStr"] or "---/---"
        if not rec.get("issueDate"):
            rec["issueDate"] = src_label.get("pubdate") or rec.get("valueDate")
        if not rec.get("valueDate"):
            rec["valueDate"] = rec.get("issueDate")
        bonds.append(rec)
        known.add(code)
        added.append(rec)
        print(f"  [新债] {rec['bondShort']} ({code}) {rec['valueDate']} [{src_label.get('title','')[:30]}]")
        return rec

    # ---------- 栏目1：发行情况报告（fxqkbg/jrz01，新债主渠道） ----------
    if not args.no_pdf and HAS_PYMUPDF:
        fx_items = run_list_pages(CAT_FXQKBG, page_count, LIST_PAT_FX, cutoff,
                                  args.backfill, report)
        fx_ins = [it for it in fx_items if INS_PAT.search(it["title"])]
        report["fxItems"] = len(fx_items)
        report["fxIns"] = len(fx_ins)
        report["fxInsTitles"] = [f"{it['pubdate']} {it['title'][:60]}" for it in fx_ins]
        report["itemsTotal"] += len(fx_items)
        report["insFound"] += len(fx_ins)
        for it in fx_ins:
            detail_url = CAT_FXQKBG + it["url"].lstrip("./")
            f, err = download_and_parse_pdf(detail_url)
            if f is None:
                report["errors"].append(f"fxqkbg pdf fail: {it['title'][:40]} ({err})")
                continue
            rec = build_record_from_pdf(f, it["title"])
            do_add(rec, it)

    # ---------- 栏目2：交易流通要素公告（jrz_547，原有逻辑） ----------
    lt_items = run_list_pages(CAT_LIUTONG, page_count, LIST_PAT_LT, cutoff,
                              args.backfill, report)
    lt_ins = [it for it in lt_items if INS_PAT.search(it["title"])]
    report["ltItems"] = len(lt_items)
    report["ltIns"] = len(lt_ins)
    report["ltInsTitles"] = [f"{it['pubdate']} {it['title'][:60]}" for it in lt_ins]
    report["itemsTotal"] += len(lt_items)
    report["insFound"] += len(lt_ins)
    todo = lt_ins[:80 if args.backfill else 300]
    for it in todo:
        html = fetch(CAT_LIUTONG + it["url"].lstrip("./"))
        if html is None:
            report["errors"].append("detail fail: " + it["title"][:60])
            continue
        d = parse_detail(html)
        rec = build_record(d, it["title"])
        if rec is None:
            continue
        do_add(rec, it)

    if added:
        data["count"] = len(bonds)
        data["generatedAt"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open("ins_bonds.json", "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
    report["newBonds"] = [b["bondShort"] for b in added]

    with open("_shc_report.json", "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)
    print(f"[shc] mode={report['mode']} pages={report['pagesFetched']} "
          f"发行报告INS={report.get('fxIns',0)} 流通INS={report.get('ltIns',0)} "
          f"新增={len(added)} blocked={report['blocked']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
