#!/usr/bin/env python3
"""测试从本地能否成功请求中债 searchYc 接口抓取国开债即期利率"""
import json
import urllib.parse
import urllib.request

ENDPOINT = "https://yield.chinabond.com.cn/cbweb-mn/yc/searchYc"
REFERER = "https://yield.chinabond.com.cn/cbweb-mn/yield_main?locale=zh_CN"
CDB_ID = "8a8b2ca037a7ca910137bfaa94fa5057"

params = {
    "xyzSelect": "txy",
    "workTimes": "2026-07-11",
    "dxbj": "0",
    "qxll": "1",
    "yqqxN": "N",
    "yqqxK": "K",
    "ycDefIds": CDB_ID,
    "wrjxCBFlag": "0",
    "locale": "zh_CN",
}

body = urllib.parse.urlencode(params).encode("utf-8")
req = urllib.request.Request(ENDPOINT, data=body, headers={
    "User-Agent": "Mozilla/5.0",
    "Referer": REFERER,
    "Content-Type": "application/x-www-form-urlencoded",
})

print("1. 请求中债 searchYc 接口（国开债即期 2026-07-11）...")
try:
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    print(f"   HTTP 状态: 成功")
    print(f"   返回条数: {len(data)}")
    if data:
        d = data[0]
        print(f"   曲线名: {d.get('ycDefName')}")
        print(f"   日期: {d.get('worktime')}")
        sd = d.get("seriesData", [])
        print(f"   seriesData 点数: {len(sd)}")
        # 提取整数年限
        exact = {}
        for tenor, val in sd:
            if abs(tenor - round(tenor)) < 1e-6 and 1 <= tenor <= 50:
                exact[int(tenor)] = val
        print(f"   整数年限(1-50Y): {len(exact)} 个")
        print(f"   1Y={exact.get(1)}, 5Y={exact.get(5)}, 10Y={exact.get(10)}, 30Y={exact.get(30)}, 50Y={exact.get(50)}")
        print("\n2. 结论: ✓ 本地可成功抓取国开债即期利率")
    else:
        print("   返回空数组（可能非交易日）")
        print("\n2. 结论: ⚠ 接口可达但返回空")
except Exception as e:
    print(f"   失败: {e}")
    print(f"\n2. 结论: ✗ 无法抓取")
