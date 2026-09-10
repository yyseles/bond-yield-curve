# -*- coding: utf-8 -*-
"""按中债登「保险公司资本补充债券」栏目公告修正债券状态(一次性/可重跑)。

数据源: POST https://www.chinabond.com.cn/cbiw/trs/getContentByConditions
        parentChnlName=fxdfyxqgg_bxgszbbczq  (信息披露>金融债券>付息兑付与行权公告>保险公司资本补充债券)
该栏目为登记托管机构官方披露, 标题标准写明"赎回选择权行使/不行使", 是赎回状态的权威依据。

同时修正历史数据错误: 部分 5+5年 债的 mrtyDate 被写成第5年末(等于 callDate),
导致被误判"已到期"; 统一修正为 发行日+10年。
"""
import json
import os
import sys
from datetime import date, timedelta

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fetch_ins_bonds as F  # noqa: E402

CB_URL = "https://www.chinabond.com.cn/cbiw/trs/getContentByConditions"
CB_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/109.0.0.0 Safari/537.36"),
    "Content-Type": "application/json;charset=UTF-8",
    "Referer": ("https://www.chinabond.com.cn/xxpl/ywzc_fxyfxdh/fxyfxdh_zqzl/zqzl_jrzq/"
                "jrzq_fxdfyxqgg/fxdfyxqgg_bxgszbbczq/index.html"),
}
CHNL = "fxdfyxqgg_bxgszbbczq"


def fetch_notices(pages=8, page_size=100):
    """拉取栏目全量公告。"""
    out, seen = [], set()
    for pg in range(1, pages + 1):
        body = {"parentChnlName": CHNL, "excludeParentChnlNames": [], "childChnlDesc": "",
                "jrzqChnlName": "", "hasAppendix": True, "siteName": "chinaBond",
                "pageSize": page_size, "pageNum": pg,
                "queryParam": {"keywords": "", "startDate": "", "endDate": "",
                               "reportType": "", "reportYear": "", "ratingAgency": ""}}
        r = requests.post(CB_URL, json=body, headers=CB_HEADERS, timeout=30)
        r.raise_for_status()
        lst = ((r.json().get("data") or {}).get("list") or [])
        for x in lst:
            t = (x.get("docTitle") or "").strip()
            if t and t not in seen:
                seen.add(t)
                out.append({"title": t, "url": x.get("docPubUrl") or "", "date": ""})
        if len(lst) < page_size:
            break
    return out


def _parse(s):
    try:
        y, m, d = str(s).split("-")
        return date(int(y), int(m), int(d))
    except Exception:
        return None


def fix_mrty_date(bonds):
    """修正 5+5年 债被错写成第5年末的 mrtyDate -> 发行日+10年。返回修正列表。"""
    fixed = []
    for b in bonds:
        if b.get("bondPeriod") != "5+5年":
            continue
        i, m, c = _parse(b.get("issueDate")), _parse(b.get("mrtyDate")), _parse(b.get("callDate"))
        if not (i and m and c):
            continue
        if m == c or (m - i).days < 3000:  # 明显是第5年而非第10年
            new_m = _add_years(i, 10)
            if new_m != m:
                fixed.append((b.get("bondShort"), b.get("mrtyDate"), new_m.isoformat()))
                b["mrtyDate"] = new_m.isoformat()
    return fixed


def _add_years(d, n):
    try:
        return d.replace(year=d.year + n)
    except ValueError:  # 2月29日
        return d.replace(year=d.year + n, day=28)


def recompute_status(bonds):
    """无公告覆盖的债, 按修正后的日期重算状态。"""
    today = date.today()
    changed = []
    for b in bonds:
        m, c = _parse(b.get("mrtyDate")), _parse(b.get("callDate"))
        perp = "永续" in (b.get("bondType") or "") or "N" in (b.get("bondPeriod") or "")
        st = b.get("status")
        if perp:
            new = "存续"
        elif m and m < today:
            new = "已到期"
        else:
            new = st  # 无公告不作推断
        if new != st:
            changed.append((b.get("bondShort"), st, new))
            b["status"] = new
    return changed


def main():
    d = json.load(open(F.DATA_FILE, encoding="utf-8"))
    bonds = d["bonds"]

    print("=== 1. 修正错写的 mrtyDate ===")
    fixed = fix_mrty_date(bonds)
    for s, o, n in fixed:
        print(f"  [mrty] {s:<20} {o} -> {n}")
    print(f"  共 {len(fixed)} 条")

    print("=== 2. 拉取中债登公告 ===")
    notices = fetch_notices()
    red = [n for n in notices if "赎回" in n["title"]]
    print(f"  栏目公告 {len(notices)} 条, 其中赎回类 {len(red)} 条")

    print("=== 3. 无公告的重算(仅到期判定, 先跑) ===")
    rec = recompute_status(bonds)
    for s, o, n in rec:
        print(f"  [重算] {s:<22} {o} -> {n}")
    print(f"  变更 {len(rec)} 条")

    print("=== 4. 按公告更新状态(最后覆盖, 优先级最高) ===")
    ch, sk = F.apply_notice_status(bonds, red, verbose=False)
    for s, o, n, why in ch:
        print(f"  [公告] {s:<22} {o} -> {n}  | {why[:52]}")
    print(f"  变更 {len(ch)} 条")
    amb = [(t, w) for t, w in sk if "候选" in w or "无匹配" in w]
    if amb:
        print("  --- 需人工确认(未自动改) ---")
        for t, w in amb:
            print(f"    {t[:56]} ({w})")

    bonds.sort(key=lambda x: x.get("issueDate") or "", reverse=True)
    d["bonds"] = bonds
    d["count"] = len(bonds)
    d["generatedAt"] = date.today().isoformat()
    json.dump(d, open(F.DATA_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    from collections import Counter
    print("=== 最终状态分布 ===")
    print(" ", dict(Counter(b["status"] for b in bonds)))


if __name__ == "__main__":
    main()
