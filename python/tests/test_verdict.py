"""统计体检（verdict）测试：结论分档、渲染、多重检验惩罚。"""

import os
import random
import sys
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from qcta import verdict


def _equity_from_returns(rets, start=1_000_000.0):
    eq, out = start, [start]
    for r in rets:
        eq *= 1.0 + r
        out.append(eq)
    return out


def _trending(n=600, drift=0.0022, noise=0.0006, seed=3):
    rnd = random.Random(seed)
    rets = [drift + rnd.uniform(-noise, noise) for _ in range(n)]
    return _equity_from_returns(rets)


def _weak_edge(n=700, drift=0.0006, noise=0.008, seed=17):
    rnd = random.Random(seed)
    return _equity_from_returns([drift + rnd.uniform(-noise, noise) for _ in range(n)])


def _noise(n=600, seed=11):
    rnd = random.Random(seed)
    return _equity_from_returns([rnd.gauss(0.0, 0.006) for _ in range(n)])


def _dates(n):
    return ["2024-01-%03d" % (i + 1) for i in range(n)]


class AnalyzeTests(unittest.TestCase):
    def test_strong_trend_is_flagged_as_edge(self):
        eq = _trending()
        v = verdict.analyze(eq, _dates(len(eq)), [])
        self.assertEqual(v["label_level"], "ok", v["label"])
        self.assertGreater(v["sharpe_ci"]["lo"], 0)
        self.assertGreaterEqual(v["sharpe"]["psr"], 0.95)

    def test_pure_noise_is_indistinguishable(self):
        eq = _noise()
        v = verdict.analyze(eq, _dates(len(eq)), [])
        self.assertEqual(v["label_level"], "none", v["label"])
        self.assertLessEqual(v["sharpe_ci"]["lo"], 0.0)

    def test_steady_loss_is_flagged_bad(self):
        rnd = random.Random(5)
        rets = [-0.0022 + rnd.uniform(-0.0006, 0.0006) for _ in range(600)]
        eq = _equity_from_returns(rets)
        v = verdict.analyze(eq, _dates(len(eq)), [])
        self.assertEqual(v["label_level"], "bad", v["label"])
        self.assertLess(v["sharpe_ci"]["hi"], 0)

    def test_multiple_testing_punishes_dsr(self):
        # 弱边缘：单次看漂亮，试了 200 组参数后就不该还显著
        eq = _weak_edge()
        one = verdict.analyze(eq, _dates(len(eq)), [], n_trials=1)
        many = verdict.analyze(eq, _dates(len(eq)), [], n_trials=200)
        self.assertGreater(one["dsr"]["dsr"], many["dsr"]["dsr"])
        self.assertLess(many["dsr"]["dsr"], 0.95)

    def test_short_series_reports_error(self):
        eq = _trending(n=5)
        v = verdict.analyze(eq, _dates(len(eq)), [])
        self.assertIn("error", v)
        md = "\n".join(verdict.render_markdown(v))
        self.assertIn("样本不足", md)

    def test_win_rate_keys_only_with_trades(self):
        eq = _trending()
        no_trades = verdict.analyze(eq, _dates(len(eq)), [])
        self.assertNotIn("win_rate_bayes", no_trades)
        trades = [SimpleNamespace(net_pnl=10.0)] * 30 + [SimpleNamespace(net_pnl=-10.0)] * 10
        v = verdict.analyze(eq, _dates(len(eq)), trades)
        self.assertAlmostEqual(v["win_rate_bayes"]["posterior_mean"], 31.0 / 42.0, places=9)
        self.assertGreater(v["prob_edge"], 0.5)

    def test_keys_present(self):
        eq = _trending()
        v = verdict.analyze(eq, _dates(len(eq)), [])
        for k in ("sharpe", "mintrl", "dsr", "sharpe_ci", "cagr_ci", "t_test",
                  "wilcoxon", "ljung_box", "autocorr1", "adf_equity", "hurst",
                  "var", "garch", "underwater", "label"):
            self.assertIn(k, v)


class RenderTests(unittest.TestCase):
    def setUp(self):
        eq = _trending()
        self.v = verdict.analyze(eq, _dates(len(eq)), [], n_trials=7)
        self.md = "\n".join(verdict.render_markdown(self.v, n_trials=7))

    def test_no_unformatted_placeholder(self):
        self.assertNotIn("%%", self.md)
        self.assertNotIn("%d", self.md)
        self.assertNotIn("%s", self.md)

    def test_has_expected_sections(self):
        for token in ("## 统计体检", "**结论", "PSR", "DSR", "MinTRL", "VaR", "GARCH",
                      "风险结构", "免责" if False else "统计体检只回答"):
            self.assertIn(token, self.md)

    def test_trials_shown(self):
        self.assertIn("N=7", self.md)

    def test_days_formatting(self):
        self.assertEqual(verdict._days(None), "∞（不可达）")
        self.assertEqual(verdict._days(120), "120 天")
        self.assertIn("年", verdict._days(1000))
        self.assertIn("> 100 年", verdict._days(250 * 500))

    def test_lb_note_uses_magnitude(self):
        self.assertIn("大样本", verdict._lb_note(0.0, 0.008))
        self.assertIn("可交易量级", verdict._lb_note(0.0, 0.30))
        self.assertIn("白噪声", verdict._lb_note(0.4, 0.30))


if __name__ == "__main__":
    unittest.main()
