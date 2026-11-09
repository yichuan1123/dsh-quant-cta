"""时间序列的统计工具（纯标准库）。

量化里最常犯的错是「把自相关数据的标准误当成独立样本算」——夏普的 t 值会虚高
2–5 倍。这一层提供的都是**能修正这件事**的工具：

- `acf` / `pacf`：自相关结构
- `ljung_box`：白噪声检验（残差里还有没有可挖的东西）
- `ols`：最小二乘，支持 **Newey-West HAC 标准误**（自相关稳健）
- `adf`：单位根检验（这序列是随机游走还是有均值回归）
- `variance_ratio`：方差比检验（随机游走的另一个角度）
- `hurst_dfa`：去趋势波动分析估 Hurst 指数（趋势持续性强弱）
- `block_bootstrap`：循环块自助法（保留自相关的重采样）
"""

import math
import random

from .probability import mean, std


# ───────────────────────────── 线性代数 ─────────────────────────────

def solve(A, b):
    """高斯消元（部分主元）。A 为 n×n 行列表，b 为 n 维。"""
    n = len(A)
    M = [list(A[i]) + [b[i]] for i in range(n)]
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(M[r][col]))
        if abs(M[piv][col]) < 1e-14:
            raise ValueError("矩阵奇异")
        M[col], M[piv] = M[piv], M[col]
        pv = M[col][col]
        for r in range(col + 1, n):
            factor = M[r][col] / pv
            if factor == 0:
                continue
            for c in range(col, n + 1):
                M[r][c] -= factor * M[col][c]
    x = [0.0] * n
    for r in range(n - 1, -1, -1):
        acc = M[r][n] - sum(M[r][c] * x[c] for c in range(r + 1, n))
        x[r] = acc / M[r][r]
    return x


def inverse(A):
    n = len(A)
    inv = []
    for j in range(n):
        e = [0.0] * n
        e[j] = 1.0
        col = solve(A, e)
        inv.append(col)
    # solve 返回的是解向量（按行）；转置成矩阵
    return [[inv[j][i] for j in range(n)] for i in range(n)]


# ───────────────────────────── 回归 ─────────────────────────────

def _newey_west_meat(X, resid, lags):
    """Newey-West HAC：Σ_t e_t² x_t x_tᵀ + Σ_l w_l Σ_t e_t e_{t-l}(x_t x_{t-l}ᵀ + h.c.)"""
    n = len(X)
    k = len(X[0])
    S = [[0.0] * k for _ in range(k)]
    for t in range(n):
        for i in range(k):
            xi = X[t][i] * resid[t]
            for j in range(k):
                S[i][j] += xi * X[t][j] * resid[t]
    for lag in range(1, lags + 1):
        w = 1.0 - lag / (lags + 1.0)          # Bartlett 核
        for t in range(lag, n):
            for i in range(k):
                a = X[t][i] * resid[t]
                for j in range(k):
                    S[i][j] += w * a * X[t - lag][j] * resid[t - lag]
                    S[i][j] += w * X[t - lag][i] * resid[t - lag] * X[t][j] * resid[t]
    return S


def ols(y, X, hac_lags=0):
    """普通最小二乘，返回系数、标准误、t 值、R²。

    hac_lags > 0 时用 Newey-West 异方差+自相关稳健标准误（推荐对收益/权益这类时序数据使用）。
    X 需由调用方决定是否包含常数项（行列表，每行含全部回归元）。
    """
    n = len(y)
    k = len(X[0])
    XtX = [[sum(X[t][i] * X[t][j] for t in range(n)) for j in range(k)] for i in range(k)]
    Xty = [sum(X[t][i] * y[t] for t in range(n)) for i in range(k)]
    beta = solve(XtX, Xty)
    resid = [y[t] - sum(X[t][i] * beta[i] for i in range(k)) for t in range(n)]
    dof = max(1, n - k)
    sigma2 = sum(r * r for r in resid) / dof

    if hac_lags and hac_lags > 0:
        S = _newey_west_meat(X, resid, min(hac_lags, n - 2))
        XtX_inv = inverse(XtX)
        # Var = XtX⁻¹ · S · XtX⁻¹
        tmp = [[sum(XtX_inv[i][m] * S[m][j] for m in range(k)) for j in range(k)] for i in range(k)]
        cov = [[sum(tmp[i][m] * XtX_inv[m][j] for m in range(k)) for j in range(k)] for i in range(k)]
        se = [math.sqrt(max(cov[i][i], 0.0)) for i in range(k)]
    else:
        XtX_inv = inverse(XtX)
        se = [math.sqrt(max(sigma2 * XtX_inv[i][i], 0.0)) for i in range(k)]

    tstats = [(beta[i] / se[i]) if se[i] > 0 else 0.0 for i in range(k)]
    ybar = mean(y)
    sst = sum((v - ybar) ** 2 for v in y)
    sse = sum(r * r for r in resid)
    r2 = 1.0 - sse / sst if sst > 0 else 0.0
    return {
        "params": beta, "se": se, "tstats": tstats, "resid": resid,
        "r2": r2, "n": n, "k": k, "sigma2": sigma2,
    }


# ───────────────────────────── 自相关 ─────────────────────────────

def acf(xs, max_lag):
    """样本自相关（分母用 n，与常见实现一致）。"""
    n = len(xs)
    m = mean(xs)
    denom = sum((x - m) ** 2 for x in xs)
    out = []
    for k in range(1, max_lag + 1):
        if k >= n:
            break
        num = sum((xs[t] - m) * (xs[t - k] - m) for t in range(k, n))
        out.append(num / denom if denom > 0 else 0.0)
    return out


def pacf(xs, max_lag):
    """偏自相关（Durbin-Levinson 递推）。"""
    r = acf(xs, max_lag)
    if not r:
        return []
    phi = [[0.0] * max_lag for _ in range(max_lag)]
    out = []
    for k in range(max_lag):
        if k == 0:
            phi[0][0] = r[0]
        else:
            num = r[k] - sum(phi[k - 1][j] * r[k - j - 1] for j in range(k))
            den = 1.0 - sum(phi[k - 1][j] * r[j] for j in range(k))
            phi[k][k] = num / den if den != 0 else 0.0
            for j in range(k):
                phi[k][j] = phi[k - 1][j] - phi[k][k] * phi[k - 1][k - j - 1]
        out.append(phi[k][k])
    return out


def ljung_box(xs, lags=10):
    """Ljung-Box 白噪声检验。返回 {q, df, p, lags}；p 小 → 拒绝白噪声（还有结构）。"""
    from .probability import chi2_cdf

    n = len(xs)
    lags = min(lags, n - 2)
    if lags <= 0:
        return {"q": 0.0, "df": 0, "p": 1.0, "lags": 0}
    r = acf(xs, lags)
    q = n * (n + 2) * sum((r[k - 1] ** 2) / (n - k) for k in range(1, lags + 1))
    p = 1.0 - chi2_cdf(q, lags)
    return {"q": q, "df": lags, "p": p, "lags": lags}


# ───────────────────────────── 单位根 ─────────────────────────────

ADF_CRIT = {
    "c": {"1%": -3.43, "5%": -2.86, "10%": -2.57},        # 仅常数
    "ct": {"1%": -3.96, "5%": -3.41, "10%": -3.12},       # 常数 + 趋势
    "n": {"1%": -2.58, "5%": -1.95, "10%": -1.62},        # 无
}


def adf(y, max_lag=None, regression="c"):
    """增广 Dickey-Fuller 单位根检验。

    H0：存在单位根（随机游走）。统计量**小于**临界值 → 拒绝 H0 → 序列平稳（有均值回归）。
    滞后阶数按 AIC 在 0..max_lag 里选。
    """
    n = len(y)
    if n < 30:
        # 样本太短时增广项会把自由度吃光，回归矩阵直接奇异——如实说算不了
        return {"stat": 0.0, "lags": 0, "n": n, "crit": ADF_CRIT[regression],
                "reject": False, "insufficient": True}
    dy = [y[t] - y[t - 1] for t in range(1, n)]
    if max_lag is None:
        max_lag = int(12 * (n / 100.0) ** 0.25)
    # 经验上限：每个滞后阶大约吃掉 3 个自由度，至少留 12 个观测
    max_lag = max(0, min(max_lag, (len(dy) - 12) // 3))

    best = None
    for lag in range(max_lag + 1):
        start = lag
        rows, target = [], []
        for t in range(start, len(dy)):
            row = []
            if regression in ("c", "ct"):
                row.append(1.0)
            if regression == "ct":
                row.append(float(t + 1))
            row.append(y[t])                                  # y_{t-1}
            for j in range(1, lag + 1):
                row.append(dy[t - j])
            rows.append(row)
            target.append(dy[t])
        if len(target) < len(rows[0]) + 5:
            continue
        try:
            res = ols(target, rows)
        except ValueError:
            # 共线/奇异：这一阶跳过，换更小的滞后
            continue
        k = len(rows[0])
        aic = res["n"] * math.log(max(res["sigma2"], 1e-300)) + 2 * k
        if best is None or aic < best["aic"]:
            best = {"aic": aic, "res": res, "lag": lag, "k": k}
    if best is None:
        return {"stat": 0.0, "lags": 0, "n": n, "crit": ADF_CRIT[regression], "reject": False}

    idx = 1 if regression in ("c", "ct") else 0     # y_{t-1} 在常数（和趋势）之后
    stat = best["res"]["tstats"][idx]
    crit = ADF_CRIT[regression]
    return {
        "stat": stat, "lags": best["lag"], "n": best["res"]["n"],
        "crit": crit, "reject": stat < crit["5%"],
        "r2": best["res"]["r2"],
    }


def variance_ratio(returns, q):
    """Lo-MacKinlay 方差比检验（同方差版本）。

    VR=1 与随机游走一致；VR>1 表示正自相关（动量），<1 表示负自相关（均值回归）。
    """
    n = len(returns)
    if n < 4 * q:
        return {"vr": 1.0, "z": 0.0, "p": 1.0, "q": q, "n": n}
    mu = mean(returns)
    var1 = sum((r - mu) ** 2 for r in returns) / (n - 1)
    if var1 <= 0:
        return {"vr": 1.0, "z": 0.0, "p": 1.0, "q": q, "n": n}
    agg = [sum(returns[t:t + q]) for t in range(0, n - q + 1)]
    muq = mean(agg)
    varq = sum((a - muq) ** 2 for a in agg) / (len(agg) - 1)
    vr = varq / (q * var1)
    denom = math.sqrt(2.0 * (2 * q - 1) * (q - 1) / (3.0 * q * n))
    z = (vr - 1.0) / denom if denom > 0 else 0.0
    from .probability import norm_cdf

    p = 2.0 * (1.0 - norm_cdf(abs(z)))
    return {"vr": vr, "z": z, "p": p, "q": q, "n": n}


# ───────────────────────────── Hurst（DFA） ─────────────────────────────

def hurst_dfa(increments, scales=None):
    """去趋势波动分析（DFA）估 Hurst 指数。

    **输入必须是增量序列（收益率），不是价格路径。**
    DFA 内部会先做一次累加得到「轮廓」；如果直接喂价格（已经是累加结果），
    等于积分两次，H 会虚高到 1.5 左右——这是这个函数最容易踩的坑。

    H ≈ 0.5：随机游走（增量无记忆）；H > 0.5：增量正自相关（趋势持续，动量可能有效）；
    H < 0.5：增量负自相关（均值回归，反转策略可能有效）。

    做法：累加得轮廓 → 各尺度分窗、去线性趋势、算均方根 → log-log 回归斜率。
    """
    xs = increments
    n = len(xs)
    if n < 64:
        return 0.5
    m = mean(xs)
    profile = []
    acc = 0.0
    for x in xs:
        acc += x - m
        profile.append(acc)

    if scales is None:
        scales = []
        s = 8
        while s <= n // 4:
            scales.append(s)
            s = int(s * 1.6)
        if not scales:
            return 0.5

    logs_n, logs_f = [], []
    for s in scales:
        if s < 4:
            continue
        nseg = n // s
        if nseg < 2:
            continue
        f2 = 0.0
        count = 0
        for i in range(nseg):
            seg = profile[i * s:(i + 1) * s]
            xbar = (s - 1) / 2.0
            ybar = mean(seg)
            sxx = sum((k - xbar) ** 2 for k in range(s))
            if sxx == 0:
                continue
            sxy = sum((k - xbar) * (seg[k] - ybar) for k in range(s))
            slope = sxy / sxx
            intercept = ybar - slope * xbar
            for k in range(s):
                fit = intercept + slope * k
                f2 += (seg[k] - fit) ** 2
                count += 1
        if count == 0:
            continue
        f = math.sqrt(f2 / count)
        if f > 0:
            logs_n.append(math.log(s))
            logs_f.append(math.log(f))

    if len(logs_n) < 2:
        return 0.5
    xm = mean(logs_n)
    ym = mean(logs_f)
    sxx = sum((x - xm) ** 2 for x in logs_n)
    if sxx == 0:
        return 0.5
    sxy = sum((logs_n[i] - xm) * (logs_f[i] - ym) for i in range(len(logs_n)))
    return sxy / sxx


# ───────────────────────────── 重采样 ─────────────────────────────

def block_bootstrap(xs, n_boot, block=None, seed=0):
    """循环块自助法：保留局部自相关结构。

    block 默认 ceil(n^(1/3))——块太短会破坏依赖，太长会让重采样缺乏变化。
    """
    n = len(xs)
    if n == 0 or n_boot <= 0:
        return []
    if block is None:
        block = max(2, int(round(n ** (1.0 / 3.0))))
    block = min(block, n)
    rng = random.Random(seed)
    out = []
    for _ in range(n_boot):
        sample = []
        while len(sample) < n:
            start = rng.randrange(n)
            for j in range(block):
                sample.append(xs[(start + j) % n])
                if len(sample) >= n:
                    break
        out.append(sample)
    return out


def autocorr_se_factor(xs, max_lag=10):
    """自相关对均值标准误的放大系数 sqrt(1 + 2Σρ_k)。

    独立样本时为 1；强自相关时可达 2–3。用它把「按独立样本算出的 t 值」修回现实。
    """
    r = acf(xs, max_lag)
    total = 1.0 + 2.0 * sum(r)
    return math.sqrt(max(total, 1e-6))
