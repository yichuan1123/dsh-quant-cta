"""交易成本：按品种估滑点（不再全局 1 跳）。

为什么不能全局 1 跳：螺纹一跳 1 元、每手 10 吨 = 10 元/手，而 IF 一跳 0.2 点 × 300 = 60 元/手。
同样是「1 跳」，成本差 6 倍；流动性差的品种实际要吃掉几个跳。

估计方法（都能只用日线/分钟线做，不需要订单簿）：

1. **成交额**：`volume × close × multiplier` 的中位数 —— 衡量「吃不吃得下」。
2. **日内振幅**：`(high − low) / tick` 的中位数 —— 一天里价格能走多少个跳。
3. **有效价差代理**：1 分钟 bar 里 `(high − low) / tick` 的**低分位**（10%）。
   安静的一分钟里价格只在买卖价之间来回，振幅≈价差；这是文献里常用的 spread proxy。
4. 滑点（跳）= `max(1, 0.5 × 价差代理 + 0.5)` —— 半价差过市 + 半个跳的冲击，向下取整到 1 跳。

拿不到分钟数据时退回「日内振幅 / 20」，并在结果里标注 `method`，绝不假装精确。
"""

import json
import math
import os

DATA_DIR = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "data"))
COSTS_PATH = os.path.join(DATA_DIR, "costs.json")


def median(xs):
    if not xs:
        return 0.0
    s = sorted(xs)
    n = len(s)
    mid = n // 2
    return s[mid] if n % 2 else (s[mid - 1] + s[mid]) / 2.0


def quantile(xs, q):
    if not xs:
        return 0.0
    s = sorted(xs)
    if len(s) == 1:
        return s[0]
    pos = q * (len(s) - 1)
    lo = int(math.floor(pos))
    hi = min(lo + 1, len(s) - 1)
    w = pos - lo
    return s[lo] * (1 - w) + s[hi] * w


def daily_stats(bars, spec):
    """成交额与振幅（都用日线，任何品种都有）。"""
    amounts = []
    ranges = []
    for b in bars:
        if b.close <= 0 or b.volume <= 0:
            continue
        amounts.append(b.volume * b.close * spec.multiplier)
        ranges.append((b.high - b.low) / spec.tick)
    return {
        "median_amount": median(amounts),
        "median_range_ticks": median(ranges),
        "days": len(amounts),
    }


def spread_from_minutes(minute_bars, spec):
    """1 分钟 bar 估有效价差（跳）。安静分钟的振幅 ≈ 买卖价差。"""
    ranges = [(b.high - b.low) / spec.tick for b in minute_bars if b.high > b.low > 0]
    if len(ranges) < 60:
        return None
    return quantile(ranges, 0.10)


# 1 分钟 bar 的振幅只有在「跳数远小于波动」时才能当价差用。
# 指数期货一跳 0.2 点、一分钟能走 2 个点（10 跳），此时振幅几乎全是波动——
# 所以对价差代理设 3 跳上限，宁可保守也不用一个虚高的数把回测搞成「怎么都不赚」。
SPREAD_CAP_TICKS = 3.0


def slip_ticks_from(spread_ticks=None, range_ticks=None):
    """滑点（跳）= 半个价差 + 半个跳冲击，向下取整到 1 跳。

    价差代理超过 3 跳时按 3 跳算并标注 `capped`——分钟线分辨不出价差与波动。
    """
    if spread_ticks is not None and spread_ticks > 0:
        s = min(float(spread_ticks), SPREAD_CAP_TICKS)
        method = "minute_spread" if spread_ticks <= SPREAD_CAP_TICKS else "minute_spread_capped"
        raw = 0.5 * s + 0.5
    elif range_ticks:
        s = min(float(range_ticks) / 20.0, SPREAD_CAP_TICKS)
        method = "daily_range_proxy"
        raw = 0.5 * s + 0.5
    else:
        return 1.0, "default"
    return float(max(1.0, round(raw))), method


def build_table(bars_by_code, specs, minutes_by_code=None, min_amount=None):
    """生成 {code: {...}} 成本表；min_amount 给了就顺带标出流动性不足的品种。"""
    minutes_by_code = minutes_by_code or {}
    out = {}
    for code, bars in bars_by_code.items():
        spec = specs.get(code)
        if not spec or not bars:
            continue
        st = daily_stats(bars, spec)
        spread = spread_from_minutes(minutes_by_code.get(code) or [], spec)
        ticks, method = slip_ticks_from(spread, st["median_range_ticks"])
        row = {
            "slip_ticks": ticks,
            "method": method,
            "spread_ticks": round(spread, 3) if spread else None,
            "median_amount": round(st["median_amount"], 1),
            "median_range_ticks": round(st["median_range_ticks"], 2),
            "days": st["days"],
            "tick_value": round(spec.tick_value, 2),
        }
        if min_amount:
            row["illiquid"] = st["median_amount"] < min_amount
        out[code] = row
    return out


def load_costs(path=None):
    path = path or COSTS_PATH
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        return {}
    return (data or {}).get("symbols") or {}


def save_costs(table, path=None, note=""):
    path = path or COSTS_PATH
    os.makedirs(os.path.dirname(path), exist_ok=True)
    payload = {"note": note, "symbols": table}
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=1)
    return path


def slip_map(table, fallback=1.0):
    """给引擎用的 {code: 跳数}。"""
    return {code: float(row.get("slip_ticks") or fallback) for code, row in (table or {}).items()}
