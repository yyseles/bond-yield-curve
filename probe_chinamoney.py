# -*- coding: utf-8 -*-
"""chinamoney 连通性自检(CI 诊断用)。

逐个端点打 1 个请求, 不做退避重试, 结果写入 connectivity_report.json。
用途: 判断 GitHub Actions 的数据中心 IP 能通哪些 /ags/ms 端点。
"""
import json
import time

import requests

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/109.0.0.0 Safari/537.36")
H = {"User-Agent": UA, "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"}

TESTS = [
    ("新债列表 BondMarketInfoList2",
     "https://www.chinamoney.com.cn/ags/ms/cm-u-bond-md/BondMarketInfoList2",
     {"pageSize": "5", "pageIndex": "1", "bondType": "保险公司资本补充债", "year": "2026"},
     "https://www.chinamoney.com.cn/chinese/bond/"),
    ("行使公告 majorMatters",
     "https://www.chinamoney.com.cn/ags/ms/cm-u-notice-issue/majorMatters",
     {"eventCode": "", "drftClAngl": "1001", "bondNameOrCode": "保险",
      "pageNo": "1", "pageSize": "100", "startDate": "", "endDate": ""},
     "https://www.chinamoney.com.cn/chinese/zdsx/"),
    ("付息兑付 clinrAnNotice",
     "https://www.chinamoney.com.cn/ags/ms/cm-u-notice-issue/clinrAnNotice",
     {"channelId": "2562", "bondSrno": "", "drftClAngl": "20", "scnd": "2001,2002",
      "pageNo": "1", "pageSize": "100", "startDate": "", "endDate": "",
      "limit": "0", "timeln": "0"},
     "https://www.chinamoney.com.cn/chinese/fxdflm/"),
]


def main():
    rep = {"runAt": time.strftime("%Y-%m-%d %H:%M:%S"), "results": []}
    for name, url, form, referer in TESTS:
        item = {"name": name, "url": url}
        try:
            r = requests.post(url, data=form, headers={**H, "Referer": referer}, timeout=30)
            item["http"] = r.status_code
            body = r.text or ""
            item["bytes"] = len(body)
            item["head"] = body[:160]
            try:
                d = r.json()
                recs = d.get("records") or []
                item["records"] = len(recs)
                if recs:
                    item["sample"] = (recs[0].get("title") or recs[0].get("bondName")
                                      or str(recs[0])[:60])
            except Exception:
                pass
        except Exception as e:  # noqa
            item["error"] = str(e)[:200]
        rep["results"].append(item)
        print(f"{name}: http={item.get('http')} bytes={item.get('bytes')} "
              f"records={item.get('records')} {item.get('error', '')}", flush=True)
        time.sleep(3)
    with open("connectivity_report.json", "w", encoding="utf-8") as f:
        json.dump(rep, f, ensure_ascii=False, indent=1)
    print("written connectivity_report.json", flush=True)


if __name__ == "__main__":
    main()
