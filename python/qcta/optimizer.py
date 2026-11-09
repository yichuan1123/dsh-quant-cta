"""组合最优配比求解。

回答一个问题：**给定 N 个品种，每个应该分到多少风险预算？**

纯标准库实现（列表 + math），不依赖 numpy —— 回测、单测、插件在没装
numpy 的机器上都能跑。N≤30、观察期 T≤500 时，协方差 O(T·N²) 与
坐标下降都足够快。

支持的方法：

| 方法 | 目标 | 算法 |
|---|---|---|
| equal_weight | 等权（基准线） | 1/N |
| inverse_vol | 按波动率倒数 | 1/σ 归一 |
| risk_parity | 等风险贡献（ERC） | 坐标下降解凸问题（精确） |
| min_variance | 最小方差 | 投影梯度 |
| mean_variance | 均值方差 | 投影梯度（带收缩协方差） |

共同约束：权重非负（只做多）、单品种上限、板块上限。

数学说明（ERC）：解 min ½wᵀΣw − Σbᵢ·ln(wᵢ)，一阶条件是
wᵢ(Σw)ᵢ = bᵢ，即**每个品种的风险贡献相等**。固定其它分量时
对 wᵢ 是一元二次方程，直接求根即可，收敛快且稳定。
"""

import math

# ───────────────────────────── 基础统计 ─────────────────────────────

def mean(xs):
    return sum(xs) / len(xs) if xs else 0.0


def std(xs):
    n = len(xs)
    if n < 2:
        return 0.0
    m = mean(xs)
    var = sum((x - m) ** 2 for x in xs) / (n - 1)
    return math.sqrt(max(var, 0.0))


def daily_returns(closes):
    out = []
    for i in range(1, len(closes)):
        prev = closes[i - 1]
        if prev > 0:
            out.append(closes[i] / prev - 1.0)
    return out


def sample_cov(return_matrix):
    """样本协方差。return_matrix: 每个元素是一个品种的日收益序列（等长对齐）。"""
    n = len(return_matrix)
    if n == 0:
        return [], 0
    t = min(len(s) for s in return_matrix)
    if t < 2:
        return [[0.0] * n for _ in range(n)], t
    trimmed = [s[len(s) - t:] for s in return_matrix]
    mus = [mean(s) for s in trimmed]
    cov = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(i, n):
            acc = 0.0
            si, sj, mi, mj = trimmed[i], trimmed[j], mus[i], mus[j]
            for k in range(t):
                acc += (si[k] - mi) * (sj[k] - mj)
            v = acc / (t - 1)
            cov[i][j] = v
            cov[j][i] = v
    return cov, t


def shrink_cov(cov, return_matrix, t):
    """Ledoit-Wolf 恒等目标收缩：Σ̃ = (1−δ)·S + δ·m·I，m = tr(S)/N。

    N 接近甚至超过 T 时样本协方差是奇异的，直接用会让最小方差解爆掉。
    收缩强度 δ 用 LW 的最优估计 δ* = π/γ（截断到 [0,1]）：
      π = (1/T²)·Σₜ‖xₜxₜᵀ − S‖²_F    （估计误差）
      γ = ‖S − m·I‖²_F                （偏离目标的平方）
    """
    n = len(cov)
    if n == 0:
        return cov
    m = sum(cov[i][i] for i in range(n)) / n
    gamma_f = 0.0
    for i in range(n):
        for j in range(n):
            target = m if i == j else 0.0
            gamma_f += (cov[i][j] - target) ** 2
    if t >= 2 and return_matrix:
        trimmed = [s[len(s) - t:] for s in return_matrix]
        mus = [mean(s) for s in trimmed]
        pi = 0.0
        for k in range(t):
            # ‖xₜxₜᵀ − S‖²_F 展开为 Σᵢⱼ (xᵢxⱼ − Sᵢⱼ)²
            for i in range(n):
                xi = trimmed[i][k] - mus[i]
                for j in range(n):
                    xj = trimmed[j][k] - mus[j]
                    pi += (xi * xj - cov[i][j]) ** 2
        pi /= (t * t)
    else:
        pi = gamma_f
    if gamma_f <= 0:
        delta = 1.0
    else:
        delta = max(0.0, min(1.0, pi / gamma_f))
    out = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(n):
            target = m if i == j else 0.0
            out[i][j] = (1.0 - delta) * cov[i][j] + delta * target
    return out


def annualized_vol(cov, w):
    """组合年化波动（cov 是日频）。"""
    n = len(w)
    var = 0.0
    for i in range(n):
        if w[i] == 0:
            continue
        for j in range(n):
            if w[j] == 0:
                continue
            var += w[i] * w[j] * cov[i][j]
    return math.sqrt(max(var, 0.0) * 252.0)


# ───────────────────────────── 约束投影 ─────────────────────────────

def project_capped(v, cap):
    """把任意向量投影到 {w ≥ 0, Σw = 1, wᵢ ≤ cap}。

    用「封顶 + 补缺口」的迭代：触顶的固定住，剩余预算按未触顶分量的
    权重再分配，直到总和为 1 或全部触顶。
    """
    n = len(v)
    if n == 0:
        return []
    cap = float(cap)
    if cap <= 0 or cap * n < 1.0:
        cap = 1.0 / n
    w = [max(0.0, float(x)) for x in v]
    total = sum(w)
    if total <= 0:
        w = [1.0 / n] * n
    else:
        w = [x / total for x in w]
    for _ in range(200):
        w = [min(cap, x) for x in w]
        s = sum(w)
        if abs(s - 1.0) < 1e-12:
            break
        free = [i for i in range(n) if w[i] < cap - 1e-15]
        if not free:
            # 全部触顶：只能等比缩放（此时 cap·n ≥ 1，舍入误差内可满足）
            w = [x / s for x in w] if s > 0 else [1.0 / n] * n
            break
        if s < 1.0:
            weights = [(max(v[i], 0.0) if v[i] > 0 else 1e-9) for i in free]
            tot = sum(weights) or float(len(free))
            gap = 1.0 - s
            for idx, i in enumerate(free):
                w[i] += gap * weights[idx] / tot
        else:
            w = [x / s for x in w]
    return w


def apply_sector_caps(weights, sectors, cap):
    """板块上限。超限板块按比例缩到 cap，释放的预算按权重回填给未触顶板块。"""
    if not cap or cap <= 0 or cap >= 1.0:
        return weights
    w = list(weights)
    for _ in range(100):
        totals = {}
        for i, s in enumerate(sectors):
            totals[s] = totals.get(s, 0.0) + w[i]
        excess = {s: t - cap for s, t in totals.items() if t > cap + 1e-12}
        if not excess:
            break
        freed = 0.0
        for i, s in enumerate(sectors):
            if s in excess:
                scale = cap / totals[s]
                new = w[i] * scale
                freed += w[i] - new
                w[i] = new
        room = [i for i, s in enumerate(sectors) if s not in excess]
        if not room or freed <= 0:
            break
        tot = sum(w[i] for i in room)
        if tot <= 0:
            break
        for i in room:
            w[i] += freed * w[i] / tot
    return w


# ───────────────────────────── 各方法 ─────────────────────────────

def _equal_weight(n):
    return [1.0 / n] * n if n else []


def _inverse_vol(cov):
    n = len(cov)
    inv = []
    for i in range(n):
        v = cov[i][i]
        inv.append(1.0 / math.sqrt(v) if v > 0 else 0.0)
    total = sum(inv)
    if total <= 0:
        return _equal_weight(n)
    return [x / total for x in inv]


def risk_parity(cov, budgets=None, iters=5000, tol=1e-14):
    """等风险贡献（ERC）。返回归一化权重（Σw = 1）。"""
    n = len(cov)
    if n == 0:
        return []
    if n == 1:
        return [1.0]
    b = list(budgets) if budgets else [1.0 / n] * n
    w = []
    for i in range(n):
        v = cov[i][i]
        w.append(1.0 / math.sqrt(v) if v > 0 else 1.0)
    s = sum(w)
    w = [x / s for x in w]
    # 迭代内部**不做归一化**：固定点 wᵢ(Σw)ᵢ = bᵢ 自带尺度，
    # 每轮归一化会把尺度一起压小，收敛反而变慢（实测残差停在 1e-2 量级）。
    for _ in range(iters):
        max_rel = 0.0
        for i in range(n):
            a = cov[i][i]
            if a <= 0:
                w[i] = 0.0
                continue
            c = 0.0
            for j in range(n):
                if j != i:
                    c += cov[i][j] * w[j]
            disc = c * c + 4.0 * a * b[i]
            new = (-c + math.sqrt(max(disc, 0.0))) / (2.0 * a)
            if new < 0:
                new = 0.0
            if w[i] > 0:
                max_rel = max(max_rel, abs(new - w[i]) / w[i])
            else:
                max_rel = max(max_rel, 1.0)
            w[i] = new
        if max_rel < tol:
            break
    total = sum(w)
    if total <= 0:
        return _equal_weight(n)
    return [x / total for x in w]


def _gradient_descend(cov, grad_fn, n, iters, lr=None):
    w = [1.0 / n] * n
    row = max(sum(abs(x) for x in r) for r in cov) if cov else 1.0
    step = lr if lr else (1.0 / (2.0 * row) if row > 0 else 0.1)
    for _ in range(iters):
        g = grad_fn(w)
        w = [w[i] - step * g[i] for i in range(n)]
        w = [max(0.0, x) for x in w]
        s = sum(w)
        if s > 0:
            w = [x / s for x in w]
    return w


def min_variance(cov, iters=600):
    """最小方差（长仓）。投影梯度：每次下降后投回单纯形。"""
    n = len(cov)
    if n == 0:
        return []

    def grad(w):
        return [2.0 * sum(cov[i][j] * w[j] for j in range(n)) for i in range(n)]

    return _gradient_descend(cov, grad, n, iters)


def mean_variance(cov, expected_returns, risk_aversion=2.0, iters=600):
    """均值方差：min −μᵀw + (λ/2)·wᵀΣw（长仓）。"""
    n = len(cov)
    if n == 0:
        return []
    mu = list(expected_returns or [0.0] * n)

    def grad(w):
        return [
            -mu[i] + risk_aversion * sum(cov[i][j] * w[j] for j in range(n))
            for i in range(n)
        ]

    return _gradient_descend(cov, grad, n, iters)


# ───────────────────────────── 统一入口 ─────────────────────────────

METHODS = ("equal_weight", "inverse_vol", "risk_parity", "min_variance", "mean_variance")


def optimize(method, return_matrix, symbols, sectors=None,
             max_weight=0.15, sector_cap=0.35,
             expected_returns=None, risk_aversion=2.0, iters=600):
    """求解最优配比。

    return_matrix: 与 symbols 对齐的日收益序列
    返回 {weights, diagnostics}
    """
    if method not in METHODS:
        raise ValueError("未知方法 %r，可用：%s" % (method, ", ".join(METHODS)))
    n = len(symbols)
    if n == 0:
        return {"weights": {}, "diagnostics": {"method": method, "n": 0}}
    if n == 1:
        return {"weights": {symbols[0]: 1.0},
                "diagnostics": {"method": method, "n": 1, "note": "单品种，权重必为 1"}}

    sectors = sectors or ["na"] * n
    cov, t = sample_cov(return_matrix)
    cov_s = shrink_cov(cov, return_matrix, t)

    if method == "equal_weight":
        w = _equal_weight(n)
    elif method == "inverse_vol":
        w = _inverse_vol(cov_s)
    elif method == "risk_parity":
        w = risk_parity(cov_s)
    elif method == "min_variance":
        w = min_variance(cov_s, iters=iters)
    elif method == "mean_variance":
        w = mean_variance(cov_s, expected_returns, risk_aversion, iters=iters)
    else:
        raise ValueError("未知方法 %r，可用：%s" % (method, ", ".join(METHODS)))

    w = project_capped(w, max_weight)
    w = apply_sector_caps(w, sectors, sector_cap)
    w = project_capped(w, max_weight)

    # 风险贡献（诊断用）
    rc = []
    for i in range(n):
        contrib = w[i] * sum(cov_s[i][j] * w[j] for j in range(n))
        rc.append(contrib)
    total_rc = sum(rc)
    if total_rc > 0:
        rc = [x / total_rc for x in rc]

    hhi = sum(x * x for x in w)
    diagnostics = {
        "method": method,
        "n": n,
        "observations": t,
        "ann_vol_pct": round(annualized_vol(cov_s, w) * 100.0, 3),
        "hhi": round(hhi, 5),
        "effective_n": round(1.0 / hhi, 2) if hhi > 0 else 0.0,
        "max_weight_pct": round(max(w) * 100.0, 2) if w else 0.0,
        "min_weight_pct": round(min(w) * 100.0, 2) if w else 0.0,
        "risk_contributions": {symbols[i]: round(rc[i] * 100.0, 2) for i in range(n)},
        "sector_totals_pct": {},
        "shrinkage": "ledoit-wolf-identity",
    }
    totals = {}
    for i, s in enumerate(sectors):
        totals[s] = totals.get(s, 0.0) + w[i]
    diagnostics["sector_totals_pct"] = {k: round(v * 100.0, 2) for k, v in sorted(totals.items())}

    return {"weights": {symbols[i]: round(w[i], 8) for i in range(n)}, "diagnostics": diagnostics}
