import unittest

from qcta.data import DataBundle, synthetic_bars
from qcta.engine import Backtester
from qcta.metrics import compute_metrics
from qcta.report import render_markdown, render_svg
from qcta.types import EngineConfig


class TestReport(unittest.TestCase):
    def _result(self):
        bundle = DataBundle({"rb": synthetic_bars("rb", "2020-01-01", "2021-12-31")}, "synthetic",
                            ["警告：本次结果包含合成数据"])
        cfg = EngineConfig(start_date="", end_date="", initial_capital=500_000.0,
                           sleeves=({"name": "trend", "strategy": "trend", "alloc": 1.0,
                                     "kwargs": {"entry": 20, "exit": 10}},))
        res = Backtester(cfg, bundle).run()
        res.metrics = compute_metrics(res.aggregate, res.trades, cfg.initial_capital, 0.0, res.fills)
        return res

    def test_markdown_has_core_sections(self):
        md = render_markdown(self._result(), {"data_source": "synthetic"})
        for key in ("核心指标", "成本分解", "策略腿归因", "品种归因", "月度收益矩阵", "口径说明", "免责"):
            self.assertIn(key, md)
        self.assertIn("synthetic", md)

    def test_svg_is_wellformed(self):
        svg = render_svg(self._result())
        self.assertTrue(svg.startswith("<svg"))
        self.assertTrue(svg.endswith("</svg>"))
        self.assertIn("<polyline", svg)

    def test_synthetic_warning_present(self):
        res = self._result()
        md = render_markdown(res, {"data_source": "synthetic"})
        self.assertIn("合成数据", md)


if __name__ == "__main__":
    unittest.main()
