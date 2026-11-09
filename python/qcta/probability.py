"""概率分布与稳健统计（纯标准库实现）。

为什么不用 numpy/scipy：项目从一开始就约束「引擎与统计在无第三方库的机器上也能跑」，
回测、单测、插件在任何环境都能验证。代价是要自己实现分布函数，
所以每条函数都配了已知值测试（见 tests/test_probability.py）。

覆盖：
- 正态（CDF / 分位数 Acklam 近似 / 抽样）
- χ²（正则化不完全伽马）
- Student-t（正则化不完全 Beta，含自由度）
- Beta（分位数用二分）
- 描述统计与稳健统计（中位数 / MAD / 截尾均值 / 缩尾 / 偏度 / 峰度）
"""

import math
import random

SQRT2 = math.sqrt(2.0)
INV_SQRT2PI = 1.0 / math.sqrt(2.0 * math.pi)


# ───────────────────────────── 正态 ─────────────────────────────

def norm_cdf(x: float) -> float:
    """标准正态 CDF：Φ(x) = ½[1 + erf(x/√2)]。"""
    return 0.5 * (1.0 + math.erf(x / SQRT2))


def norm_pdf(x: float) -> float:
    return INV_SQRT2PI * math.exp(-0.5 * x * x)


def norm_ppf(p: float) -> float:
    """标准正态分位数（Acklam 有理逼近，绝对误差 < 1.2e-9）。

    分位数是 VaR、DSR、置信区间都要用的基础件，自己实现比查表可靠。
    """
    # p=0/1 返回 ∓inf 是数学上正确且实用的（极端置信度的 VaR 不该抛异常）；
    # 只有落在 [0,1] 之外才是调用错误。
    if p < 0.0 or p > 1.0:
        raise ValueError("p 必须在 [0,1] 内")
    if p == 0.0:
        return float("-inf")
    if p == 1.0:
        return float("inf")

    a = (-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00)
    b = (-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01)
    c = (-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00)
    d = (7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00)

    p_low = 0.02425
    p_high = 1.0 - p_low

    if p < p_low:
        q = math.sqrt(-2.0 * math.log(p))
        return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
               ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1.0)
    if p > p_high:
        q = math.sqrt(-2.0 * math.log(1.0 - p))
        return -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
                ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1.0)
    q = p - 0.5
    r = q * q
    return (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q / \
           (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1.0)


# ───────────────────────────── 不完全伽马 / χ² ─────────────────────────────

def _gammainc_P(a: float, x: float) -> float:
    """正则化下不完全伽马 P(a,x)：级数（x < a+1）/ 连分式（否则）。"""
    if x <= 0.0:
        return 0.0
    if x < a + 1.0:
        # 级数展开
        term = 1.0 / a
        total = term
        n = a
        for _ in range(500):
            n += 1.0
            term *= x / n
            total += term
            if abs(term) < abs(total) * 1e-15:
                break
        return total * math.exp(-x + a * math.log(x) - math.lgamma(a))
    # 连分式（Lentz）
    tiny = 1e-300
    b = x + 1.0 - a
    c = 1.0 / tiny
    d = 1.0 / b
    h = d
    for i in range(1, 500):
        an = -i * (i - a)
        b += 2.0
        d = an * d + b
        if abs(d) < tiny:
            d = tiny
        c = b + an / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 1e-15:
            break
    return 1.0 - math.exp(-x + a * math.log(x) - math.lgamma(a)) * h


def chi2_cdf(x: float, df: float) -> float:
    return _gammainc_P(df / 2.0, x / 2.0)


def chi2_ppf(p: float, df: float) -> float:
    """χ² 分位数（二分；单调函数足够稳）。"""
    if p <= 0.0:
        return 0.0
    if p >= 1.0:
        return float("inf")
    lo, hi = 0.0, max(1.0, df * 4.0)
    while chi2_cdf(hi, df) < p:
        hi *= 2.0
        if hi > 1e12:
            return hi
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if chi2_cdf(mid, df) < p:
            lo = mid
        else:
            hi = mid
        if hi - lo < 1e-12 * max(1.0, hi):
            break
    return 0.5 * (lo + hi)


# ───────────────────────────── 不完全 Beta / t 分布 ─────────────────────────────

def _betacf(a: float, b: float, x: float) -> float:
    """不完全 Beta 连分式（Lentz 法）。"""
    tiny = 1e-300
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < tiny:
        d = tiny
    d = 1.0 / d
    h = d
    for m in range(1, 500):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < tiny:
            d = tiny
        c = 1.0 + aa / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < tiny:
            d = tiny
        c = 1.0 + aa / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 1e-15:
            break
    return h


def beta_inc(a: float, b: float, x: float) -> float:
    """正则化不完全 Beta I_x(a,b)。"""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    log_beta = math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
    front = math.exp(log_beta + a * math.log(x) + b * math.log(1.0 - x))
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _betacf(a, b, x) / a
    return 1.0 - front * _betacf(b, a, 1.0 - x) / b


def beta_ppf(p: float, a: float, b: float) -> float:
    lo, hi = 0.0, 1.0
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if beta_inc(a, b, mid) < p:
            lo = mid
        else:
            hi = mid
        if hi - lo < 1e-14:
            break
    return 0.5 * (lo + hi)


def t_cdf(t: float, df: float) -> float:
    if df <= 0:
        raise ValueError("df 必须为正")
    x = df / (df + t * t)
    p = 0.5 * beta_inc(df / 2.0, 0.5, x)
    return 1.0 - p if t > 0 else p


def t_ppf(p: float, df: float) -> float:
    """t 分位数（二分；对称分布）。"""
    if not (0.0 < p < 1.0):
        raise ValueError("p 必须在 (0,1) 内")
    if abs(p - 0.5) < 1e-15:
        return 0.0
    if p < 0.5:
        return -t_ppf(1.0 - p, df)
    lo, hi = 0.0, 1.0
    while t_cdf(hi, df) < p:
        hi *= 2.0
        if hi > 1e8:
            return hi
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if t_cdf(mid, df) < p:
            lo = mid
        else:
            hi = mid
        if hi - lo < 1e-13:
            break
    return 0.5 * (lo + hi)


def t_two_sided_p(t: float, df: float) -> float:
    """双侧 p 值。"""
    return 2.0 * (1.0 - t_cdf(abs(t), df))


# ───────────────────────────── 描述与稳健统计 ─────────────────────────────

def mean(xs):
    return sum(xs) / len(xs) if xs else 0.0


def variance(xs, ddof=1):
    n = len(xs)
    if n <= ddof:
        return 0.0
    m = mean(xs)
    return sum((x - m) ** 2 for x in xs) / (n - ddof)


def std(xs, ddof=1):
    return math.sqrt(max(variance(xs, ddof), 0.0))


def median(xs):
    s = sorted(xs)
    n = len(s)
    if n == 0:
        return 0.0
    mid = n // 2
    return s[mid] if n % 2 else 0.5 * (s[mid - 1] + s[mid])


def mad(xs):
    """中位数绝对偏差（未乘 1.4826，保持原始尺度）。"""
    m = median(xs)
    return median([abs(x - m) for x in xs])


def quantile(xs, q):
    """线性插值分位数（与 numpy 默认 percentile 一致）。"""
    if not xs:
        return 0.0
    s = sorted(xs)
    if q <= 0:
        return s[0]
    if q >= 1:
        return s[-1]
    pos = (len(s) - 1) * q
    lo = int(math.floor(pos))
    hi = min(lo + 1, len(s) - 1)
    frac = pos - lo
    return s[lo] * (1.0 - frac) + s[hi] * frac


def trimmed_mean(xs, proportion=0.05):
    """截尾均值：两端各去掉 proportion 比例。抗极端值。"""
    if not xs:
        return 0.0
    s = sorted(xs)
    k = int(len(s) * proportion)
    core = s[k:len(s) - k] if len(s) - 2 * k > 0 else s
    return mean(core)


def winsorize(xs, proportion=0.05):
    """缩尾：把两端极端值替换为分位点值。保留样本量，不改排序。"""
    if not xs:
        return []
    lo = quantile(xs, proportion)
    hi = quantile(xs, 1.0 - proportion)
    return [min(max(x, lo), hi) for x in xs]


def skewness(xs):
    n = len(xs)
    if n < 3:
        return 0.0
    m = mean(xs)
    s = std(xs)
    if s == 0:
        return 0.0
    return sum(((x - m) / s) ** 3 for x in xs) * n / ((n - 1) * (n - 2))


def kurtosis(xs):
    """超额峰度（正态 = 0）。"""
    n = len(xs)
    if n < 4:
        return 0.0
    m = mean(xs)
    s = std(xs)
    if s == 0:
        return 0.0
    m4 = sum(((x - m) / s) ** 4 for x in xs) / n
    # 无偏化
    return ((n + 1) * m4 - 3 * (n - 1)) * (n - 1) / ((n - 2) * (n - 3))


def zscore(x: float, xs):
    s = std(xs)
    return 0.0 if s == 0 else (x - mean(xs)) / s


def sample_normal(rng: random.Random, n: int):
    return [rng.gauss(0.0, 1.0) for _ in range(n)]
