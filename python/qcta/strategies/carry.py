"""期限结构（carry）：做多曲线最陡（backwardation 最深）、做空最平（contango 最深）。

规则（刻意朴素，不做参数优化）：
- 每 rebalance 个交易日调仓一次
- 按当日**横截面去均值后**的年化 carry 排序
- 多头前 top_n，空头后 top_n，等权
- 只有真的抓到合约数据的品种参与（`data/term/<code>.json`，由 tools/term_build.py 生成）

与趋势/动量腿的关系：carry 交易的是「曲线形状」，不是价格方向，
所以它跟 trend（绝对动量）、xsec（相对动量）在逻辑上不重叠——这正是它值得占一腿的原因。
"""

from .. import carry as carry_mod
from ..types import TargetPosition
from .base import Strategy


class CarrySector(Strategy):
    name = "carry"

    def __init__(self, top_n: int = 3, rebalance: int = 20, enabled: bool = True,
                 term_dir=None, min_symbols: int = 6):
        self.top_n = max(1, int(top_n))
        self.rebalance = max(1, int(rebalance))
        self.enabled = enabled
        self.term_dir = term_dir
        self.min_symbols = int(min_symbols)
        self.warmup = 0

    def generate(self, bundle, cfg):
        if not self.enabled:
            return {}
        all_dates = bundle.dates
        if not all_dates:
            return {}
        codes = list(bundle.bars_by_code.keys())
        frames = carry_mod.load_all(codes, self.term_dir)
        if len(frames) < self.min_symbols:
            return {}
        xs = carry_mod.cross_section(frames, all_dates)
        if not xs:
            return {}

        out = {}
        since = 0
        for date in all_dates:
            row = xs.get(date)
            if not row:
                continue
            since += 1
            if since < self.rebalance and out:
                continue
            since = 0
            ranked = sorted(row.items(), key=lambda kv: -kv[1])
            k = min(self.top_n, len(ranked) // 2)
            if k < 1:
                continue
            picks = [(c, 1) for c, _ in ranked[:k]] + [(c, -1) for c, _ in ranked[-k:]]
            out[date] = [TargetPosition(symbol=c, direction=d) for c, d in sorted(picks)]
        return out
