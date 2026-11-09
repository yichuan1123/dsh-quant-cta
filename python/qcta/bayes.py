"""贝叶斯更新：小样本下的胜率与均值估计。

回测里常见「胜率 52%，所以我占了优势」——但 200 笔交易的 52% 与 2000 笔的 52%
可信度天差地别。贝叶斯给出的是**后验分布**，直接能回答「胜率超过 50% 的概率是多少」，
比点估计诚实得多。
"""

import math

from .probability import beta_inc, beta_ppf, mean, std


def beta_binomial(wins, trials, prior=(1.0, 1.0)):
    """Beta-Binomial 共轭更新。prior=(a,b)，默认均匀先验 Beta(1,1)。"""
    a0, b0 = prior
    a = a0 + wins
    b = b0 + (trials - wins)
    return {
        "posterior_a": a,
        "posterior_b": b,
        "posterior_mean": a / (a + b),
        "prior_mean": a0 / (a0 + b0),
        "trials": trials,
        "wins": wins,
        "ci90": (beta_ppf(0.05, a, b), beta_ppf(0.95, a, b)),
        "ci95": (beta_ppf(0.025, a, b), beta_ppf(0.975, a, b)),
    }


def prob_win_rate_above(wins, trials, threshold=0.5, prior=(1.0, 1.0)):
    """P(真实胜率 > threshold | 观测)。避免"52% 就是优势"的错觉。"""
    a0, b0 = prior
    a = a0 + wins
    b = b0 + (trials - wins)
    return 1.0 - beta_inc(a, b, threshold)


def normal_inverse_gamma(mu0, kappa0, alpha0, beta0, data):
    """正态-逆伽马共轭更新（估计均值与方差）。

    κ 越大表示先验对 mu 越自信；α、β 控制方差的先验。
    """
    n = len(data)
    if n == 0:
        return {"mu": mu0, "kappa": kappa0, "alpha": alpha0, "beta": beta0, "n": 0}
    xbar = mean(data)
    ss = sum((x - xbar) ** 2 for x in data)
    kappa = kappa0 + n
    mu = (kappa0 * mu0 + n * xbar) / kappa
    alpha = alpha0 + n / 2.0
    beta = beta0 + 0.5 * ss + (kappa0 * n * (xbar - mu0) ** 2) / (2.0 * kappa)
    return {
        "mu": mu, "kappa": kappa, "alpha": alpha, "beta": beta, "n": n,
        "mu_std": math.sqrt(beta / (alpha * kappa)) if alpha > 1 else float("nan"),
    }
