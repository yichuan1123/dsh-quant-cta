"""唐奇安通道突破（趋势跟随）。

规则（经典海龟骨架，参数刻意用大众值，不做优化）：
- 收盘价突破过去 entry 根的最高价 → 做多
- 收盘价跌破过去 exit 根的最低价 → 平多并反手做空
- 反向对称
这是「止损反手」系统：永远在场上，方向由最近一次突破决定。
"""

from ..indicators import adx, rolling_max, rolling_min
from .base import Strategy


class DonchianTrend(Strategy):
    name = "trend"

    # 默认 40/20：真实数据扫描里 IS(2021-2024) 与 OOS(2024-今) 都优于 20/10，
    # 且成本低三分之一（见 docs/SWEEP.md）。20/10 在两段上都是负的。
    def __init__(self, entry: int = 40, exit: int = 20, enabled: bool = True,
                 adx_min: float = 0.0, adx_period: int = 14):
        self.entry = int(entry)
        self.exit = int(exit)
        self.enabled = enabled
        self.adx_min = float(adx_min or 0.0)
        self.adx_period = int(adx_period)
        self.warmup = max(self.entry, self.exit) + 2 + (2 * self.adx_period if self.adx_min > 0 else 0)

    def generate(self, bundle, cfg):
        all_dates = bundle.dates
        per_code = {}

        for code, bars in bundle.bars_by_code.items():
            if not self.enabled or len(bars) < self.warmup:
                continue
            highs = [b.high for b in bars]
            lows = [b.low for b in bars]
            upper = rolling_max(highs, self.entry)
            lower = rolling_min(lows, self.exit)
            strength = adx(bars, self.adx_period) if self.adx_min > 0 else None
            direction = 0
            timeline = {}
            for i in range(self.warmup, len(bars)):
                up = upper[i]
                lo = lower[i]
                if up is None or lo is None:
                    continue
                close = bars[i].close
                new_dir = direction
                if close > up:
                    new_dir = 1
                elif close < lo:
                    new_dir = -1
                if new_dir != direction:
                    # 强度过滤：反手信号在弱趋势里只用来离场（归零），不建立反向仓
                    if strength is not None:
                        a = strength[i]
                        if a is None or a < self.adx_min:
                            new_dir = 0
                    direction = new_dir
                    timeline[bars[i].date] = direction
            if timeline:
                per_code[code] = timeline

        return self._emit_on_change(per_code, all_dates)
