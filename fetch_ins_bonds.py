#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fetch_ins_bonds.py
从 中国货币网(chinamoney)「债券信息综合查询」抓取保险公司发行的资本补充债与
无固定期限资本债券(永续债)全量数据，输出 ins_bonds.json。

数据源:
  - 列表: AKShare bond_info_cm (接口 BondMarketInfoList2), 按 债券类型 × 发行年份 分页拉全
  - 详情: 直接调 BondDetailInfo(bondDefinedCode=查询代码), 补全 票面利率/起息日/到期日/发行量/评级/含权
  - 赎回公告: POST /ags/ms/cm-u-notice-issue/majorMatters (披露->重大事项->行使公告),
    标题"行使赎回选择权"->已赎回 / "不行使"->存续; 周频增量21天, 首次 --notice-days 0 回填约3年

处理逻辑:
  - 永续债类型(无固定期限资本债券)含银行债, 按发行人名过滤只留保险公司
  - 行业(产/寿/再保/集团)按发行人名关键字推断
  - 状态(存续/已赎回/已到期)按 起息日+5年(call) 与 到期兑付日 相对运行日判定
    (赎回只改状态, 不动发行总额 —— 由前端/汇总时按 issueYear 聚合保证)

用法:
  python fetch_ins_bonds.py              # 抓全量
  python fetch_ins_bonds.py --year 2025  # 只抓某年(调试)
"""
import argparse
import json
import os
import re
import sys
import time
from datetime import date, datetime, timedelta

import requests

HERE = __import__("os").path.dirname(__import__("os").path.abspath(__file__))
DATA_FILE = __import__("os").path.join(HERE, "ins_bonds.json")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/109.0.0.0 Safari/537.36",
}
DETAIL_HEADERS = {
    **HEADERS,
    "host": "www.chinamoney.com.cn",
    "origin": "https://www.chinamoney.com.cn",
    "referer": "https://www.chinamoney.com.cn/chinese/zqjc/",
}

LIST_URL = "https://www.chinamoney.com.cn/ags/ms/cm-u-bond-md/BondMarketInfoList2"
DETAIL_URL = "https://www.chinamoney.com.cn/ags/ms/cm-u-bond-md/BondDetailInfo"

BOND_TYPES = ["保险公司资本补充债", "无固定期限资本债券"]
START_YEAR = 2012  # 资本补充债最早 2015 前后, 永续 2019 前后; 宽一点无妨

# ---------- 工具 ----------

def _norm_date(s):
    if not s or s in ("---", "0", "None", "null"):
        return None
    m = re.search(r"(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})", str(s))
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def _to_float(s):
    if s is None or s in ("---", "", "None"):
        return None
    try:
        return float(str(s).replace(",", "").replace("%", ""))
    except ValueError:
        return None


def is_insurance(issuer: str) -> bool:
    if not issuer:
        return False
    # 排除银行类(永续债类型混有大量银行)
    if re.search(r"银行|商行|农商|信用社", issuer):
        return False
    return bool(re.search(r"保险|人寿|财险|产险|再保险|养老|健康|相互|信保", issuer))


def infer_industry(issuer: str) -> str:
    if not issuer:
        return "其他"
    if "再保险" in issuer:
        return "再保"
    if re.search(r"财险|财产|产险", issuer):
        return "产险"
    if re.search(r"集团|控股", issuer):
        return "集团"
    return "寿险"


def derive_call_date(value_date: date, bond_period: str):
    """含权债的 call 日: 从 '5+5年' / '5+N年' 取 '+' 前的年数, 加到起息日。"""
    if not value_date or not bond_period:
        return None
    m = re.search(r"(\d+)\s*\+", str(bond_period))
    if not m:
        return None
    try:
        years = int(m.group(1))
    except ValueError:
        return None
    try:
        return value_date.replace(year=value_date.year + years)
    except ValueError:  # 闰年 02-29
        return value_date + timedelta(days=365 * years)


def compute_status(value_date, mrty_date, call_date, exercise_flag, is_perpetual=False):
    """仅按到期日判 已到期/存续。
    注意: call 过期绝不推断"已赎回" —— 有公司不行使第5年赎回权,
    赎回与否必须以官方公告(行使/不行使)或摘牌信号为准(用户规则 2026-09-08)。"""
    today = date.today()
    if mrty_date and mrty_date < today:
        # 永续债的 mrtyDate 是首个赎回日而非真实到期日, 不据此判已到期
        if not is_perpetual:
            return "已到期"
    return "存续"


# chinamoney note 字段里出现以下关键词之一时, 即认定为官方已标"已赎回/已兑付"。
_REDEEM_NOTE_KEYS = ("已赎回", "提前赎回", "已兑付", "赎回完成", "兑付完成")


def _maybe_update_status_from_detail(rec, info):
    """用 chinamoney 详情接口的真实信号(摘牌日/备注) 修正已存在债的 status。

    返回 ("updated", 新status) / ("kept", 旧status) / ("skipped", 原因)。

    严格保护:
      - rec["source"] == "Excel(用户维护)"  永不动(用户手录最高优先级)
      - detail 接口的 dlstngDate / note 缺失或为 "---" 不动
    """
    if not info or not isinstance(info, dict):
        return ("skipped", "no-info")
    if rec.get("source") == "Excel(用户维护)":
        return ("skipped", "excel-source")
    today = date.today()
    dlstng = _norm_date(info.get("dlstngDate"))
    note = (info.get("note") or "").strip()
    # 优先级 1: 官方摘牌日非空且 < 今天, 视债券已退出流通
    if dlstng and dlstng <= today:
        # 区分已赎回 vs 已到期: 看 mrtyDate 是否已到
        m = _norm_date(info.get("mrtyDate"))
        if m and m <= today and (not _norm_date(info.get("frstValueDate")) or
                                  (m - (_norm_date(info.get("frstValueDate")) or m)).days >= 365 * 8):
            new = "已到期"
        else:
            new = "已赎回"
        if rec.get("status") != new:
            old = rec.get("status")
            rec["status"] = new
            return ("updated", (old, new, f"dlstngDate={dlstng.isoformat()}"))
        return ("kept", rec.get("status"))
    # 优先级 2: 备注里有"已赎回/已兑付"等关键字
    if note and note != "---" and any(k in note for k in _REDEEM_NOTE_KEYS):
        new = "已赎回"
        if rec.get("status") != new:
            old = rec.get("status")
            rec["status"] = new
            return ("updated", (old, new, f"note='{note[:20]}'"))
        return ("kept", rec.get("status"))
    return ("kept", rec.get("status"))


# ---------- 稳健去重（修复"两个数据源 + 改名/带序号简称"造成的重复）----------

def _issuer_tokens(s):
    """发行人匹配令牌集合: 含现名与'(原:X)'中的旧名, 用于跨数据源/改名识别同一公司。"""
    s = str(s or "").strip()
    toks = {s}
    m = re.search(r"[（(]原[:：]?\s*([^）)]+)", s)
    if m:
        toks.add(m.group(1).strip())
    m2 = re.search(r"[（(]原", s)
    if m2:
        toks.add(s[:m2.start()].strip())
    return frozenset(toks)


def _strip_seq(s):
    """去掉债券简称末尾两位发行序号(01/02/03), 用于 '26中英人寿永续债' == '26中英人寿永续债01'。"""
    return re.sub(r"[0-9]{2}$", "", str(s or "").strip())


_CN_NUM = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6,
           "七": 7, "八": 8, "九": 9, "十": 10}


def _cn2int(s):
    """中文/阿拉伯数字期数 -> int (如 '第一期'/'第1期' -> 1)。"""
    s = str(s)
    if s.isdigit():
        return int(s)
    n = 0
    for ch in s:
        if ch == "十":
            n = n * 10 + 10 if n else 10
        elif ch in _CN_NUM:
            n = n * 10 + _CN_NUM[ch] if n else _CN_NUM[ch]
    return n


def _norm_bondfull(s):
    """归一化债券全称: 去空格/统一括号/统一债种词/统一期数写法。
    跨数据源(Excel用户维护 vs chinamoney抓取)对同一只债的 bondFull 通常完全一致,
    是比简称更权威的判重主键。"""
    s = str(s or "").strip().replace(" ", "")
    s = s.replace("（", "(").replace("）", ")")
    s = s.replace("资本补充债券", "资本补充债")
    s = s.replace("无固定期限资本债券", "永续债")
    s = re.sub(r"\(第([一二三四五六七八九十\d]+)期\)",
               lambda m: "(%d期)" % _cn2int(m.group(1)), s)
    return s


def _norm_short_core(s):
    """归一化债券简称核心: 先剥离债种词(资本补充债/永续债/无固定期限资本债券),
    再去末尾序号。'26横琴人寿01' 与 '26横琴人寿资本补充债01' 归一化后均为 '26横琴人寿'。"""
    s = str(s or "").strip()
    s = re.sub(r"资本补充债(券)?", "", s)
    s = re.sub(r"无固定期限资本债券", "", s)
    s = re.sub(r"永续债", "", s)
    s = re.sub(r"[0-9]{2}$", "", s)
    return s.strip()


def _date_gap(a, b):
    da, db = _norm_date(a), _norm_date(b)
    if da and db:
        return abs((da - db).days)
    return None


def _rec_match(a, b):
    """两条记录是否同源(同一只债的不同命名形态/不同数据源写法):

    主键1(最权威): 归一化 bondFull 相同 —— 跨数据源(Excel用户维护 vs chinamoney抓取)
        对同一只债的 bondFull 通常完全一致, 但简称可能省略债种词(如 '26横琴人寿01' vs
        '26横琴人寿资本补充债01'), 因此不可用简称做唯一主键。
    主键2(兜底, 仅当至少一侧无 bondFull 时): 债种相同 + 简称核心词相同(剥离债种词+序号)
        + 发行额相同 + 发行日差<=14天。多期发行(01/02/03 相隔数月)因日差>14天不被误判。
        仅当至少一侧 bondFull 缺失才走兜底, 避免全称明确不同的债被简称巧合误并。"""
    if not (_issuer_tokens(a.get("issuer")) & _issuer_tokens(b.get("issuer"))):
        return False
    fa, fb = _norm_bondfull(a.get("bondFull")), _norm_bondfull(b.get("bondFull"))
    if fa and fb and fa == fb:
        return True
    # 兜底: 仅当至少一侧 bondFull 缺失
    if not fa or not fb:
        if a.get("bondType") and a.get("bondType") == b.get("bondType"):
            ca, cb = _norm_short_core(a.get("bondShort")), _norm_short_core(b.get("bondShort"))
            if ca and ca == cb:
                amnt_a = float(a.get("planAmnt") or 0)
                amnt_b = float(b.get("planAmnt") or 0)
                if amnt_a and amnt_b and round(amnt_a, 1) != round(amnt_b, 1):
                    return False
                gap = _date_gap(a.get("issueDate"), b.get("issueDate"))
                if gap is not None and gap <= 14:
                    return True
    return False


def _is_preissue(rec):
    """是否'尚未发行'占位记录: 唯一的硬标记是 bondCode=='---'
    (发行前 chinamoney 尚无代码/票息/评级; 发行成功后会补全)。
    注意: bondCode 为空串 '' 不等于未发行 —— 很多已发行债只是脚本没抓到代码,
    但其票面/评级/金额都是真实值, 不应被整体接管。"""
    return rec.get("bondCode") == "---"


def _refresh(old, new):
    """重抓命中已有记录时, 用新抓到的真实数据刷新 old (同只债不同期/不同抓取时点):
    - 未发行占位(bondCode=='---'): 全面接管真实数据(发行额/代码/票息/评级/状态)。
    - 已发行但缺字段(如 bondCode 空串): 仅补缺, 绝不覆盖已有真实值, 避免 chinamoney
      偶发异常值污染已正确的金额/票息。"""
    pre = _is_preissue(old)
    for k, v in new.items():
        if v in (None, "", "---"):
            continue
        ov = old.get(k)
        if pre or ov in (None, "", "---"):
            old[k] = v
    return old


def dedup_bonds(bonds):
    """去除同源重复: 命中时合并两条(保留 bondCode 更全的为基, 补全另一条非空字段),
    而非简单替换, 以免丢失 Excel 维护的评级等字段。O(n^2), 数据量小无妨。"""
    out = []
    for b in bonds:
        hit_idx = None
        for i, x in enumerate(out):
            if _rec_match(b, x):
                hit_idx = i
                break
        if hit_idx is None:
            out.append(b)
            continue
        x = out[hit_idx]
        bc, xc = str(b.get("bondCode") or "").strip(), str(x.get("bondCode") or "").strip()
        if bc and (not xc or len(bc) > len(xc)):
            base, other = b, x
        else:
            base, other = x, b
        merged = dict(base)
        # 补全 base 缺失/占位的字段
        for k, v in other.items():
            if merged.get(k) in (None, "", "---") and v not in (None, "", "---"):
                merged[k] = v
        out[hit_idx] = merged
    return out


# ---------- 抓取 ----------

def fetch_list(bond_type: str, year: str, retries=5):
    """返回该类型该年份的全部债券摘要列表(含 查询代码)。带重试(限流时返回非JSON)。"""
    import akshare as ak
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            df = ak.bond_info_cm(bond_type=bond_type, issue_year=year)
            return df.to_dict("records")
        except Exception as e:  # noqa
            last_err = e
            time.sleep(2.0 * attempt)
    sys.stderr.write(f"  [warn] list fail {bond_type} {year}: {last_err}\n")
    return []


def fetch_detail(bond_defined_code: str, retries=4):
    """直接调 BondDetailInfo, 返回 bondBaseInfo dict; 失败返回 None。"""
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            r = requests.post(DETAIL_URL, data={"bondDefinedCode": bond_defined_code},
                              headers=DETAIL_HEADERS, timeout=30)
            j = r.json()
            info = j.get("data", {}).get("bondBaseInfo")
            if info:
                return info
        except Exception as e:  # noqa
            last_err = e
        time.sleep(1.5 * attempt)
    if last_err:
        sys.stderr.write(f"  [warn] detail fail {bond_defined_code}: {last_err}\n")
    return None


def build_record(list_row, info):
    enty = info.get("entyFullName") or list_row.get("发行人/受托机构") or ""
    bond_period = info.get("bondPeriod") or ""
    bt = info.get("bondType") or list_row.get("债券类型") or ""
    bond_type_norm = "永续债" if "无固定期限" in bt else ("资本补充债" if "资本补充" in bt else bt)
    value_date = _norm_date(info.get("frstValueDate"))
    mrty_date = _norm_date(info.get("mrtyDate"))
    call_date = derive_call_date(value_date, bond_period)
    exercise_flag = info.get("exerciseInfoFlag") or "否"
    # 主体/债项评级: creditRateEntyList[0].creditSubjectRating 形如 "AA+/AAA"
    rating_str = ""
    crl = info.get("creditRateEntyList") or []
    if crl and isinstance(crl, list):
        rating_str = crl[0].get("creditSubjectRating") or ""
    status = compute_status(value_date, mrty_date, call_date, exercise_flag,
                             is_perpetual=(bond_type_norm == "永续债"))
    issue_date = _norm_date(info.get("issueDate")) or _norm_date(list_row.get("发行日期"))
    # 期限显示归一化: 资本补充债统一 "5+5年"。
    # 数据源两种写法: Excel 来源录 "10年", chinamoney 详情返回 "5+5年", 同一品种应一致。
    # (derive_call_date 需在归一化前用原始 '5+5' 模式取 '+' 年数, 故放其后)
    if bond_type_norm == "资本补充债" and bond_period.strip() == "10年":
        bond_period = "5+5年"
    rec = {
        "bondDefinedCode": info.get("bondDefinedCode") or list_row.get("查询代码"),
        "issuer": enty,
        "bondShort": info.get("bondName") or list_row.get("债券简称"),
        "bondFull": info.get("bondFullName") or "",
        "bondCode": info.get("bondCode") or list_row.get("债券代码"),
        "bondType": bond_type_norm,
        "industry": infer_industry(enty),
        "issueDate": issue_date.isoformat() if issue_date else (list_row.get("发行日期") or ""),
        "valueDate": value_date.isoformat() if value_date else "",
        "mrtyDate": mrty_date.isoformat() if mrty_date else "",
        "bondPeriod": bond_period,
        "planAmnt": _to_float(info.get("plndIssueAmnt")),
        "issueAmnt": _to_float(info.get("issueAmnt")),
        "couponRate": _to_float(info.get("parCouponRate")),
        "couponType": info.get("couponType") or "",
        "couponFrqncy": info.get("couponFrqncy") or "",
        "debtRating": list_row.get("最新债项评级") or "",
        "ratingStr": rating_str,
        "exerciseFlag": exercise_flag,
        "callDate": call_date.isoformat() if call_date else "",
        "status": status,
    }
    return rec


# ---------- 赎回公告(重大事项-行权公告)状态同步 ----------
# 来源: 中国货币网 披露 -> 债券信息披露 -> 重大事项 -> 行使公告
# 接口: POST /ags/ms/cm-u-notice-issue/majorMatters
#       (与行情列表同一 /ags/ms 网关, 纯 requests 可直连, 无需浏览器)
# 规则(用户确认 2026-09-08):
#   标题含"不行使/放弃/不予行使(赎回选择权)" -> 存续(可回退错误的已赎回)
#   标题含"行使"且非"不行使"               -> 已赎回
#   绝不按 callDate 推断赎回; 公告状态可更新 Excel(用户维护) 记录的 status
#   (字段仍受保护, 仅状态以官方公告为准)。
# 窗口: 接口只保留近3年滚动(实测 minDate≈today-3年), 回填用全窗口, 周频增量默认21天。

NOTICE_URL = "https://www.chinamoney.com.cn/ags/ms/cm-u-notice-issue/majorMatters"
NOTICE_HEADERS = {
    **HEADERS,
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "referer": "https://www.chinamoney.com.cn/chinese/zdsx/",
}


def fetch_redemption_notices(days_back=None, sleep=1.0):
    """拉取重大事项-行权公告(关键词'保险'), 返回赎回类公告列表。
    days_back=None -> 接口全窗口(约3年, 用于首次回填); 否则只看最近 N 天(周频增量)。"""
    records, page = [], 1
    while True:
        form = {
            "eventCode": "",
            "drftClAngl": "1001",
            "bondNameOrCode": "保险",
            "pageNo": str(page),
            "pageSize": "100",
            "startDate": (date.today() - timedelta(days=days_back)).isoformat() if days_back else "",
            "endDate": "",
        }
        # 该接口限流极严: 实测单 IP 约 20 分钟只放行 1 个请求(其余 403),
        # 退避重试覆盖一个完整冷却周期; 仍失败则本轮放弃(周频任务下轮再补)。
        d = None
        for wait in (0, 60, 300, 900):
            if wait:
                print(f"[notices] 被限流, 等待 {wait}s 后重试...", flush=True)
                time.sleep(wait)
            try:
                r = requests.post(NOTICE_URL, data=form, headers=NOTICE_HEADERS, timeout=30)
            except requests.RequestException as e:
                sys.stderr.write(f"  [warn] notices 请求异常: {e}\n")
                continue
            if r.status_code == 200:
                d = r.json()
                break
        if d is None:
            sys.stderr.write("  [warn] 赎回公告接口持续限流, 本轮跳过(下轮再补)\n")
            break
        recs = d.get("records") or []
        records.extend(recs)
        total = int((d.get("data") or {}).get("total") or 0)
        if not recs or len(records) >= total or page >= 60:
            break
        page += 1
        time.sleep(1200)  # 翻页前强制冷却(限流约20分钟/请求); 周频任务总量<100条通常单页即可
    out = []
    for rec in records:
        title = (rec.get("title") or "").strip()
        if rec.get("prefix") == "行权公告" and "赎回" in title:
            out.append({"title": title, "date": rec.get("releaseDate") or "",
                        "contentId": rec.get("contentId")})
    return out


def _title_bondname(title):
    """从公告标题截取债券全称段(去掉前缀'关于', 掐掉'赎回/行使/兑付/摘牌'及之后), 归一化。
    注意'不行使赎回...'会在'行使'处被切断留下尾巴'不', 需去尾。"""
    t = str(title or "").strip()
    m = re.match(r"^关于(.+)$", t)
    if m:
        t = m.group(1)
    head = re.split(r"赎回|行使|兑付|摘牌", t)[0]
    head = re.sub(r"[不拟]+$", "", head)
    return _norm_bondfull(head)


def _notice_new_status(title):
    """按标题判定公告含义: 不行使->存续 / 行使->已赎回 / 其他->None。"""
    if "不行使" in title or "不予行使" in title or ("放弃" in title and "赎回" in title):
        return "存续"
    if "行使" in title:
        return "已赎回"
    return None


def apply_notice_status(bonds, notices, verbose=True):
    """按公告标题更新 bonds 的 status(行使->已赎回 / 不行使->存续)。
    匹配优先级:
      强: 公告标题债券名段(归一化) 与 bondFull(归一化) 相互包含
      弱: 发行人命中 + 标题年份 == 发行年份
    候选不唯一(如同年多期且标题缺期数)时不动作, 宁缺毋滥。
    注: Excel(用户维护) 记录仅保护字段不被覆写, 官方赎回公告的状态判定必须生效
    (否则 Excel 来源的存续债永远等不到赎回状态)。返回 (changed, skipped)。"""
    changed, skipped = [], []
    for n in notices:
        st = _notice_new_status(n["title"])
        if st is None:
            continue
        tname = _title_bondname(n["title"])
        m_year = re.search(r"(20\d\d)", n["title"])
        year = m_year.group(1) if m_year else ""
        strong, weak = [], []
        for b in bonds:
            if not any(tok and tok in n["title"] for tok in _issuer_tokens(b.get("issuer"))):
                continue
            nf = _norm_bondfull(b.get("bondFull"))
            if nf and (nf in tname or tname.endswith(nf)):
                strong.append(b)
            elif year and (b.get("issueDate") or "")[:4] == year:
                weak.append(b)
        cands = strong if strong else weak
        if len(cands) != 1:
            skipped.append((n["title"][:44], f"{len(cands)}只候选" if cands else "无匹配债"))
            continue
        b = cands[0]
        if b.get("status") != st:
            old = b.get("status")
            b["status"] = st
            changed.append(((b.get("bondShort") or b.get("bondFull") or "?")[:24],
                            old, st, f"{n['date']} {n['title'][:40]}"))
        elif verbose:
            skipped.append((n["title"][:44], f"已是{st}"))
    return changed, skipped


def _sync_notices(seen_list, notice_days, sleep, verbose=True):
    """抓赎回公告并应用到债券列表。notice_days=0 -> 全窗口回填。返回变更数。"""
    nd = notice_days or None
    notices = fetch_redemption_notices(days_back=nd, sleep=sleep)
    print(f"[notices] 赎回类行权公告 {len(notices)} 条 (窗口={'全量约3年' if nd is None else f'{nd}天'})",
          flush=True)
    changed, skipped = apply_notice_status(seen_list, notices, verbose=verbose)
    for short, old, new, why in changed:
        print(f"  [notice-upd] {short:<24} {old} -> {new} | {why}", flush=True)
    for title, why in skipped:
        print(f"  [notice-skip] {title} ({why})", flush=True)
    print(f"[notices] 状态变更 {len(changed)} 条", flush=True)
    return len(changed)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--year", default=None, help="只抓某年(调试)")
    ap.add_argument("--type", default=None, help="只抓某类型(资本补充债/永续债)，合并进已有 json")
    ap.add_argument("--sleep", type=float, default=0.6, help="详情请求间隔(秒)")
    ap.add_argument("--year-sleep", type=float, default=1.5, help="每年列表请求间隔(秒)")
    ap.add_argument("--dedup-only", action="store_true",
                    help="不抓取, 仅对已有 ins_bonds.json 跑稳健去重并重写(用于清历史重复)")
    ap.add_argument("--notices-only", action="store_true",
                    help="不抓行情, 仅抓赎回公告(重大事项-行权公告)并按行使/不行使更新状态")
    ap.add_argument("--notice-days", type=int, default=0,
                    help="赎回公告回看天数; 0=接口全窗口(约3年, 总量<100条单页即拉全)。默认0")
    ap.add_argument("--no-notices", action="store_true", help="跳过赎回公告状态同步")
    args = ap.parse_args()

    # ---- 仅赎回公告模式: 载入已有 json, 抓公告更新状态后写回 ----
    if args.notices_only:
        if not os.path.exists(DATA_FILE):
            sys.stderr.write("  [err] 不存在 ins_bonds.json\n")
            return
        prev = json.load(open(DATA_FILE, encoding="utf-8"))
        seen_list = dedup_bonds(prev.get("bonds", []))
        _sync_notices(seen_list, args.notice_days, args.sleep)
        bonds = dedup_bonds(seen_list)
        bonds.sort(key=lambda r: r.get("issueDate") or "", reverse=True)
        prev["bonds"] = bonds
        prev["count"] = len(bonds)
        prev["generatedAt"] = date.today().isoformat()
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(prev, f, ensure_ascii=False, indent=1)
        from collections import Counter
        print("[done] 按状态:", dict(Counter(b["status"] for b in bonds)), flush=True)
        return

    # ---- 仅去重模式: 直接复用 dedup_bonds, 不改网络抓取 ----
    if args.dedup_only:
        if not os.path.exists(DATA_FILE):
            sys.stderr.write("  [err] 不存在 ins_bonds.json\n")
            return
        prev = json.load(open(DATA_FILE, encoding="utf-8"))
        before = len(prev.get("bonds", []))
        cleaned = dedup_bonds(prev.get("bonds", []))
        prev["bonds"] = cleaned
        prev["count"] = len(cleaned)
        prev["generatedAt"] = date.today().isoformat()
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(prev, f, ensure_ascii=False, indent=1)
        print(f"[dedup-only] {before} -> {len(cleaned)} 只 (删除 {before - len(cleaned)} 条重复)")
        return

    types = [args.type] if args.type else BOND_TYPES
    years = [args.year] if args.year else [str(y) for y in range(START_YEAR, date.today().year + 1)]

    # 始终以已有 json 为基底: 保留 Excel 来源的永续债/资本补充债, chinamoney 仅补充缺失的新发行债。
    # 载入即先跑一次稳健去重, 清掉历史遗留的同源重复(改名/带序号简称两种命名形态)。
    seen_list = []
    if os.path.exists(DATA_FILE):
        try:
            prev = json.load(open(DATA_FILE, encoding="utf-8"))
            seen_list = dedup_bonds(prev.get("bonds", []))
            print(f"[merge] 载入并去重后 {len(seen_list)} 只", flush=True)
        except Exception as e:  # noqa
            sys.stderr.write(f"  [warn] load prev fail: {e}\n")

    # 赎回公告状态同步放在重度抓取之前: 公告接口限流严格,
    # 先用干净 IP 完成(仅1~8个请求), 再慢慢爬新债列表。
    if not args.no_notices:
        try:
            _sync_notices(seen_list, args.notice_days, args.sleep)
        except Exception as e:  # noqa
            sys.stderr.write(f"  [warn] 赎回公告同步失败(不影响新债抓取): {e}\n")

    for bt in types:
        print(f"[info] 类型={bt}", flush=True)
        for yr in years:
            rows = fetch_list(bt, yr)
            time.sleep(args.year_sleep)
            if not rows:
                continue
            for row in rows:
                issuer = row.get("发行人/受托机构") or ""
                if not is_insurance(issuer):
                    continue
                code = row.get("查询代码")
                if not code:
                    continue
                # 构造候选记录, 用稳健匹配判断是否已存在(同源不同命名形态也算存在)
                probe = {
                    "issuer": issuer,
                    "bondShort": row.get("债券简称") or "",
                    "planAmnt": None,
                    "issueDate": row.get("发行日期") or "",
                    "bondCode": "",
                }
                pre_hit = next((x for x in seen_list if _rec_match(probe, x)), None)
                if pre_hit is not None:
                    # 已有债: 仍查详情, 但只用来刷新 status (其余字段 _refresh '仅补缺' 原则不变)
                    info = fetch_detail(code)
                    time.sleep(args.sleep)
                    if info:
                        action, payload = _maybe_update_status_from_detail(pre_hit, info)
                        if action == "updated":
                            old, new, why = payload
                            print(f"  [status-upd] {pre_hit.get('bondShort','?')[:18]:<18} "
                                  f"{old} -> {new} ({why})", flush=True)
                    continue
                info = fetch_detail(code)
                time.sleep(args.sleep)
                if not info:
                    # 详情缺失也保留列表级最小记录, 标记 status 未知
                    rec = build_record(row, {
                        "bondDefinedCode": code,
                        "entyFullName": issuer,
                        "bondName": row.get("债券简称"),
                        "bondFullName": "",
                        "bondCode": row.get("债券代码"),
                        "bondType": bt,
                        "issueDate": row.get("发行日期"),
                        "frstValueDate": "",
                        "mrtyDate": "",
                        "bondPeriod": "",
                        "plndIssueAmnt": None,
                        "issueAmnt": None,
                        "parCouponRate": None,
                        "couponType": "",
                        "couponFrqncy": "",
                        "exerciseInfoFlag": "否",
                        "creditRateEntyList": [],
                    })
                    rec["status"] = "存续"  # 无到期日信息时保守视为存续
                else:
                    rec = build_record(row, info)
                # 再次稳健匹配(此时有完整 issueDate/额):
                # 命中=同只债已存在 -> 用新抓到的真实数据刷新(发行成功后补全金额/代码/票息/评级),
                # 而非丢弃; 未命中才作为新债追加。
                hit = next((x for x in seen_list if _rec_match(rec, x)), None)
                if hit is None:
                    seen_list.append(rec)
                else:
                    _refresh(hit, rec)
            print(f"  {yr}: 累计 {len(seen_list)} 只", flush=True)
        time.sleep(3)

    bonds = dedup_bonds(seen_list)  # 末步安全网
    bonds.sort(key=lambda r: r.get("issueDate") or "", reverse=True)

    out = {
        "generatedAt": date.today().isoformat(),
        "source": "中国货币网(chinamoney) 债券信息综合查询",
        "count": len(bonds),
        "bonds": bonds,
    }
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"[done] 写出 {len(bonds)} 只 -> {DATA_FILE}", flush=True)

    # 简要统计
    from collections import Counter
    by_type = Counter(b["bondType"] for b in bonds)
    by_status = Counter(b["status"] for b in bonds)
    by_ind = Counter(b["industry"] for b in bonds)
    print("  按类型:", dict(by_type))
    print("  按状态:", dict(by_status))
    print("  按行业:", dict(by_ind))


if __name__ == "__main__":
    main()
