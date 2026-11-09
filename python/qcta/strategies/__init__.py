"""策略集。每个策略把历史行情映射成「决策日 → 目标持仓簿」。

约定（引擎依赖它，改之前先看 engine.py）：
- 只在一个日期出现，表示「该日收盘做了决策，次日开盘调仓」
- 该日给出的列表是**完整的期望持仓簿**：没列出的品种会被平掉
- 不出现的日期表示「不调仓，维持现状」
"""

from .carry import CarrySector
from .trend import DonchianTrend
from .xsec import CrossSectionMomentum

STRATEGIES = {
    "trend": DonchianTrend,
    "xsec": CrossSectionMomentum,
    "carry": CarrySector,
}


def build(name: str, **kwargs):
    cls = STRATEGIES.get(name)
    if cls is None:
        raise KeyError("未知策略 %r。可用：%s" % (name, ", ".join(sorted(STRATEGIES))))
    return cls(**kwargs)
