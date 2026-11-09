"""算每个品种的真实滑点与流动性，写 data/costs.json。

用法：
    python tools/costs.py                 # 只用日线（成交额 + 振幅代理）
    python tools/costs.py --live          # 额外拉 1 分钟线估价差（更准，需要快期账号）
    python tools/costs.py --write         # 写入 data/costs.json
"""

import argparse
import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "python"))

from qcta import costs  # noqa: E402
from qcta.data import fetch_tqsdk_series, load_cache  # noqa: E402
from qcta.specs import SPECS, resolve_pool  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--pool", default="all")
    ap.add_argument("--live", action="store_true", help="拉 1 分钟线估价差")
    ap.add_argument("--min-amount", type=float, default=5e8,
                    help="低于这个日均成交额（元）标为流动性不足，默认 5 亿")
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    codes = resolve_pool(args.pool)
    bars_by_code = {}
    for code in codes:
        bars = load_cache(code)
        if bars:
            bars_by_code[code] = bars
    if not bars_by_code:
        print("没有缓存数据，先跑 qcta data sync --live")
        return 2

    minutes = {}
    if args.live:
        from qcta.specs import get_spec
        # 品种代码 → 天勤主连符号，两边的键都对得上才可能命中
        sym_of = {c: "KQ.m@" + get_spec(c).tq_symbol().split("@")[-1] for c in bars_by_code}
        rev = {v: k for k, v in sym_of.items()}
        print("拉 %d 个品种的 1 分钟线…" % len(sym_of), file=sys.stderr, flush=True)
        got = fetch_tqsdk_series(list(sym_of.values()), period_seconds=60, data_length=8000, adj=None)
        for sym, res in got.items():
            code = rev.get(sym)
            if code and res.get("bars"):
                minutes[code] = res["bars"]
        print("分钟数据到手 %d/%d 个品种" % (len(minutes), len(sym_of)), file=sys.stderr, flush=True)

    table = costs.build_table(bars_by_code, SPECS, minutes, min_amount=args.min_amount)
    if args.json:
        print(json.dumps(table, ensure_ascii=False, indent=1))
    else:
        head = "| 品种 | 滑点(跳) | 方法 | 价差代理(跳) | 日均成交额 | 振幅(跳) | 每跳价值 |"
        print(head)
        print("|---|---|---|---|---|---|---|")
        for code in sorted(table, key=lambda c: -table[c]["median_amount"]):
            r = table[code]
            flag = " ⚠︎" if r.get("illiquid") else ""
            print("| %s%s | %.0f | %s | %s | %.1f 亿 | %.1f | %.1f 元 |" % (
                code, flag, r["slip_ticks"], r["method"],
                ("%.2f" % r["spread_ticks"]) if r["spread_ticks"] else "—",
                r["median_amount"] / 1e8, r["median_range_ticks"], r["tick_value"]))
        ill = [c for c in table if table[c].get("illiquid")]
        print("\n流动性不足（日均成交额 < %.0f 亿）：%s" % (
            args.min_amount / 1e8, ", ".join(sorted(ill)) or "无"))

    if args.write:
        p = costs.save_costs(table, note="由 tools/costs.py 生成，方法见 python/qcta/costs.py 顶部")
        print("\n已写入 %s" % p, file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
