"""横截面动量：在品种池内做多强势、做空弱势。

规则：
- 每 rebalance 个交易日调仓一次
- 按过去 lookback 日收益率排序
- 多头前 top_frac，空头后 top_frac（相等权重）
这样做的是「品种间相对强弱」，与趋势策略（绝对方向）相关性较低，
是用来降低组合层面回撤的第二条腿。
"""

from ..indicators import pct_change
from ..types import TargetPosition
from .base import Strategy


class CrossSectionMomentum(Strategy):
    name = "xsec"

    def __init__(self, lookback: int = 60, rebalance: int = 20, top_frac: float = 0.3, enabled: bool = True):
        self.lookback = int(lookback)
        self.rebalance = max(1, int(rebalance))
        self.top_frac = float(top_frac)
        self.enabled = enabled
        self.warmup = self.lookback + 5

    def generate(self, bundle, cfg):
        if not self.enabled:
            return {}
        all_dates = bundle.dates
        if not all_dates:
            return {}

        # 预计算每个品种的动量序列
        mom = {}
        for code, bars in bundle.bars_by_code.items():
            if len(bars) < self.warmup:
                continue
            series = pct_change(bars, self.lookback)
            mom[code] = {bars[i].date: series[i] for i in range(len(bars)) if series[i] is not None}
        if len(mom) < 3:
            return {}

        out = {}
        counter = 0
        for date in all_dates:
            counter += 1
            if counter % self.rebalance != 0:
                continue
            scored = []
            for code, table in mom.items():
                v = table.get(date)
                if v is not None:
                    scored.append((v, code))
            if len(scored) < 3:
                continue
            scored.sort(reverse=True)
            k = max(1, int(len(scored) * self.top_frac))
            longs = [c for _, c in scored[:k]]
            shorts = [c for _, c in scored[-k:]]
            if set(longs) & set(shorts):
                continue
            book = [TargetPosition(symbol=c, direction=1) for c in longs] + \
                   [TargetPosition(symbol=c, direction=-1) for c in shorts]
            out[date] = book
        return out
