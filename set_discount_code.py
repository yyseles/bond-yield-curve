#!/usr/bin/env python3
# 设置/更换折现率板块访问码。
# 用法：
#   python set_discount_code.py            # 随机生成强访问码
#   python set_discount_code.py 你的新码    # 指定访问码
# 计算 SHA-256(访问码 + 盐) 并写入 discount-loader.js 的两个常量。
import hashlib, secrets, sys, re, os

HERE = os.path.dirname(os.path.abspath(__file__))
LOADER = os.path.join(HERE, "discount-loader.js")

code = sys.argv[1] if len(sys.argv) > 1 else secrets.token_urlsafe(9)
salt = secrets.token_hex(8)
h = hashlib.sha256((code + salt).encode("utf-8")).hexdigest()

s = open(LOADER, encoding="utf-8").read()
s = re.sub(r'var DISCOUNT_CODE_HASH = "[^"]*";', 'var DISCOUNT_CODE_HASH = "%s";' % h, s)
s = re.sub(r'var DISCOUNT_SALT = "[^"]*";', 'var DISCOUNT_SALT = "%s";' % salt, s)
open(LOADER, "w", encoding="utf-8").write(s)

with open(os.path.join(HERE, ".discount_code.txt"), "w", encoding="utf-8") as f:
    f.write(code)

print("访问码已设置：%s" % code)
print("（请妥善保存；更改访问码请重跑：python set_discount_code.py 你的新码）")
