"""对一份回测报告做恒等式核验（不碰网络、不依赖 pandas）。

用法：python tools/verify_invariants.py <run_dir|report.json>

三条恒等式，任何一条不成立都说明账本口径出了问题：
  1. 期末权益 - 初始资金 == Σ 所有平仓交易的净盈亏
  2. Σ 分品种净盈亏 == Σ 交易净盈亏
  3. Σ 分策略腿期末权益 == 总期末权益
"""

import json
import math
import os
import sys


def load(path):
    if os.path.isdir(path):
        path = os.path.join(path, "report.json")
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def main(argv):
    if len(argv) < 2:
        print("用法: python tools/verify_invariants.py <run_dir|report.json>")
        return 2
    data = load(argv[1])
    m = data.get("metrics", {})
    if not m:
        print("FAIL: 报告里没有 metrics")
        return 1

    failures = []
    tol = lambda v: max(0.5, abs(v) * 1e-6)

    bad = [k for k, v in m.items() if isinstance(v, float) and (math.isnan(v) or math.isinf(v))]
    if bad:
        failures.append("指标含 NaN/Inf: " + ", ".join(bad))

    trades = data.get("trades", [])
    pnl = sum(t["pnl"] for t in trades)
    delta = m["final_equity"] - m["initial_capital"]
    print("① 期末-初始 = %.2f ；Σ交易净盈亏 = %.2f ；差 %.4f" % (delta, pnl, delta - pnl))
    if abs(delta - pnl) > tol(pnl):
        failures.append("恒等式①不成立（差 %.4f）" % (delta - pnl))

    syms = sum(v["pnl"] for v in data.get("symbols", {}).values())
    print("② Σ分品种 = %.2f ；Σ交易 = %.2f ；差 %.4f" % (syms, pnl, syms - pnl))
    if abs(syms - pnl) > tol(pnl):
        failures.append("恒等式②不成立（差 %.4f）" % (syms - pnl))

    sf = sum(v["final"] for v in data.get("sleeves", {}).values())
    tf = m["final_equity"]
    print("③ Σ分腿期末 = %.2f ；总期末 = %.2f ；差 %.4f" % (sf, tf, tf - sf))
    if abs(sf - tf) > tol(tf):
        failures.append("恒等式③不成立（差 %.4f）" % (tf - sf))

    print("区间 %s → %s ；交易 %d 笔 ；CAGR %.2f%% ；夏普 %.2f ；回撤 %.2f%%" % (
        m.get("start"), m.get("end"), m.get("trades", 0), m.get("cagr_pct", 0),
        m.get("sharpe", 0), m.get("max_drawdown_pct", 0)))

    if failures:
        print("\nFAIL:")
        for f in failures:
            print("  - " + f)
        return 1
    print("\nOK 三条恒等式全部成立")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
