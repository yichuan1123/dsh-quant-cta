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

def adx(bars, period: int = 14):
    """Wilder 的 ADX（平均趋向指数）：衡量「趋势有多强」，与方向无关。

    +DM = 当日高点比前高多出的部分（且要大于前低的下移）
    -DM = 对称的另一侧
    两者与真实波幅一起做 Wilder 平滑，得到 +DI / -DI，再由 DX 平滑出 ADX。

    返回值与 bars 等长，前 2*period 个为 None（双向平滑需要预热）。
    ADX > 25 通常视为有趋势，< 20 视为震荡 —— 这是通行口径，不是精确分界。
    """
    n = len(bars)
    out = [None] * n
    if n < period * 2 + 2:
        return out
    trs, pdm, ndm = [], [], []
    for i in range(1, n):
        h, l = bars[i].high, bars[i].low
        pc = bars[i - 1].close
        tr = max(h - l, abs(h - pc), abs(l - pc))
        up = h - bars[i - 1].high
        dn = bars[i - 1].low - l
        pdm.append(up if (up > dn and up > 0) else 0.0)
        ndm.append(dn if (dn > up and dn > 0) else 0.0)
        trs.append(tr)
    if len(trs) < period * 2:
        return out
    # Wilder 平滑（等价于 alpha = 1/period 的递推，但首值用简单和——与经典定义一致）
    atr_s = sum(trs[:period])
    pdi_s = sum(pdm[:period])
    ndi_s = sum(ndm[:period])
    dxs = []
    for i in range(period, len(trs)):
        atr_s = atr_s - atr_s / period + trs[i]
        pdi_s = pdi_s - pdi_s / period + pdm[i]
        ndi_s = ndi_s - ndi_s / period + ndm[i]
        if atr_s <= 0:
            dxs.append(0.0)
            continue
        pdi = 100.0 * pdi_s / atr_s
        ndi = 100.0 * ndi_s / atr_s
        denom = pdi + ndi
        dxs.append(100.0 * abs(pdi - ndi) / denom if denom > 0 else 0.0)
    if len(dxs) < period:
        return out
    adx_s = sum(dxs[:period]) / period
    start = period + period     # bars 里第一个能算出 ADX 的位置（含首根 TR 的偏移）
    out[start] = adx_s
    for k in range(period, len(dxs)):
        adx_s = (adx_s * (period - 1) + dxs[k]) / period
        idx = start + (k - period) + 1
        if idx < n:
            out[idx] = adx_s
    return out
