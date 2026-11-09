"""滚动模型与「最优配比接入引擎」的测试。

要守住的三件事：
1. 权重永远合法（和为 1、非负、不超上限）
2. 筛选真的生效（历史不够、板块被排除的品种不会进池）
3. 引擎在 optimizer 模式下真的按模型权重分配风险，且是 walk-forward
"""

import json
import os
import tempfile
import unittest

from qcta.data import DataBundle, synthetic_bars
from qcta.engine import Backtester
from qcta.model import RollingModel, load_state, save_state
from qcta.types import EngineConfig
from qcta.universe import select_universe


def bundle(codes, start="2019-01-01", end="2022-12-31"):
    return DataBundle({c: synthetic_bars(c, start, end) for c in codes}, "synthetic", ["测试用合成数据"])


class TestUniverse(unittest.TestCase):
    def test_drops_short_history(self):
        bars = {
            "rb": synthetic_bars("rb", "2021-01-01", "2022-12-31"),
            "cu": synthetic_bars("cu", "2022-12-01", "2022-12-31"),   # 太短
        }
        kept, report = select_universe(bars, min_bars=250)
        self.assertIn("rb", kept)
        self.assertNotIn("cu", kept)
        self.assertTrue(any(d["code"] == "cu" and "history" in d["reason"] for d in report["dropped"]))

    def test_drops_missing(self):
        kept, report = select_universe({"rb": synthetic_bars("rb", "2019-01-01", "2022-12-31")}, min_bars=100)
        self.assertIn("rb", kept)
        self.assertEqual(report["requested"], 28)   # 默认全盘

    def test_exclude_sector(self):
        all_bars = {c: synthetic_bars(c, "2019-01-01", "2022-12-31") for c in ("rb", "IF")}
        kept, _ = select_universe(all_bars, min_bars=100, exclude_sectors=("金融",))
        self.assertIn("rb", kept)
        self.assertNotIn("IF", kept)


class TestRollingModel(unittest.TestCase):
    def setUp(self):
        self.b = bundle(["rb", "cu", "m", "TA", "au"])

    def test_weights_are_valid(self):
        model = RollingModel(method="risk_parity", lookback=120, min_bars=120, max_weight=0.4)
        st = model.update(self.b.bars_by_code)
        w = st["weights"]
        self.assertTrue(w)
        self.assertAlmostEqual(sum(w.values()), 1.0, places=6)
        self.assertTrue(all(v >= 0 for v in w.values()))
        self.assertTrue(all(v <= 0.4 + 1e-9 for v in w.values()))

    def test_version_increments(self):
        model = RollingModel(lookback=120, min_bars=120)
        a = model.update(self.b.bars_by_code)
        b = model.update(self.b.bars_by_code)
        self.assertEqual(b["version"], a["version"] + 1)

    def test_deterministic(self):
        m1 = RollingModel(lookback=120, min_bars=120)
        m2 = RollingModel(lookback=120, min_bars=120)
        self.assertEqual(m1.update(self.b.bars_by_code)["weights"],
                         m2.update(self.b.bars_by_code)["weights"])

    def test_asof_cuts_history(self):
        """asof 必须真的截断历史：给一个早于 min_bars 的日期应当算不出池子。"""
        model = RollingModel(lookback=120, min_bars=120)
        st = model.update(self.b.bars_by_code, asof="2019-02-01")   # 只有 ~20 个交易日
        self.assertEqual(st["asof"], "2019-02-01")
        self.assertEqual(st["weights"], {})
        self.assertIn("error", st["diagnostics"])

    def test_asof_late_is_valid(self):
        model = RollingModel(lookback=120, min_bars=120)
        st = model.update(self.b.bars_by_code, asof="2022-06-30")
        self.assertAlmostEqual(sum(st["weights"].values()), 1.0, places=6)
        self.assertLessEqual(st["diagnostics"]["observations"], 120)

    def test_methods_all_produce_weights(self):
        for method in ("equal_weight", "inverse_vol", "risk_parity", "min_variance"):
            with self.subTest(method=method):
                model = RollingModel(method=method, lookback=120, min_bars=120)
                w = model.update(self.b.bars_by_code)["weights"]
                self.assertAlmostEqual(sum(w.values()), 1.0, places=6)

    def test_empty_universe_reports_error(self):
        model = RollingModel(lookback=200, min_bars=100000)
        st = model.update(self.b.bars_by_code)
        self.assertEqual(st["weights"], {})
        self.assertIn("error", st["diagnostics"])

    def test_state_roundtrip_with_history(self):
        model = RollingModel(lookback=120, min_bars=120)
        st = model.update(self.b.bars_by_code)
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "state", "model.json")
            save_state(path, st)
            save_state(path, model.update(self.b.bars_by_code))
            loaded = load_state(path)
            self.assertIsNotNone(loaded)
            self.assertEqual(len(loaded["history"]), 2)
            self.assertEqual(loaded["method"], st["method"])


class TestEngineOptimizerSizing(unittest.TestCase):
    def _cfg(self, sizing):
        return EngineConfig(
            start_date="2020-01-01", end_date="2021-12-31", initial_capital=1_000_000.0,
            target_vol=0.15, atr_period=10, slip_ticks=1.0, max_leverage=2.0,
            max_drawdown_halt=0.9, max_symbol_risk=0.5,
            sleeves=({"name": "trend", "strategy": "trend", "alloc": 1.0,
                      "kwargs": {"entry": 20, "exit": 10}},),
            sizing=sizing, model_method="risk_parity", model_lookback=120,
            model_min_bars=120, model_rebalance=20, model_max_weight=0.5, model_sector_cap=1.0,
        )

    def test_optimizer_mode_records_model_history(self):
        b = bundle(["rb", "cu", "m", "TA", "au"])
        res = Backtester(self._cfg("optimizer"), b).run()
        self.assertGreater(len(res.model_history), 0)
        self.assertTrue(res.model_history[0]["weights"])

    def test_vol_parity_mode_has_no_model(self):
        b = bundle(["rb", "cu"])
        res = Backtester(self._cfg("vol_parity"), b).run()
        self.assertEqual(res.model_history, [])

    def test_optimizer_is_deterministic(self):
        b = bundle(["rb", "cu", "m"])
        a = Backtester(self._cfg("optimizer"), b).run()
        c = Backtester(self._cfg("optimizer"), b).run()
        self.assertEqual(a.aggregate[-1].equity, c.aggregate[-1].equity)
        self.assertEqual(a.model_history[-1]["weights"], c.model_history[-1]["weights"])

    def test_zero_weight_symbol_is_not_traded(self):
        # min_bars 设到只有部分品种能进池：没进池的品种不该有持仓
        b = bundle(["rb", "cu"])
        cfg = self._cfg("optimizer")
        cfg.model_min_bars = 10_000           # 谁都进不了池
        res = Backtester(cfg, b).run()
        self.assertEqual(res.model_history[0]["weights"], {})
        for s in res.sleeves:
            self.assertEqual(s.account.trades, [])


if __name__ == "__main__":
    unittest.main()
