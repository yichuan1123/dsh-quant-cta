"""报告输出：Markdown（人读）+ JSON（机器读）+ SVG（净值曲线）。

SVG 用标准库手写，不引 matplotlib —— 无头环境里中文字体经常缺字形，
手写 SVG 只画线与坐标轴，稳定且体积极小。
"""

import json
import os

from .metrics import attribution_by_sleeve, attribution_by_symbol


def _p(v):
    return "—" if v is None else ("%.2f%%" % v)


def _money(v):
    return "—" if v is None else ("{:,.0f}".format(v))


def _num(v, nd=2):
    return "—" if v is None else ("%.*f" % (nd, v))


def render_markdown(result, meta=None):
    m = result.metrics
    meta = meta or {}
    L = []
    a = L.append

    a("# 量化 CTA 回测报告")
    a("")
    src = meta.get("data_source", "unknown")
    if src in ("synthetic", "mixed"):
        a("> ⚠️ **数据来源含合成数据（synthetic）**。本次结果只能验证流程与成本模型，**不可作为真实行情结论**。")
        a("")

    a("| 项目 | 值 |")
    a("|---|---|")
    a("| 数据来源 | %s |" % src)
    a("| 品种池 | %s |" % ", ".join(sorted(result.bundle.bars_by_code.keys())))
    a("| 区间 | %s → %s（%d 个交易日） |" % (m.get("start"), m.get("end"), m.get("trading_days", 0)))
    a("| 初始资金 | %s |" % _money(m.get("initial_capital")))
    a("| 目标年化波动 | %s |" % _p((result.cfg.target_vol or 0) * 100))
    a("| 滑点假设 | %s 跳/笔 |" % _num(result.cfg.slip_ticks, 2))
    a("| 策略腿 | %s |" % ", ".join("%s(%.0f%%)" % (s.name, s.alloc * 100) for s in result.sleeves))
    a("")

    a("## 核心指标")
    a("")
    a("| 指标 | 值 | 指标 | 值 |")
    a("|---|---|---|---|")
    a("| 期末权益 | %s | 总收益 | %s |" % (_money(m.get("final_equity")), _p(m.get("total_return_pct"))))
    a("| 年化收益 (CAGR) | %s | 年化波动 | %s |" % (_p(m.get("cagr_pct")), _p(m.get("ann_vol_pct"))))
    a("| 夏普 | %s | 索提诺 | %s |" % (_num(m.get("sharpe"), 3), _num(m.get("sortino"), 3)))
    a("| 最大回撤 | %s | 回撤日 | %s |" % (_p(m.get("max_drawdown_pct")), m.get("max_drawdown_date")))
    a("| 卡玛 | %s | 平均敞口 | %s |" % (_num(m.get("calmar"), 3), _p(m.get("avg_exposure_pct"))))
    a("| 交易次数 | %s | 胜率 | %s |" % (m.get("trades"), _p(m.get("win_rate_pct"))))
    a("| 盈亏比(PF) | %s | 单笔期望 | %s 元 |" % (_num(m.get("profit_factor"), 3), _money(m.get("expectancy"))))
    a("| 平均持仓 | %s 天 | 年换手 | %s 倍 |" % (_num(m.get("avg_holding_days"), 1), _num(m.get("turnover_annualized"), 2)))
    a("")

    a("## 成本分解")
    a("")
    a("| 项目 | 金额 | 占初始资金 |")
    a("|---|---|---|")
    a("| 手续费 | %s | — |" % _money(m.get("commission_total")))
    a("| 滑点 | %s | — |" % _money(m.get("slippage_total")))
    a("| 合计 | %s | %s |" % (_money(m.get("cost_total")), _p(m.get("cost_pct_initial"))))
    a("")
    a("> 成本是策略的重力。毛收益若在 5% 以下，扣完成本基本归零。")
    a("")

    mh = getattr(result, "model_history", None) or []
    if mh:
        latest = mh[-1]
        d = latest.get("diagnostics", {})
        a("## 最优配比（滚动重估）")
        a("")
        a("| 项目 | 值 |")
        a("|---|---|")
        a("| 方法 | %s |" % latest.get("method"))
        a("| 重估次数 | %d |" % len(mh))
        a("| 最近重估日 | %s |" % latest.get("asof"))
        a("| 品种数 / 有效品种数 | %s / %s |" % (d.get("n"), d.get("effective_n")))
        a("| 组合年化波动 | %s |" % _p(d.get("ann_vol_pct")))
        a("| 观测天数 / 单品种上限 | %s / %s |" % (d.get("observations"), _p((d.get("max_weight_cap") or 0) * 100)))
        a("")
        w = latest.get("weights", {})
        rc = d.get("risk_contributions", {})
        ranked = sorted(w.items(), key=lambda kv: -kv[1])[:12]
        a("| 品种 | 权重 | 风险贡献 |")
        a("|---|---|---|")
        for code, weight in ranked:
            a("| %s | %s | %s |" % (code, _p(weight * 100), _p(rc.get(code))))
        if len(w) > 12:
            a("| …(其余 %d 个) | %s | — |" % (len(w) - 12, _p(sum(v for _, v in sorted(w.items(), key=lambda kv: -kv[1])[12:]) * 100)))
        if d.get("sector_totals_pct"):
            a("")
            a("板块权重：" + "　".join("%s %s" % (k, _p(v)) for k, v in d["sector_totals_pct"].items()))
        if len(mh) > 1:
            first = mh[0].get("weights", {})
            codes = set(first) | set(w)
            drift = sorted(((abs(w.get(c, 0) - first.get(c, 0)), c) for c in codes), reverse=True)[:5]
            a("")
            a("权重漂移最大的品种（首次重估 → 最近）：")
            for delta, code in drift:
                a("  %s：%s → %s（Δ %s）" % (code, _p(first.get(code, 0) * 100),
                                           _p(w.get(code, 0) * 100), _p(delta * 100)))
        a("")

    a("## 策略腿归因")
    a("")
    a("| 策略腿 | 策略 | 资金占比 | 期末权益 | 收益 | 夏普 | 最大回撤 | 交易数 |")
    a("|---|---|---|---|---|---|---|---|")
    for name, row in attribution_by_sleeve(result.sleeves).items():
        a("| %s | %s | %s | %s | %s | %s | %s | %s |" % (
            name, row["strategy"], _p(row["alloc_pct"]), _money(row["final"]),
            _p(row["return_pct"]), _num(row["sharpe"], 2), _p(row["max_drawdown_pct"]), row["trades"]))
    a("")

    a("## 品种归因")
    a("")
    a("| 品种 | 交易数 | 胜率 | 净盈亏(元) |")
    a("|---|---|---|---|")
    for code, row in attribution_by_symbol(result.trades).items():
        a("| %s | %d | %s | %s |" % (code, row["trades"], _p(row["win_rate_pct"]), _money(row["pnl"])))
    a("")

    a("## 分年度收益")
    a("")
    a("| 年份 | 收益 |")
    a("|---|---|")
    for y, v in m.get("yearly_returns_pct", {}).items():
        a("| %s | %s |" % (y, _p(v)))
    a("")

    a("## 月度收益矩阵")
    a("")
    mr = m.get("monthly_returns_pct", {})
    years = sorted({k[:4] for k in mr})
    a("| 年 | " + " | ".join("%02d月" % i for i in range(1, 13)) + " | 当年 |")
    a("|" + "---|" * 14)
    for y in years:
        cells = []
        for mo in range(1, 13):
            k = "%s-%02d" % (y, mo)
            cells.append(_p(mr.get(k)) if k in mr else "")
        a("| " + y + " | " + " | ".join(cells) + " | " + _p(m.get("yearly_returns_pct", {}).get(y)) + " |")
    a("")

    a("## 口径说明")
    a("")
    a("- 信号在 **t 日收盘** 产生，**t+1 日开盘** 成交；不使用当日收盘成交（防未来函数）")
    a("- 手数按 ATR 波动率预算换算，组合目标年化波动 %s" % _p((result.cfg.target_vol or 0) * 100))
    a("- 滑点 %s 跳/笔，手续费按合约规格表（近似值，以交易所公告为准）" % _num(result.cfg.slip_ticks, 2))
    a("- 单品种风险上限 %s，名义杠杆上限 %s 倍" % (_p(result.cfg.max_symbol_risk * 100), _num(result.cfg.max_leverage, 2)))
    a("- 回撤熔断：组合回撤达 %s 后停止开新仓" % _p(result.cfg.max_drawdown_halt * 100))
    a("- 期末强制平仓，使成交与统计闭合")
    a("")
    a("---")
    a("")
    a("**免责**：历史回测不代表未来收益；本工具不构成投资建议，也不产生任何真实委托。")
    a("")
    return "\n".join(L)


def render_svg(result, width=960, height=360):
    curve = result.aggregate
    if not curve:
        return "<svg xmlns='http://www.w3.org/2000/svg' width='%d' height='%d'></svg>" % (width, height)
    pad_l, pad_r, pad_t, pad_b = 66, 18, 24, 34
    w = width - pad_l - pad_r
    h = height - pad_t - pad_b
    eq = [r.equity for r in curve]
    lo, hi = min(eq), max(eq)
    if hi - lo < 1e-9:
        hi = lo + 1.0

    def x(i):
        return pad_l + (w * i / max(len(eq) - 1, 1))

    def y(v):
        return pad_t + h - (h * (v - lo) / (hi - lo))

    pts = " ".join("%.2f,%.2f" % (x(i), y(v)) for i, v in enumerate(eq))

    dd = [r.drawdown for r in curve]
    dmax = max(dd) if dd else 0.0
    def ydd(v):
        return pad_t + h - (h * (v / dmax) if dmax > 1e-9 else 0.0)
    dpts = " ".join("%.2f,%.2f" % (x(i), ydd(v)) for i, v in enumerate(dd))

    parts = []
    for k in range(5):
        v = lo + (hi - lo) * k / 4
        yy = y(v)
        parts.append("<line x1='%d' y1='%.1f' x2='%d' y2='%.1f' stroke='#e8e8ee' stroke-width='1'/>" % (pad_l, yy, pad_l + w, yy))
        parts.append("<text x='%d' y='%.1f' font-size='10' fill='#8a8a99' text-anchor='end'>%s</text>" % (pad_l - 8, yy + 3, "{:,.0f}".format(v)))
    n = len(curve)
    for k in range(5):
        i = int((n - 1) * k / 4)
        parts.append("<text x='%.1f' y='%d' font-size='10' fill='#8a8a99' text-anchor='middle'>%s</text>" % (x(i), height - 12, curve[i].date))

    return (
        "<svg xmlns='http://www.w3.org/2000/svg' width='%d' height='%d' viewBox='0 0 %d %d'>" % (width, height, width, height)
        + "<rect width='100%' height='100%' fill='#ffffff'/>"
        + "".join(parts)
        + "<polyline fill='none' stroke='#2f6feb' stroke-width='1.6' points='%s'/>" % pts
        + "<polyline fill='none' stroke='#e05b5b' stroke-width='1' stroke-dasharray='3 2' points='%s'/>" % dpts
        + "<text x='%d' y='%d' font-size='11' fill='#2f6feb' text-anchor='start'>权益（蓝线） / 回撤（红虚线）</text>" % (pad_l, pad_t - 8)
        + "</svg>"
    )


def write_reports(out_dir, result, meta=None):
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "report.md"), "w", encoding="utf-8") as fh:
        fh.write(render_markdown(result, meta))
    with open(os.path.join(out_dir, "report.svg"), "w", encoding="utf-8") as fh:
        fh.write(render_svg(result))
    payload = {
        "meta": meta or {},
        "config": {
            "start_date": result.cfg.start_date,
            "end_date": result.cfg.end_date,
            "initial_capital": result.cfg.initial_capital,
            "target_vol": result.cfg.target_vol,
            "atr_period": result.cfg.atr_period,
            "slip_ticks": result.cfg.slip_ticks,
            "max_leverage": result.cfg.max_leverage,
            "max_drawdown_halt": result.cfg.max_drawdown_halt,
            "max_symbol_risk": result.cfg.max_symbol_risk,
            "sleeves": [{"name": s.name, "strategy": s.strategy.name, "alloc": s.alloc} for s in result.sleeves],
            "sizing": getattr(result.cfg, "sizing", "vol_parity"),
            "model_method": getattr(result.cfg, "model_method", None),
            "model_rebalance": getattr(result.cfg, "model_rebalance", None),
        },
        "model": (
            {
                "rebalances": len(result.model_history),
                "latest": result.model_history[-1],
                "series": [
                    {"version": s.get("version"), "asof": s.get("asof"), "weights": s.get("weights", {})}
                    for s in result.model_history[-40:]
                ],
            }
            if getattr(result, "model_history", None) else None
        ),
        "metrics": result.metrics,
        "sleeves": attribution_by_sleeve(result.sleeves),
        "symbols": attribution_by_symbol(result.trades),
        "curve": [{"date": r.date, "equity": round(r.equity, 2), "drawdown": round(r.drawdown, 5)} for r in result.aggregate],
        "trades": [
            {
                "symbol": t.symbol, "sleeve": t.sleeve, "direction": t.direction,
                "open_date": t.open_date, "close_date": t.close_date,
                "open_price": t.open_price, "close_price": t.close_price,
                "pnl": round(t.net_pnl, 2),
            }
            for t in result.trades
        ],
        "rejections": [r for s in result.sleeves for r in s.account.rejections],
    }
    with open(os.path.join(out_dir, "report.json"), "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    return out_dir
