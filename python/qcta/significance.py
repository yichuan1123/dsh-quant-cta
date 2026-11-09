"""统计显著性：这套策略的「夏普」到底是不是噪声。

量化里最贵的一课：一个年化 20%、夏普 1.5 的回测，可能只是「试了 200 个参数后
挑出来的最好那个」。这一层给三把尺子：

1. **PSR（概率性夏普）** — 给定样本量、偏度、峰度，「真实夏普 > 基准」的概率
2. **DSR（通缩夏普）** — 再扣掉「我试了多少次」的多重检验惩罚
3. **MinTRL（最小样本长度）** — 想以 95% 置信说「夏普 > 0」，至少要多少数据

外加：块自助法置信区间、单样本 t/Wilcoxon、Benjamini-Hochberg 多重检验校正。

公式出处（都在论文里可查）：
- Bailey & López de Prado (2012) *The Sharpe Ratio Efficient Frontier* — PSR / MinTRL
- Bailey & López de Prado (2014) *The Deflated Sharpe Ratio* — DSR
- Benjamini & Hochberg (1995) — FDR 控制
"""

import math
import random

from .probability import kurtosis, mean, norm_cdf, norm_ppf, skewness, std, t_two_sided_p
from .timeseries import block_bootstrap

TRADING_DAYS = 252
EULER = 0.5772156649015329


# ───────────────────────────── 基础 ─────────────────────────────

def daily_returns_from_equity(eq):
    out = []
    for i in range(1, len(eq)):
        if eq[i - 1] > 0:
            out.append(eq[i] / eq[i - 1] - 1.0)
    return out


def sharpe_per_period(returns, rf=0.0):
    """单期夏普（未年化）。PSR 系列公式要求用它，不能直接喂年化值。

    波动极小时返回 0：常数序列的标准差只是浮点噪声，除下去会得到 1e15 这种假夏普。
    """
    if not returns:
        return 0.0
    s = std(returns)
    if s <= 1e-15 * max(1.0, abs(mean(returns))):
        return 0.0
    return (mean(returns) - rf) / s


def annualized_sharpe(returns, rf=0.0, periods=TRADING_DAYS):
    return sharpe_per_period(returns, rf) * math.sqrt(periods)


def psr(returns, sr_benchmark=0.0):
    """概率性夏普：P(真实夏普 > sr_benchmark)。

    用的是**单期**夏普与样本矩校正：
        PSR = Φ( (SR - SR*)·√(n-1) / √(1 - γ₃·SR + ((γ₄-1)/4)·SR²) )
    分母 > 1 时（负偏、肥尾）同样的 SR 会得到更低的置信度——这正是它比裸 t 检验好的地方。
    """
    n = len(returns)
    if n < 3:
        return {"psr": 0.5, "sr": 0.0, "sr_ann": 0.0, "n": n, "skew": 0.0, "kurt": 0.0}
    sr = sharpe_per_period(returns)
    g3 = skewness(returns)
    g4 = kurtosis(returns)          # 超额峰度
    denom_sq = 1.0 - g3 * sr + (g4 - 1.0) / 4.0 * sr * sr
    denom = math.sqrt(max(denom_sq, 1e-12))
    z = (sr - sr_benchmark) * math.sqrt(n - 1) / denom
    return {
        "psr": norm_cdf(z),
        "sr": sr,
        "sr_ann": sr * math.sqrt(TRADING_DAYS),
        "n": n,
        "skew": g3,
        "kurt": g4,
        "z": z,
        "denom": denom,
    }


def expected_max_sharpe(n_trials, sr_variance):
    """N 次独立试验下，最大夏普的期望（Bailey & LdP 的极值近似）。

    SR* ≈ √V · [ (1-γ)Φ⁻¹(1-1/N) + γ·Φ⁻¹(1-1/(N·e)) ]，γ 为欧拉常数。
    """
    if n_trials is None or n_trials < 2 or sr_variance is None or sr_variance <= 0:
        return 0.0
    a = norm_ppf(1.0 - 1.0 / n_trials)
    b = norm_ppf(1.0 - 1.0 / (n_trials * math.e))
    return math.sqrt(sr_variance) * ((1.0 - EULER) * a + EULER * b)


def deflated_sharpe(returns, n_trials=1, sr_variance=None):
    """通缩夏普：把「试了多少次」折算成基准夏普，再算 PSR。

    n_trials=1 → 退化为 PSR(0)；试得越多，同样的夏普越不可信。
    sr_variance：各次试验夏普的方差（拿不到就给 1/n，即「一次试验的夏普标准误平方」）。
    """
    n = len(returns)
    if n < 3:
        return {"dsr": 0.5, "sr": 0.0, "sr_ann": 0.0, "sr_star": 0.0,
                "n_trials": n_trials, "n": n}
    sr = sharpe_per_period(returns)
    if sr_variance is None:
        sr_variance = 1.0 / max(n - 1, 1)     # 单次试验夏普的近似方差
    sr_star = expected_max_sharpe(n_trials, sr_variance)
    base = psr(returns, sr_benchmark=sr_star)
    out = dict(base)
    out.update({"dsr": base["psr"], "sr_star": sr_star, "n_trials": n_trials})
    return out


def min_track_record_length(returns, sr_target=0.0, confidence=0.95):
    """最小样本长度：要多大样本才能以 confidence 声称「夏普 > sr_target」。

    由 PSR 反解：n ≥ 1 + (1 - γ₃SR + ((γ₄-1)/4)SR²)·(z/SR)²
    """
    n = len(returns)
    if n < 3:
        return 0
    sr = sharpe_per_period(returns) - sr_target
    if sr <= 0:
        return float("inf")
    g3 = skewness(returns)
    g4 = kurtosis(returns)
    z = norm_ppf(confidence)
    factor = 1.0 - g3 * sr + (g4 - 1.0) / 4.0 * sr * sr
    return int(math.ceil(1.0 + factor * (z / sr) ** 2))


# ───────────────────────────── 置信区间 ─────────────────────────────

def bootstrap_sharpe_ci(returns, n_boot=400, level=0.95, seed=0, block=None):
    """块自助法给出**年化夏普**的置信区间（保留自相关）。"""
    if len(returns) < 20:
        return {"lo": 0.0, "hi": 0.0, "point": annualized_sharpe(returns), "n_boot": 0}
    samples = block_bootstrap(returns, n_boot, block=block, seed=seed)
    srs = sorted(annualized_sharpe(s) for s in samples)
    alpha = (1.0 - level) / 2.0
    lo = srs[int(alpha * (len(srs) - 1))]
    hi = srs[int((1.0 - alpha) * (len(srs) - 1))]
    return {"lo": lo, "hi": hi, "point": annualized_sharpe(returns),
            "n_boot": len(srs), "level": level}


def bootstrap_cagr_ci(equity, n_boot=400, level=0.95, seed=0):
    """块自助法给出**年化收益**的置信区间（基于日收益重采样再复利）。"""
    rets = daily_returns_from_equity(equity)
    if len(rets) < 20:
        return {"lo": 0.0, "hi": 0.0, "point": 0.0, "n_boot": 0}
    real_eq = 1.0
    for r in rets:
        real_eq *= 1.0 + r
    point = real_eq ** (TRADING_DAYS / len(rets)) - 1.0

    samples = block_bootstrap(rets, n_boot, seed=seed)
    cagrs = []
    for s in samples:
        eq = 1.0
        for r in s:
            eq *= 1.0 + r
        cagrs.append(eq ** (TRADING_DAYS / len(s)) - 1.0)
    cagrs.sort()
    alpha = (1.0 - level) / 2.0
    return {
        "lo": cagrs[int(alpha * (len(cagrs) - 1))],
        "hi": cagrs[int((1.0 - alpha) * (len(cagrs) - 1))],
        "point": point,
        "n_boot": len(cagrs), "level": level,
    }


# ───────────────────────────── 假设检验 ─────────────────────────────

def t_test_mean(returns, mu0=0.0):
    """单样本 t 检验：均值是否显著不等于 mu0。"""
    n = len(returns)
    if n < 3:
        return {"t": 0.0, "p": 1.0, "n": n, "mean": mean(returns) if returns else 0.0}
    s = std(returns)
    m = mean(returns)
    if s == 0:
        return {"t": 0.0, "p": 1.0, "n": n, "mean": m}
    t = (m - mu0) / (s / math.sqrt(n))
    return {"t": t, "p": t_two_sided_p(t, n - 1), "n": n, "mean": m}


def wilcoxon_signed_rank(returns, mu0=0.0):
    """符号秩检验（不假设正态）。小样本用正态近似。"""
    diffs = [r - mu0 for r in returns if r != mu0]
    n = len(diffs)
    if n < 8:
        return {"z": 0.0, "p": 1.0, "n": n}
    order = sorted(range(n), key=lambda i: abs(diffs[i]))
    ranks = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j + 1 < n and abs(diffs[order[j + 1]]) == abs(diffs[order[i]]):
            j += 1
        avg = (i + j) / 2.0 + 1.0          # 1-based 平均秩
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    w_plus = sum(ranks[i] for i in range(n) if diffs[i] > 0)
    mu_w = n * (n + 1) / 4.0
    sd_w = math.sqrt(n * (n + 1) * (2 * n + 1) / 24.0)
    z = (w_plus - mu_w) / sd_w if sd_w > 0 else 0.0
    return {"z": z, "p": 2.0 * (1.0 - norm_cdf(abs(z))), "n": n}


def benjamini_hochberg(pvalues, q=0.05):
    """BH 多重检验校正。返回每个 p 的通过标记与 BH 阈值。

    「我测了 28 个品种」这种场景下，不校正就等于把 5% 的偶然当发现。
    """
    m = len(pvalues)
    if m == 0:
        return {"pass": [], "thresholds": [], "m": 0}
    order = sorted(range(m), key=lambda i: pvalues[i])
    thresholds = [q * (k + 1) / m for k in range(m)]
    max_k = -1
    for k, idx in enumerate(order):
        if pvalues[idx] <= thresholds[k]:
            max_k = k
    passed = [False] * m
    if max_k >= 0:
        for k in range(max_k + 1):
            passed[order[k]] = True
    return {"pass": passed, "thresholds": thresholds, "m": m, "q": q}
