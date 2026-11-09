"""风险度量：VaR / CVaR / 回撤结构 / GARCH 波动率。

为什么不用「样本标准差 × 1.65」了事：
- 收益是**肥尾**的，正态假设会系统性低估尾部风险
- 波动率是**聚集**的（GARCH 效应），用全体样本的常数波动率会漏掉"现在正处在高波动期"

于是这里给三档 VaR（历史模拟 / 正态 / Cornish-Fisher 修正）与 GARCH(1,1) 的极大似然拟合。
"""

import math

from .probability import (mean, norm_ppf, quantile, skewness, std,
                          variance)

TRADING_DAYS = 252


# ───────────────────────────── VaR / CVaR ─────────────────────────────

def historical_var(returns, level=0.95):
    """历史模拟 VaR：直接取分位点。返回正数表示损失。"""
    if not returns:
        return 0.0
    q = quantile(returns, 1.0 - level)
    return max(0.0, -q)


def historical_cvar(returns, level=0.95):
    """条件 VaR（期望损失）：尾部均值。"""
    if not returns:
        return 0.0
    q = quantile(returns, 1.0 - level)
    tail = [r for r in returns if r <= q]
    if not tail:
        return max(0.0, -q)
    return max(0.0, -mean(tail))


def parametric_var(returns, level=0.95):
    """正态假设下的 VaR：-(μ + z_{1-level}·σ)。"""
    if len(returns) < 3:
        return 0.0
    z = norm_ppf(1.0 - level)
    return max(0.0, -(mean(returns) + z * std(returns)))


def cornish_fisher_var(returns, level=0.95):
    """Cornish-Fisher 修正：用偏度/峰度校正正态分位数。

    z_cf = z + (z²-1)γ₃/6 + (z³-3z)γ₄/24 - (2z³-5z)γ₃²/36
    负偏（γ₃<0）时 z_cf 更负 → VaR 更大（更保守），这正是我们要的。
    """
    if len(returns) < 4:
        return parametric_var(returns, level)
    z = norm_ppf(1.0 - level)
    g3 = skewness(returns)
    g4 = kurtosis_excess(returns)
    z_cf = (z + (z * z - 1) * g3 / 6.0 + (z ** 3 - 3 * z) * g4 / 24.0
            - (2 * z ** 3 - 5 * z) * g3 * g3 / 36.0)
    return max(0.0, -(mean(returns) + z_cf * std(returns)))


def kurtosis_excess(xs):
    from .probability import kurtosis

    return kurtosis(xs)


def var_report(returns, level=0.95):
    """三档 VaR + 两档 CVaR，一并给出以便对照。"""
    return {
        "level": level,
        "historical": historical_var(returns, level),
        "parametric": parametric_var(returns, level),
        "cornish_fisher": cornish_fisher_var(returns, level),
        "cvar_historical": historical_cvar(returns, level),
        "skew": skewness(returns) if len(returns) > 3 else 0.0,
        "kurtosis": kurtosis_excess(returns) if len(returns) > 3 else 0.0,
        "n": len(returns),
    }


# ───────────────────────────── 回撤结构 ─────────────────────────────

def drawdown_episodes(equity):
    """回撤事件列表：峰 → 谷 → 是否已收复，含幅度与持续天数。"""
    out = []
    if not equity:
        return out
    peak = equity[0]
    peak_i = 0
    trough = None
    trough_i = None
    for i, v in enumerate(equity):
        if v >= peak:
            if trough is not None:
                out.append({
                    "peak_index": peak_i, "trough_index": trough_i, "recover_index": i,
                    "depth": (peak - trough) / peak if peak > 0 else 0.0,
                    "drawdown_days": trough_i - peak_i,
                    "recover_days": i - trough_i,
                    "recovered": True,
                })
                trough = None
                trough_i = None
            peak = v
            peak_i = i
        else:
            if trough is None or v < trough:
                trough = v
                trough_i = i
    if trough is not None:
        out.append({
            "peak_index": peak_i, "trough_index": trough_i, "recover_index": None,
            "depth": (peak - trough) / peak if peak > 0 else 0.0,
            "drawdown_days": trough_i - peak_i,
            "recover_days": None,
            "recovered": False,
        })
    return out


def max_drawdown(equity):
    eps = drawdown_episodes(equity)
    return max((e["depth"] for e in eps), default=0.0)


def underwater_stats(equity):
    """水下时间统计：最长水下天数、平均回撤深度、回撤次数。"""
    eps = drawdown_episodes(equity)
    if not eps:
        return {"episodes": 0, "max_depth": 0.0, "avg_depth": 0.0,
                "longest_underwater_days": 0, "unrecovered": 0}
    longest = max((e["drawdown_days"] + (e["recover_days"] or 0)) for e in eps)
    return {
        "episodes": len(eps),
        "max_depth": max(e["depth"] for e in eps),
        "avg_depth": mean([e["depth"] for e in eps]),
        "longest_underwater_days": longest,
        "unrecovered": sum(1 for e in eps if not e["recovered"]),
    }


# ───────────────────────────── GARCH(1,1) ─────────────────────────────

def _garch_filter(r, omega, alpha, beta, init_var):
    var = init_var
    loglik = 0.0
    for x in r:
        if var <= 0:
            return -1e18, 0.0
        loglik += -0.5 * (math.log(var) + x * x / var)
        var = omega + alpha * x * x + beta * var
    return loglik, var


def garch11_fit(returns, grid=24):
    """GARCH(1,1) 极大似然（方差目标化，只在 α/β 上搜）。

    Var_t = ω + α·r²_{t-1} + β·Var_{t-1}，其中 ω = σ²(1-α-β) 保证无条件方差回到样本方差。
    返回参数、持续性、长期波动与**下一期条件波动**（这才是"现在的风险"）。
    """
    r = [x - mean(returns) for x in returns]
    n = len(r)
    if n < 50:
        return {"ok": False, "reason": "样本不足 50 个"}
    sample_var = variance(r)
    if sample_var <= 0:
        return {"ok": False, "reason": "收益方差为 0"}

    best = None
    for i in range(1, grid + 1):
        alpha = 0.30 * i / grid
        for j in range(1, grid + 1):
            beta = 0.30 + 0.68 * j / grid
            if alpha + beta >= 0.999:
                continue
            omega = sample_var * (1.0 - alpha - beta)
            ll, last_var = _garch_filter(r, omega, alpha, beta, sample_var)
            if best is None or ll > best[0]:
                best = (ll, alpha, beta, omega, last_var)

    if best is None:
        return {"ok": False, "reason": "网格内没有可行参数"}
    ll, alpha, beta, omega, last_var = best
    persist = alpha + beta
    long_run = math.sqrt(omega / (1.0 - persist)) if persist < 1 else math.sqrt(sample_var)
    return {
        "ok": True,
        "omega": omega,
        "alpha": alpha,
        "beta": beta,
        "persistence": persist,
        "long_run_vol_ann": long_run * math.sqrt(TRADING_DAYS),
        "next_vol_ann": math.sqrt(max(last_var, 0.0)) * math.sqrt(TRADING_DAYS),
        "loglik": ll,
        "n": n,
        "sample_vol_ann": math.sqrt(sample_var) * math.sqrt(TRADING_DAYS),
    }
