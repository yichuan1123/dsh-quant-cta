"""参数 / 资金 / 目标波动的网格扫描（真实数据，in-sample 与 out-of-sample 分开看）。

用法：
  python tools/sweep.py --pool all --start 2021-01-01 --split 2024-01-01
  python tools/sweep.py --grid capital --out .preview/sweep_capital.md

为什么分开算 IS / OOS：同一段数据上调参数必然越调越好，那不是"找到了规律"，
是"记住了答案"。这里固定用前段选、后段验，任何只在 IS 上好看的行都会在 OOS 露馅。

产出：markdown 表 + CSV，字段含「波动利用率」= 实际年化波动 / 目标波动，
它就是「仓位有没有吃满」的直接度量（手数取整、品种相关性都会压低它）。
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "python"))

from qcta.data import load_bundle          # noqa: E402
from qcta.engine import Backtester          # noqa: E402
from qcta.metrics import compute_metrics    # noqa: E402
from qcta.specs import resolve_pool         # noqa: E402
from qcta.types import EngineConfig         # noqa: E402


def run_once(bundle, codes, capital, vol, entry, exit_, lookback, rebalance, top_frac,
             start, end, sizing="optimizer", method="risk_parity", slip=1.0, recycle=False):
    cfg = EngineConfig(
        start_date=start, end_date=end, initial_capital=capital, target_vol=vol,
        atr_period=20, slip_ticks=slip, max_leverage=3.0, max_drawdown_halt=0.99,
        max_symbol_risk=0.35, sizing=sizing, model_method=method, model_lookback=250,
        model_min_bars=250, model_rebalance=20, model_max_weight=0.15, model_sector_cap=0.35,
        recycle_budget=recycle,
        sleeves=(
            {"name": "trend", "strategy": "trend", "alloc": 0.6,
             "kwargs": {"entry": entry, "exit": exit_}},
            {"name": "xsec", "strategy": "xsec", "alloc": 0.4,
             "kwargs": {"lookback": lookback, "rebalance": rebalance, "top_frac": top_frac}},
        ),
    )
    res = Backtester(cfg, bundle).run()
    m = compute_metrics(res.aggregate, res.trades, capital, 0.0, res.fills)
    m["vol_util"] = round((m["ann_vol_pct"] / 100.0) / vol, 3) if vol else 0.0
    m["avg_exposure_pct"] = m.get("avg_exposure_pct", 0.0)
    return m


def row(label, m):
    return {
        "config": label,
        "cagr_pct": m["cagr_pct"], "ann_vol_pct": m["ann_vol_pct"], "vol_util": m["vol_util"],
        "sharpe": m["sharpe"], "maxdd_pct": m["max_drawdown_pct"], "calmar": m["calmar"],
        "trades": m["trades"], "pf": m["profit_factor"], "win_pct": m["win_rate_pct"],
        "exposure_pct": m["avg_exposure_pct"], "cost_pct": m["cost_pct_initial"],
    }


def fmt_table(rows, title):
    head = "| 配置 | 年化 | 波动 | 波动利用 | 夏普 | 回撤 | 卡玛 | 交易 | PF | 胜率 | 平均敞口 | 成本% |"
    sep = "|---|---|---|---|---|---|---|---|---|---|---|---|"
    out = ["### " + title, "", head, sep]
    for r in rows:
        out.append("| %s | %+.2f%% | %.2f%% | %.0f%% | %.2f | %.2f%% | %.2f | %d | %.2f | %.1f%% | %.1f%% | %.2f |" % (
            r["config"], r["cagr_pct"], r["ann_vol_pct"], r["vol_util"] * 100, r["sharpe"],
            r["maxdd_pct"], r["calmar"], r["trades"], r["pf"], r["win_pct"],
            r["exposure_pct"], r["cost_pct"]))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--pool", default="all")
    ap.add_argument("--start", default="2021-01-01")
    ap.add_argument("--split", default="2024-01-01", help="OOS 起始日")
    ap.add_argument("--end", default="")
    ap.add_argument("--capital", type=float, default=1_000_000.0)
    ap.add_argument("--grid", default="all", choices=["channel", "capital", "vol", "all"])
    ap.add_argument("--recycle", action="store_true", help="开启预算回收（仓位吃满口径）")
    ap.add_argument("--out", default=".preview/sweep.md")
    ap.add_argument("--csv", default=".preview/sweep.csv")
    args = ap.parse_args(argv)

    codes = resolve_pool(args.pool)
    bundle = load_bundle(codes, args.start, args.end, prefer_live=False)
    print("数据来源:", bundle.source, "| 品种:", len(codes), flush=True)
    if bundle.source == "synthetic":
        print("!! 拒绝在合成数据上做参数探索（那只是在拟合我写的生成器）", flush=True)
        return 2

    periods = [("IS %s→%s" % (args.start, args.split), args.start, args.split),
               ("OOS %s→今" % args.split, args.split, args.end or "")]

    blocks = []
    all_rows = []

    if args.grid in ("channel", "all"):
        base = dict(capital=args.capital, vol=0.10, lookback=60, rebalance=20, top_frac=0.3)
        for entry, exit_ in [(20, 10), (40, 20), (55, 20), (60, 30), (100, 50)]:
            label = "通道 %d/%d" % (entry, exit_)
            for pname, ps, pe in periods:
                m = run_once(bundle, codes, base["capital"], base["vol"], entry, exit_,
                             base["lookback"], base["rebalance"], base["top_frac"], ps, pe,
                             recycle=args.recycle)
                r = row(label, m)
                r["period"] = pname
                all_rows.append(r)
        print("channel grid done", flush=True)

    if args.grid in ("capital", "all"):
        # 用第一条网格里最稳的通道；这里先用 40/20 作为中性选择
        entry, exit_ = (40, 20)
        for cap in (1_000_000, 3_000_000, 10_000_000):
            label = "资金 %.0f万" % (cap / 10000)
            for pname, ps, pe in periods:
                m = run_once(bundle, codes, cap, 0.10, entry, exit_, 60, 20, 0.3, ps, pe)
                r = row(label, m)
                r["period"] = pname
                all_rows.append(r)
        print("capital grid done", flush=True)

    if args.grid in ("vol", "all"):
        entry, exit_ = (40, 20)
        for vol in (0.10, 0.20, 0.35, 0.50):
            label = "目标波动 %.0f%%" % (vol * 100)
            for pname, ps, pe in periods:
                m = run_once(bundle, codes, args.capital, vol, entry, exit_, 60, 20, 0.3, ps, pe)
                r = row(label, m)
                r["period"] = pname
                all_rows.append(r)
        print("vol grid done", flush=True)

    # 输出
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    md = ["# 参数 / 资金 / 目标波动 扫描", "",
          "- 数据来源：%s（%d 个品种）" % (bundle.source, len(codes)),
          "- IS：%s → %s ；OOS：%s → 今" % (args.start, args.split, args.split),
          "- 波动利用 = 实际年化波动 / 目标波动；低于 50% 说明仓位被手数取整吃掉",
          ""]
    for pname, _, _ in periods:
        sub = [r for r in all_rows if r["period"] == pname]
        if sub:
            md.extend(fmt_table(sub, pname))
            md.append("")
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write("\n".join(md))

    keys = ["period", "config", "cagr_pct", "ann_vol_pct", "vol_util", "sharpe",
            "maxdd_pct", "calmar", "trades", "pf", "win_pct", "exposure_pct", "cost_pct"]
    with open(args.csv, "w", encoding="utf-8") as fh:
        fh.write(",".join(keys) + "\n")
        for r in all_rows:
            fh.write(",".join(str(r.get(k, "")) for k in keys) + "\n")

    print("\n".join(md))
    print("\n已写：%s / %s" % (args.out, args.csv))
    return 0


if __name__ == "__main__":
    sys.exit(main())
