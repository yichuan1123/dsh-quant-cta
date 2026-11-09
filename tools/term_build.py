"""抓合约级日线，拼期限结构数据（carry 信号用）。

    python tools/term_build.py --codes rb --verbose     # 单个品种试跑
    python tools/term_build.py --pool liquid            # 流动性好的品种
    python tools/term_build.py --codes rb --dry-run     # 只列合约代码

**一次登录、持续订阅**：逐批重新登录在天勤上要几十秒一次，600 个合约会跑到天荒地老。
这里一次会话订阅到底，每个品种抓完立刻落盘（可随时中断重跑，已有的合约不会重复拉）。
"""

import argparse
import os
import sys
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "python"))

from qcta import carry, costs as cost_mod  # noqa: E402
from qcta.specs import get_spec, resolve_pool  # noqa: E402

LIQUID_MIN = 300e8     # 日均成交额门槛（期限结构只对流动性好的品种有意义）


def contract_symbols(code, years):
    spec = get_spec(code)
    # tq_symbol() 形如 "KQ.m@SHFE.rb"：取 @ 之后的交易所.品种
    ex, prod = spec.tq_symbol().split("@")[-1].split(".")
    out = []
    for y in years:
        for m in carry.months_for(code):
            if ex == "CZCE":
                out.append("%s.%s%d%02d" % (ex, prod, y % 10, m))
            else:
                out.append("%s.%s%02d%02d" % (ex, prod, y % 100, m))
    return out


def log(msg):
    sys.stderr.write(msg + "\n")
    sys.stderr.flush()


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--codes", default="")
    ap.add_argument("--pool", default="liquid", choices=["all", "liquid"])
    ap.add_argument("--from-year", dest="from_year", type=int, default=2016)
    ap.add_argument("--to-year", dest="to_year", type=int, default=2027)
    ap.add_argument("--bars", type=int, default=400)
    ap.add_argument("--wait", type=float, default=3.0, help="每批订阅后等行情的秒数")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    if args.codes:
        codes = [c.strip() for c in args.codes.split(",") if c.strip()]
    else:
        codes = resolve_pool("all")
        table = cost_mod.load_costs()
        if args.pool == "liquid" and table:
            codes = [c for c in codes if (table.get(c, {}).get("median_amount", 0) or 0) >= LIQUID_MIN]
        codes = [c for c in codes if carry.months_for(c)]

    years = range(args.from_year, args.to_year + 1)
    plan = {c: contract_symbols(c, years) for c in codes}
    log("品种 %d 个，合约 %d 个" % (len(plan), sum(len(v) for v in plan.values())))
    if args.dry_run:
        for c in codes[:3]:
            print(c, plan[c][:6], "...", plan[c][-3:])
        return 0

    from qcta.data import _serial_to_bars, load_credentials, open_api

    t0 = time.time()
    api = open_api(load_credentials())
    log("登录完成（%.1fs）" % (time.time() - t0))
    done = 0
    try:
        for code in codes:
            syms = plan[code]
            contracts = carry.load_term(code)
            todo = [s for s in syms if s not in contracts]
            if not todo:
                log("%-4s 已有 %d 个合约，跳过" % (code, len(contracts)))
                continue
            t1 = time.time()
            serials, failed = {}, 0
            for sym in todo:
                try:
                    serials[sym] = api.get_kline_serial(sym, 86400, data_length=args.bars, adj_type=None)
                except Exception:
                    failed += 1
            # 有界等待：历史通常随订阅一起回来，等太久没意义
            budget = time.time() + max(args.wait, 1.0)
            while time.time() < budget:
                try:
                    api.wait_update(deadline=time.time() + 0.5)
                except TypeError:
                    break
                except Exception:
                    break
                if serials and all(len(s) > 0 for s in serials.values()):
                    break
            added = 0
            for sym, serial in serials.items():
                bars = _serial_to_bars(serial)
                if len(bars) < 20:
                    continue
                contracts[sym] = {b.date: b.close for b in bars}
                added += 1
            carry.save_term(code, contracts, meta={"years": [args.from_year, args.to_year]})
            series = carry.carry_series(contracts)
            done += 1
            log("%-4s 合约 %d（新增 %d，失败 %d），carry %d 天，最后 %s，%.1fs" % (
                code, len(contracts), added, failed, len(series),
                (sorted(series)[-1] if series else "-"), time.time() - t1))
    finally:
        try:
            api.close()
        except Exception:
            pass
    log("完成 %d 个品种，总耗时 %.1fs" % (done, time.time() - t0))
    return 0


if __name__ == "__main__":
    sys.exit(main())
