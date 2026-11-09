import unittest

from qcta.data import DataBundle
from qcta.engine import Backtester
from qcta.types import Bar, EngineConfig


def flat(n, price=100.0, start_day=1):
    out = []
    for i in range(n):
        d = "2020-01-%02d" % (start_day + i)
        out.append(Bar(date=d, open=price, high=price, low=price, close=price, volume=1000, open_interest=1000))
    return out


def make_bundle(series_by_code):
    return DataBundle(series_by_code, "synthetic", [])


class TestEngineInvariants(unittest.TestCase):
    def _run(self, bundle, **cfg_kwargs):
        opts = dict(
            start_date="", end_date="", initial_capital=1_000_000.0,
            target_vol=0.10, atr_period=5, slip_ticks=1.0,
            max_leverage=2.0, max_drawdown_halt=0.9, max_symbol_risk=0.5,
            sleeves=({"name": "trend", "strategy": "trend", "alloc": 1.0, "kwargs": {"entry": 5, "exit": 3}},),
        )
        opts.update(cfg_kwargs)
        return Backtester(EngineConfig(**opts), bundle).run()

    def test_positions_all_closed_at_end(self):
        rb = flat(20)
        res = self._run(make_bundle({"rb": rb}))
        for s in res.sleeves:
            self.assertEqual(s.account.positions, {})
            self.assertEqual(s.account.curve[-1].gross_exposure, 0.0)

    def test_equity_identity_matches_trade_pnl(self):
        """恒等式：期末权益 - 初始资金 == 所有平仓交易的净盈亏之和。

        这条不成立就说明手续费重复计或漏计、或者盯市口径不一致。
        """
        bars = flat(30) + [Bar("2020-02-01", 120, 121, 119, 120, 1000, 1000)]
        bars += [Bar("2020-02-%02d" % (d + 2), 120, 120, 120, 120, 1000, 1000) for d in range(10)]
        res = self._run(make_bundle({"rb": bars}))
        equity_change = res.aggregate[-1].equity - res.cfg.initial_capital
        trade_pnl = sum(t.net_pnl for t in res.trades)
        self.assertAlmostEqual(equity_change, trade_pnl, delta=max(1.0, abs(trade_pnl) * 1e-6))

    def test_signal_fills_next_open_not_same_close(self):
        """突破发生在 t 日收盘，成交必须落在 t+1 开盘。"""
        bars = flat(20)
        # 第 21 根：收盘突破
        bars.append(Bar("2020-01-21", 100, 130, 99, 130, 1000, 1000))
        # 第 22 根：开盘价 140，是成交应该发生的价格
        bars.append(Bar("2020-01-22", 140, 141, 139, 140, 1000, 1000))
        res = self._run(make_bundle({"rb": bars}))
        opens = [f for f in res.fills if f.reason == "open"]
        self.assertTrue(opens, "应该至少有一笔开仓")
        first = opens[0]
        self.assertEqual(first.date, "2020-01-22")
        self.assertEqual(first.ref_price, 140)

    def test_slippage_is_adverse(self):
        bars = flat(20)
        bars.append(Bar("2020-01-21", 100, 130, 99, 130, 1000, 1000))
        bars.append(Bar("2020-01-22", 140, 141, 139, 140, 1000, 1000))
        res = self._run(make_bundle({"rb": bars}))
        first = [f for f in res.fills if f.reason == "open"][0]
        self.assertGreater(first.price, first.ref_price)          # 买在开盘价之上
        self.assertAlmostEqual(first.slippage_cost, (first.price - first.ref_price) * 10 * first.lots, places=6)

    def test_deterministic(self):
        bars = flat(30) + [Bar("2020-02-01", 120, 121, 119, 120, 1000, 1000)]
        a = self._run(make_bundle({"rb": bars}))
        b = self._run(make_bundle({"rb": bars}))
        self.assertEqual(a.metrics_final_equity() if hasattr(a, "metrics_final_equity") else a.aggregate[-1].equity,
                         b.aggregate[-1].equity)

    def test_drawdown_halt_stops_new_open(self):
        # 先稳定上涨（触发多头），随后连续暴跌（权益大幅回撤）→ 熔断应被置位
        up = [Bar("2020-01-%02d" % (i + 1), 100 + 2 * i, 101 + 2 * i, 99 + 2 * i, 100 + 2 * i, 1000, 1000)
              for i in range(20)]
        down = [Bar("2020-02-%02d" % (i + 1), 130 - 6 * i, 131 - 6 * i, 124 - 6 * i, 130 - 6 * i, 1000, 1000)
                for i in range(15)]
        res = self._run(make_bundle({"rb": up + down}), max_drawdown_halt=0.01)
        self.assertTrue(any(r.halted for r in res.aggregate))
        self.assertGreater(res.aggregate[-1].drawdown, 0.01)


if __name__ == "__main__":
    unittest.main()
