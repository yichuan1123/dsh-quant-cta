"""绩效指标。全部纯标准库实现，公式写在注释里，方便逐条核对。

约定：
- 年化一律用 252 个交易日
- 夏普的波动用日收益标准差 × sqrt(252)
- 最大回撤用日频权益的峰值回撤（不是日内）
"""

import datetime as dt
import math

TRADING_DAYS = 252


def _pct(x):
    return float(x) * 100.0


def _safe_div(a, b, default=0.0):
    return a / b if b else default


def _mean(xs):
    return sum(xs) / len(xs) if xs else 0.0


def _std(xs):
    if len(xs) < 2:
        return 0.0
    m = _mean(xs)
    var = sum((x - m) ** 2 for x in xs) / (len(xs) - 1)
    return math.sqrt(max(var, 0.0))


def _downside_std(xs, target=0.0):
    ds = [min(0.0, x - target) for x in xs]
    if len(ds) < 2:
        return 0.0
    var = sum(d * d for d in ds) / (len(ds) - 1)
    return math.sqrt(max(var, 0.0))


def daily_returns(equity_series):
    out = []
    for i in range(1, len(equity_series)):
        prev = equity_series[i - 1]
        if prev > 0:
            out.append(equity_series[i] / prev - 1.0)
    return out


def compute_metrics(curve, trades, initial_capital, rf=0.0, fills=None):
    if not curve:
        return {"error": "没有权益曲线"}

    dates = [r.date for r in curve]
    eq = [r.equity for r in curve]
    rets = daily_returns(eq)
    n = len(curve)
    years = n / TRADING_DAYS

    final = eq[-1]
    total_return = _safe_div(final, initial_capital) - 1.0 if initial_capital > 0 else 0.0
    cagr = 0.0
    if initial_capital > 0 and final > 0 and years > 0:
        cagr = (final / initial_capital) ** (1.0 / years) - 1.0

    vol = _std(rets) * math.sqrt(TRADING_DAYS)
    sharpe = _safe_div(_mean(rets) - rf / TRADING_DAYS, _std(rets)) * math.sqrt(TRADING_DAYS)
    sortino = _safe_div(_mean(rets) - rf / TRADING_DAYS, _downside_std(rets)) * math.sqrt(TRADING_DAYS)

    peak = eq[0]
    max_dd = 0.0
    max_dd_date = dates[0]
    for d, e in zip(dates, eq):
        peak = max(peak, e)
        dd = 0.0 if peak <= 0 else (peak - e) / peak
        if dd > max_dd:
            max_dd = dd
            max_dd_date = d

    calmar = _safe_div(cagr, max_dd)

    monthly = {}
    yearly = {}
    for d, e in zip(dates, eq):
        ym = d[:7]
        y = d[:4]
        monthly.setdefault(ym, [None, e])
        monthly[ym][1] = e
        yearly.setdefault(y, [None, e])
        yearly[y][1] = e

    # 月度/年度收益用上一期期末权益做基准（首期用初始资金）
    monthly_ret = {}
    prev_end = initial_capital
    for k in sorted(monthly.keys()):
        end_eq = monthly[k][1]
        monthly_ret[k] = _safe_div(end_eq, prev_end) - 1.0
        prev_end = end_eq

    yearly_ret = {}
    prev_end = initial_capital
    for k in sorted(yearly.keys()):
        end_eq = yearly[k][1]
        yearly_ret[k] = _safe_div(end_eq, prev_end) - 1.0
        prev_end = end_eq

    wins = [t.net_pnl for t in trades if t.net_pnl > 0]
    losses = [t.net_pnl for t in trades if t.net_pnl < 0]
    holding = []
    for t in trades:
        try:
            d0 = dt.date.fromisoformat(t.open_date)
            d1 = dt.date.fromisoformat(t.close_date)
            holding.append((d1 - d0).days)
        except Exception:
            pass

    fills = fills or []
    commission = sum(f.commission for f in fills)
    slippage = sum(f.slippage_cost for f in fills)
    notional = sum(abs(f.lots * f.price) for f in fills)
    avg_eq = _mean(eq)

    exposure = _mean([_safe_div(r.gross_exposure, r.equity) for r in curve if r.equity > 0])
    halted_days = sum(1 for r in curve if getattr(r, "halted", False))

    return {
        "start": dates[0],
        "end": dates[-1],
        "trading_days": n,
        "years": round(years, 3),
        "initial_capital": round(initial_capital, 2),
        "final_equity": round(final, 2),
        "total_return_pct": round(_pct(total_return), 3),
        "cagr_pct": round(_pct(cagr), 3),
        "ann_vol_pct": round(_pct(vol), 3),
        "sharpe": round(sharpe, 3),
        "sortino": round(sortino, 3),
        "max_drawdown_pct": round(_pct(max_dd), 3),
        "max_drawdown_date": max_dd_date,
        "calmar": round(calmar, 3),
        "best_day_pct": round(_pct(max(rets) if rets else 0.0), 3),
        "worst_day_pct": round(_pct(min(rets) if rets else 0.0), 3),
        "monthly_returns_pct": {k: round(_pct(v), 3) for k, v in monthly_ret.items()},
        "yearly_returns_pct": {k: round(_pct(v), 3) for k, v in yearly_ret.items()},
        "trades": len(trades),
        "win_rate_pct": round(_safe_div(len(wins), len(wins) + len(losses)) * 100.0, 2),
        "avg_win": round(_mean(wins), 2),
        "avg_loss": round(_mean(losses), 2),
        "profit_factor": round(_safe_div(sum(wins), abs(sum(losses))), 3),
        "expectancy": round(_mean([t.net_pnl for t in trades]), 2),
        "avg_holding_days": round(_mean(holding), 1),
        "commission_total": round(commission, 2),
        "slippage_total": round(slippage, 2),
        "cost_total": round(commission + slippage, 2),
        "cost_pct_initial": round(_safe_div(commission + slippage, initial_capital) * 100.0, 3),
        "avg_exposure_pct": round(_pct(exposure), 2),
        "halted_days": halted_days,
        "turnover_annualized": round(_safe_div(notional, avg_eq * max(years, 1e-9)), 3),
    }


def attribution_by_symbol(trades):
    out = {}
    for t in trades:
        row = out.setdefault(t.symbol, {"trades": 0, "pnl": 0.0, "wins": 0})
        row["trades"] += 1
        row["pnl"] += t.net_pnl
        if t.net_pnl > 0:
            row["wins"] += 1
    for k, v in out.items():
        v["pnl"] = round(v["pnl"], 2)
        v["win_rate_pct"] = round(_safe_div(v["wins"], v["trades"]) * 100.0, 1)
    return dict(sorted(out.items(), key=lambda kv: -kv[1]["pnl"]))


def attribution_by_sleeve(sleeves):
    out = {}
    for s in sleeves:
        curve = s.account.curve
        if not curve:
            continue
        init = s.capital
        final = curve[-1].equity
        rets = daily_returns([r.equity for r in curve])
        peak = curve[0].equity
        mdd = 0.0
        for r in curve:
            peak = max(peak, r.equity)
            mdd = max(mdd, 0.0 if peak <= 0 else (peak - r.equity) / peak)
        out[s.name] = {
            "strategy": s.strategy.name,
            "alloc_pct": round(s.alloc * 100.0, 2),
            "initial": round(init, 2),
            "final": round(final, 2),
            "return_pct": round((_safe_div(final, init) - 1.0) * 100.0, 3) if init else 0.0,
            "sharpe": round(_safe_div(_mean(rets), _std(rets)) * math.sqrt(TRADING_DAYS), 3),
            "max_drawdown_pct": round(mdd * 100.0, 3),
            "trades": len(s.account.trades),
            "commission": round(sum(f.commission for f in s.account.fills), 2),
        }
    return out
