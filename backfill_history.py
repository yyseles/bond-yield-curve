#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
历史数据回填脚本（2008 → 现有最早日期，倒序）
复用 ci_update.py 的抓取/解析/重试逻辑，按交易日逐日向前补，定期原子落盘（断点续传）。

覆盖 4 条曲线：
  data.json        - 国债即期 (csz=1)；MA750/MA60 不再下发，由前端用即期 rows 现场计算
  data_gov_ytm     - 国债到期 (searchYc GOV qxll=0)
  data_cdb         - 国开即期 (searchYc CDB qxll=1)
  data_cdb_ytm     - 国开到期 (searchYc CDB qxll=0)

注意：早期（约 2008-2011）中债曲线最长仅到 30Y，40Y/50Y 会为 null（前端按缺失处理）。
"""
import os
import sys
import time
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ci_update as C

START = "2008-01-01"
SLEEP = 0.15  # 请求间隔，避免触发限流


def _save_spot(file, dtr):
    sd = sorted(dtr.keys())
    C.save_json(file, {
        "dates": sd, "terms": C.ALL_TERMS,
        "rows": [dtr[x] for x in sd],
    })


def backfill_gov_spot():
    print("\n" + "=" * 50)
    print("  [国债即期] 历史回填 (spot only, MA 二期补)")
    print("=" * 50)
    existing = C.load_existing(C.DATA_FILE)
    if not existing["dates"]:
        print("  无现有数据，跳过"); return
    first = existing["dates"][0]
    print(f"  当前最早 {first} → 向 {START} 回填")
    start = datetime.strptime(START, "%Y-%m-%d")
    d = datetime.strptime(first, "%Y-%m-%d") - timedelta(days=1)
    dtr = {existing["dates"][i]: existing["rows"][i] for i in range(len(existing["dates"]))}
    fetched = skipped = done = 0
    while d >= start:
        ds = d.strftime("%Y-%m-%d")
        if d.weekday() < 5:
            rates = C.fetch_spot_rates_chinabond(ds, csz="1")
            if rates:
                dtr[ds] = [rates.get(t) for t in C.ALL_TERMS]
                fetched += 1
            else:
                skipped += 1
            done += 1
            if done % 100 == 0:
                _save_spot(C.DATA_FILE, dtr)
                print(f"  ...{ds} 抓 {fetched} / 跳 {skipped}")
            time.sleep(SLEEP)
        d -= timedelta(days=1)
    _save_spot(C.DATA_FILE, dtr)
    print(f"  ✅ 国债即期 回填 +{fetched}, 跳过 {skipped}")


def backfill_searchyc(name, curve_id, qxll, file):
    print("\n" + "=" * 50)
    print(f"  [{name}] 历史回填")
    print("=" * 50)
    existing = C.load_existing(file)
    if not existing["dates"]:
        print("  无现有数据，跳过"); return
    first = existing["dates"][0]
    print(f"  当前最早 {first} → 向 {START} 回填")
    start = datetime.strptime(START, "%Y-%m-%d")
    d = datetime.strptime(first, "%Y-%m-%d") - timedelta(days=1)
    dtr = {existing["dates"][i]: existing["rows"][i] for i in range(len(existing["dates"]))}
    fetched = skipped = done = 0
    while d >= start:
        ds = d.strftime("%Y-%m-%d")
        if d.weekday() < 5:
            rates = C.fetch_searchyc_rates(curve_id, qxll, ds, name)
            if rates:
                dtr[ds] = [rates.get(t) for t in C.ALL_TERMS]
                fetched += 1
            else:
                skipped += 1
            done += 1
            if done % 100 == 0:
                sd = sorted(dtr.keys())
                C.save_json(file, {"dates": sd, "terms": C.ALL_TERMS, "rows": [dtr[x] for x in sd]})
                print(f"  ...{ds} 抓 {fetched} / 跳 {skipped}")
            time.sleep(SLEEP)
        d -= timedelta(days=1)
    sd = sorted(dtr.keys())
    C.save_json(file, {"dates": sd, "terms": C.ALL_TERMS, "rows": [dtr[x] for x in sd]})
    print(f"  ✅ {name} 回填 +{fetched}, 跳过 {skipped}")


def acquire_lock():
    """防多实例：磁盘文件锁（O_EXCL 原子建锁）。磁盘文件跨沙箱会话共享、纯文件 I/O，
    不依赖 powershell（沙箱禁止子进程调 powershell）也不依赖内核互斥体（沙箱会话隔离致 Global 互斥体不互通）。
    首个实例建锁并持有，后续实例 O_EXCL 必失败 -> 直接退出；仅当锁超过 3h 才当陈旧清理后重试（处理被强杀的情况）。"""
    import atexit, time
    lock = os.path.join(os.path.dirname(os.path.abspath(__file__)), "backfill_history.lock")
    for attempt in range(40):
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_RDWR)
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
            break
        except FileExistsError:
            # 锁已存在：判断锁中记录的那個 pid 是否仍存活（os.kill(pid,0)：存活无异常，死亡抛 OSError(87)）
            dead = False
            try:
                old_pid = int(open(lock).read().strip())
                try:
                    os.kill(old_pid, 0)
                except (ProcessLookupError, FileNotFoundError, OSError):
                    dead = True
            except Exception:
                dead = True  # 读不了锁 -> 当陈旧处理
            if dead:
                try:
                    os.remove(lock)
                except Exception:
                    pass
                time.sleep(0.3)
                continue
            print("  ⚠ 已有回填进程在运行（锁文件存在），本实例自动退出避免抢写")
            sys.exit(0)
    atexit.register(lambda: os.path.exists(lock) and os.remove(lock))


if __name__ == "__main__":
    acquire_lock()
    t0 = time.time()
    backfill_gov_spot()
    backfill_searchyc("国债到期", C.GOV_CURVE_ID, "0", C.GOV_YTM_FILE)
    backfill_searchyc("国开即期", C.CDB_CURVE_ID, "1", C.CDB_DATA_FILE)
    backfill_searchyc("国开到期", C.CDB_CURVE_ID, "0", C.CDB_YTM_FILE)
    print(f"\n🎉 全部回填完成，耗时 {(time.time()-t0)/60:.1f} 分钟")
