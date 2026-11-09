"""对照实验：基线 / ADX 强度过滤 / 流动性池，同一份数据同一套口径。

    python tools/explore.py --grid adx        # ADX 阈值
    python tools/explore.py --grid pool       # 流动性池
    python tools/explore.py --grid all

每个配置都跑「全窗口」与「OOS（2021 起）」两段，并附统计体检的核心结论。
"""

import argparse
import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "python"))

from qcta import costs as cost_mod  # noqa: E402
from qcta.data import load_bundle  # noqa: E402
from qcta.engine import Backtester  # noqa: E402
from qcta.metrics import compute_metrics  # noqa: E402
from qcta.specs import get_spec, resolve_pool  # noqa: E402
from qcta.types import EngineConfig  # noqa: E402

START = "2016-01-05"
OOS = "2021-01-04"


DEFAULT_SLEEVES = (
    ("trend", "trend", 0.6, {}),
    ("xsec", "xsec", 0.4, {}),
)


def make_cfg(start, end, sleeves, slip_by_symbol, flat_slip=1.0):
    return EngineConfig(
        start_date=start, end_date=end, initial_capital=1_000_000.0, target_vol=0.10,
        atr_period=20, slip_ticks=flat_slip, slip_by_symbol=slip_by_symbol,
        max_leverage=1.5, max_drawdown_halt=0.20, max_symbol_risk=0.02,
        sleeves=tuple({"name": n, "strategy": s, "alloc": a, "kwargs": dict(kw)}
                      for n, s, a, kw in sleeves),
    )


def run_one(codes, bars_all, start, end, sleeves, slip_by_symbol, flat_slip=1.0):
    from qcta.data import DataBundle
    bars = {c: bars_all[c] for c in codes if c in bars_all}
    bundle = DataBundle({c: [b for b in v if (not start or b.date >= start) and (not end or b.date <= end)]
                         for c, v in bars.items()}, "tqsdk", [])
    res = Backtester(make_cfg(start, end, sleeves, slip_by_symbol, flat_slip), bundle).run()
    m = compute_metrics(res.aggregate, res.trades, 1_000_000.0, 0.0, res.fills)
    return m, [r.equity for r in res.aggregate], [r.date for r in res.aggregate], res.trades


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--grid", default="all", choices=["adx", "pool", "carry", "all"])
    ap.add_argument("--min-amount", type=float, default=130e8,
                    help="流动性门槛：日均成交额低于此值剔除（默认 130 亿）")
    ap.add_argument("--adx", default="0,20,25,30")
    ap.add_argument("--slip", default="auto", help="auto=用 data/costs.json 的逐品种滑点；数字=全局固定跳数")
    ap.add_argument("--out", default=".preview/explore.md")
    args = ap.parse_args(argv)

    all_codes = resolve_pool("all")
    bars_all = {}
    for c in all_codes:
        from qcta.data import load_cache
        bars = load_cache(c)
        if bars:
            bars_all[c] = bars
    table = cost_mod.load_costs()
    if args.slip == "auto":
        slip = cost_mod.slip_map(table) if table else {}
        flat_slip = 1.0
    else:
        slip = {}
        flat_slip = float(args.slip)

    liquid = [c for c in all_codes if not table.get(c, {}).get("illiquid")]
    by_amount = sorted(all_codes, key=lambda c: table.get(c, {}).get("median_amount", 0) or 0)
    liquid2 = [c for c in all_codes if (table.get(c, {}).get("median_amount", 0) or 0) >= args.min_amount]

    from qcta import verdict

    configs = []
    def with_trend(kw):
        return (("trend", "trend", 0.6, kw), ("xsec", "xsec", 0.4, {}))

    if args.grid in ("adx", "all"):
        for a in [float(x) for x in args.adx.split(",") if x.strip()]:
            configs.append(("ADX≥%g" % a if a else "基线 40/20", with_trend(dict(adx_min=a)), all_codes))
    if args.grid in ("pool", "all"):
        configs.append(("全池 %d" % len(all_codes), with_trend({}), all_codes))
        configs.append(("流动性池 %d（≥%.0f亿）" % (len(liquid2), args.min_amount / 1e8),
                        with_trend({}), liquid2))
    if args.grid in ("carry", "all"):
        configs.append(("趋势+动量（基线）", with_trend({}), all_codes))
        configs.append(("只有 carry", (("carry", "carry", 1.0, {"top_n": 3, "rebalance": 20}),), all_codes))
        configs.append(("趋势+动量+carry(50/30/20)",
                        (("trend", "trend", 0.5, {}), ("xsec", "xsec", 0.3, {}),
                         ("carry", "carry", 0.2, {"top_n": 3, "rebalance": 20})), all_codes))

    rows = []
    for label, kw, codes in configs:
        for pname, ps, pe in (("全窗口 2016→今", START, ""), ("OOS 2021→今", OOS, "")):
            m, eq, dates, trades = run_one(codes, bars_all, ps, pe, kw, slip, flat_slip)
            v = verdict.analyze(eq, dates, trades, n_trials=12)
            rows.append({
                "config": label, "period": pname, "symbols": len(codes),
                "cagr": m["cagr_pct"], "vol": m["ann_vol_pct"], "sharpe": m["sharpe"],
                "maxdd": m["max_drawdown_pct"], "trades": m["trades"],
                "pf": m["profit_factor"], "cost": m["cost_pct_initial"],
                "psr": round(v["sharpe"]["psr"] * 100, 1) if "sharpe" in v else None,
                "dsr": round(v["dsr"]["dsr"] * 100, 1) if "dsr" in v else None,
                "label": v.get("label") or v.get("error"),
            })
            print("done %-26s %-14s CAGR %+6.2f%% SR %5.2f" % (label, pname, m["cagr_pct"], m["sharpe"]),
                  file=sys.stderr, flush=True)

    md = ["# 对照实验", "",
          "- 数据：天勤真实主力连续（前复权），%s → 今" % START,
          "- 口径：100 万本金、目标波动 10%%、滑点按品种估（data/costs.json）",
          "- DSR 按「试过 12 组」计", ""]
    for pname in ("全窗口 2016→今", "OOS 2021→今"):
        sub = [r for r in rows if r["period"] == pname]
        if not sub:
            continue
        md += ["### " + pname, "",
               "| 配置 | 品种 | 年化 | 波动 | 夏普 | 回撤 | 交易 | PF | 成本% | PSR | DSR | 结论 |",
               "|---|---|---|---|---|---|---|---|---|---|---|---|"]
        for r in sub:
            md.append("| %s | %d | %+.2f%% | %.2f%% | %.2f | %.2f%% | %d | %.2f | %.2f | %s | %s | %s |" % (
                r["config"], r["symbols"], r["cagr"], r["vol"], r["sharpe"], r["maxdd"],
                r["trades"], r["pf"], r["cost"],
                ("%.1f%%" % r["psr"]) if r["psr"] is not None else "—",
                ("%.1f%%" % r["dsr"]) if r["dsr"] is not None else "—",
                r["label"]))
        md.append("")

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write("\n".join(md))
    with open(args.out.replace(".md", ".json"), "w", encoding="utf-8") as fh:
        json.dump(rows, fh, ensure_ascii=False, indent=1)
    print("\n".join(md))
    return 0


if __name__ == "__main__":
    sys.exit(main())
