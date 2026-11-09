"""指标计算。输入是 Bar 列表，输出是与输入等长的列表（预热期为 None）。"""


def true_ranges(bars):
    out = []
    prev_close = None
    for b in bars:
        if prev_close is None:
            out.append(b.high - b.low)
        else:
            out.append(max(b.high - b.low, abs(b.high - prev_close), abs(b.low - prev_close)))
        prev_close = b.close
    return out


def atr(bars, period: int = 20):
    """Wilder ATR。前 period 根为 None。"""
    if period <= 0:
        period = 1
    tr = true_ranges(bars)
    n = len(bars)
    out = [None] * n
    if n < period + 1:
        return out
    seed = sum(tr[1:period + 1]) / period
    out[period] = seed
    prev = seed
    for i in range(period + 1, n):
        prev = (prev * (period - 1) + tr[i]) / period
        out[i] = prev
    return out


def rolling_max(values, window: int):
    """过去 window 根（不含当根）的最大值。O(n*window)，品种内 n≈2500、window≤60，足够。"""
    n = len(values)
    out = [None] * n
    for i in range(n):
        if i < window:
            continue
        out[i] = max(values[i - window:i])
    return out


def rolling_min(values, window: int):
    n = len(values)
    out = [None] * n
    for i in range(n):
        if i < window:
            continue
        out[i] = min(values[i - window:i])
    return out


def pct_change(bars, lookback: int):
    n = len(bars)
    out = [None] * n
    for i in range(n):
        if i < lookback:
            continue
        base = bars[i - lookback].close
        if base > 0:
            out[i] = bars[i].close / base - 1.0
    return out
