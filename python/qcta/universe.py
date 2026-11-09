"""全盘品种池。

「全盘」不是「把所有代码都塞进组合」——那是拿流动性差的品种去喂滑点。
这里的做法是：从 28 个品种里按**可用性**筛出一批，再交给优化器分配权重。

筛选维度：
- 历史长度 ≥ min_bars（不够算 ATR 与协方差的直接剔除）
- 数据实际存在（bars 非空）
- 板块过滤（比如只想做商品，排除股指/国债）
"""

from .specs import SPECS


def all_codes():
    return sorted(SPECS.keys())


def sector_of(code):
    spec = SPECS.get(code)
    return spec.sector if spec is not None else "unknown"


def sector_map(codes):
    return [sector_of(c) for c in codes]


def select_universe(bars_by_code, min_bars=250, exclude_sectors=(), include_only=None):
    """返回 (codes, report)。

    report 记录「谁因为什么被剔除」——回测报告里要能解释品种池为什么是这些。
    """
    pool = list(all_codes()) if include_only is None else [c for c in include_only if c in SPECS]
    exclude_sectors = set(exclude_sectors or ())
    kept, dropped = [], []

    for code in pool:
        bars = bars_by_code.get(code) or []
        if code not in bars_by_code:
            dropped.append({"code": code, "reason": "no-data"})
            continue
        if len(bars) < min_bars:
            dropped.append({"code": code, "reason": "history<%d (%d)" % (min_bars, len(bars))})
            continue
        if sector_of(code) in exclude_sectors:
            dropped.append({"code": code, "reason": "sector-excluded(%s)" % sector_of(code)})
            continue
        kept.append(code)

    sectors = {}
    for code in kept:
        s = sector_of(code)
        sectors[s] = sectors.get(s, 0) + 1

    return kept, {
        "requested": len(pool),
        "kept": len(kept),
        "dropped": dropped,
        "sectors": sectors,
        "min_bars": min_bars,
        "exclude_sectors": sorted(exclude_sectors),
    }


def return_matrix(bars_by_code, codes, lookback=250):
    """把日收益序列按 codes 顺序对齐成矩阵（取最近 lookback 个交易日）。

    对齐方式：以**最晚的共同交易日**为终点，向前取 lookback 根。
    品种间停牌/缺数据会造成长度不一，这里按最短长度截齐，保证样本是同一段日历。
    """
    returns = []
    for code in codes:
        bars = bars_by_code.get(code) or []
        closes = [b.close for b in bars][-lookback - 1:]
        from .optimizer import daily_returns

        returns.append(daily_returns(closes))
    if not returns:
        return []
    shortest = min(len(r) for r in returns)
    return [r[len(r) - shortest:] if shortest > 0 else [] for r in returns]
