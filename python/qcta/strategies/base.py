"""策略基类。"""

from ..types import TargetPosition


class Strategy:
    name = "base"
    warmup = 0

    def generate(self, bundle, cfg):
        """返回 {date: [TargetPosition, ...]}。"""
        raise NotImplementedError

    # ── 公共工具 ──

    @staticmethod
    def _emit_on_change(per_code_dir, all_dates):
        """把「每个品种的方向时间线」合成「整簿快照的时间线」。

        per_code_dir: {code: {date: direction}} —— 只记录方向发生变化的日子
        返回: {date: [TargetPosition]} —— 只在整簿发生变化的日子出现
        """
        current = {}
        last = {}
        out = {}
        for date in all_dates:
            changed = False
            for code, timeline in per_code_dir.items():
                if date in timeline:
                    new_dir = timeline[date]
                    if current.get(code, 0) != new_dir:
                        current[code] = new_dir
                        changed = True
                    if new_dir == 0:
                        current.pop(code, None)
            if changed:
                snapshot = {c: d for c, d in current.items() if d != 0}
                if snapshot != last:
                    out[date] = [TargetPosition(symbol=c, direction=d) for c, d in sorted(snapshot.items())]
                    last = snapshot
        return out
