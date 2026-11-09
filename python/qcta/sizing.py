"""仓位换算：把「风险预算」变成「手数」。

为什么不是「按固定金额买」：期货各品种一跳价值差 100 倍（螺纹 10 元/跳，
原油 100 元/跳）。等金额配置会让高波动品种主导整个组合的风险。
CTA 的标准做法是按**波动率倒数**分配，使每个品种贡献的风险近似相等。
"""

import math

TRADING_DAYS = 252


def daily_risk_budget(equity: float, target_vol: float) -> float:
    """组合的日波动预算（元）。target_vol 是年化波动率。"""
    if equity <= 0 or target_vol <= 0:
        return 0.0
    return equity * target_vol / math.sqrt(TRADING_DAYS)


def normalize_weights(weights):
    """按 sqrt(sum(w^2)) 归一，使组合风险≈预算（零相关近似）。

    等权 n 个品种时，每品种分到 budget/sqrt(n)：这样合起来
    sqrt(n × (budget/sqrt(n))²) = budget，正好用满预算。
    """
    vals = [abs(float(w)) for w in weights]
    if not vals:
        return []
    norm = math.sqrt(sum(v * v for v in vals))
    if norm <= 0:
        return [0.0 for _ in vals]
    return [v / norm for v in vals]


def lots_for_risk(spec, equity: float, atr_price: float, budget: float, max_symbol_risk: float) -> int:
    """给定元预算与该品种 ATR（价格单位），返回手数（向下取整）。"""
    if atr_price <= 0 or budget <= 0 or equity <= 0:
        return 0
    risk_per_lot = atr_price * spec.multiplier      # 元/手 的日波动
    if risk_per_lot <= 0:
        return 0
    lots = int(budget / risk_per_lot)
    if max_symbol_risk > 0:
        cap = int(equity * max_symbol_risk / risk_per_lot)
        lots = min(lots, cap)
    return max(0, lots)


def cap_by_leverage(desired, prices, specs, equity: float, max_leverage: float):
    """名义敞口超限时等比缩减手数。desired: {code: (direction, lots)}"""
    if max_leverage <= 0 or equity <= 0:
        return desired
    notionals = {}
    total = 0.0
    for code, (direction, lots) in desired.items():
        spec = specs[code]
        price = prices.get(code, 0.0)
        n = abs(lots) * price * spec.multiplier
        notionals[code] = n
        total += n
    limit = equity * max_leverage
    if total <= limit or total <= 0:
        return desired
    scale = limit / total
    out = {}
    for code, (direction, lots) in desired.items():
        out[code] = (direction, int(lots * scale))
    return out
