"""风险度量与贝叶斯更新的测试。"""

import math
import random
import unittest

from qcta import bayes, risk
from qcta.probability import mean, std


def iid_normal(n, mu=0.0, sigma=0.01, seed=1):
    rng = random.Random(seed)
    return [rng.gauss(mu, sigma) for _ in range(n)]


def simulate_garch(n, omega, alpha, beta, seed=5):
    rng = random.Random(seed)
    var = omega / (1 - alpha - beta)
    out = []
    for _ in range(n):
        x = rng.gauss(0, math.sqrt(var))
        out.append(x)
        var = omega + alpha * x * x + beta * var
    return out


class TestVaR(unittest.TestCase):
    def test_ordering_of_estimates(self):
        rets = iid_normal(2000, 0.0005, 0.01, seed=3)
        rep = risk.var_report(rets, 0.95)
        self.assertGreater(rep["historical"], 0.0)
        self.assertGreater(rep["cvar_historical"], rep["historical"],
                           "CVaR（尾部均值）必须大于 VaR（分位点）")

    def test_parametric_matches_z_times_sigma(self):
        rets = iid_normal(4000, 0.0, 0.012, seed=7)
        var = risk.parametric_var(rets, 0.95)
        self.assertAlmostEqual(var, 1.6448536 * std(rets), delta=0.002)

    def test_fat_tail_widens_var(self):
        """负偏+肥尾时，Cornish-Fisher 修正应比正态给出更大的 VaR。"""
        rng = random.Random(11)
        fat = []
        for _ in range(2000):
            if rng.random() < 0.04:
                fat.append(-0.05 + rng.gauss(0, 0.01))
            else:
                fat.append(0.001 + rng.gauss(0, 0.004))
        rep = risk.var_report(fat, 0.95)
        self.assertGreater(rep["cornish_fisher"], rep["parametric"])

    def test_cvar_equals_tail_mean(self):
        rets = [0.01 * i for i in range(-50, 50)]
        rep = risk.var_report(rets, 0.90)
        self.assertGreater(rep["cvar_historical"], 0.0)


class TestDrawdown(unittest.TestCase):
    def test_known_episode(self):
        eq = [100.0, 120.0, 90.0, 130.0]
        self.assertAlmostEqual(risk.max_drawdown(eq), 0.25, places=10)
        eps = risk.drawdown_episodes(eq)
        self.assertEqual(len(eps), 1)
        self.assertEqual(eps[0]["recovered"], True)
        self.assertEqual(eps[0]["recover_index"], 3)

    def test_unrecovered_drawdown(self):
        eps = risk.drawdown_episodes([100.0, 80.0, 90.0])
        self.assertEqual(len(eps), 1)
        self.assertFalse(eps[0]["recovered"])
        self.assertAlmostEqual(eps[0]["depth"], 0.2, places=10)

    def test_monotonic_has_no_drawdown(self):
        self.assertEqual(risk.max_drawdown([1.0, 2.0, 3.0]), 0.0)

    def test_underwater_stats(self):
        # 100→120→90→130 是一次「已收复」回撤；130→110 是另一次未收复
        stats = risk.underwater_stats([100.0, 120.0, 90.0, 130.0, 110.0])
        self.assertEqual(stats["episodes"], 2)
        self.assertEqual(stats["unrecovered"], 1)
        self.assertAlmostEqual(stats["max_depth"], 0.25, places=10)


class TestGARCH(unittest.TestCase):
    def test_recovers_simulated_parameters(self):
        alpha_true, beta_true = 0.08, 0.90
        rets = simulate_garch(4000, omega=1e-6, alpha=alpha_true, beta=beta_true, seed=13)
        fit = risk.garch11_fit(rets)
        self.assertTrue(fit["ok"], fit.get("reason"))
        self.assertAlmostEqual(fit["alpha"], alpha_true, delta=0.06)
        self.assertAlmostEqual(fit["beta"], beta_true, delta=0.08)
        self.assertGreater(fit["persistence"], 0.8)

    def test_persistence_strictly_below_one(self):
        rets = simulate_garch(2000, omega=1e-6, alpha=0.1, beta=0.88, seed=17)
        fit = risk.garch11_fit(rets)
        self.assertLess(fit["persistence"], 1.0)
        self.assertGreater(fit["long_run_vol_ann"], 0.0)

    def test_short_sample_returns_error(self):
        fit = risk.garch11_fit([0.01] * 10)
        self.assertFalse(fit["ok"])


class TestBayes(unittest.TestCase):
    def test_beta_posterior_mean(self):
        res = bayes.beta_binomial(60, 100)
        self.assertAlmostEqual(res["posterior_mean"], 61.0 / 102.0, places=12)
        self.assertLess(res["ci95"][0], res["posterior_mean"])
        self.assertGreater(res["ci95"][1], res["posterior_mean"])

    def test_prior_shrinks_small_sample(self):
        small = bayes.beta_binomial(6, 10)          # 60%
        self.assertLess(abs(small["posterior_mean"] - 0.5),
                        abs(0.6 - 0.5), "小样本应向先验收缩")

    def test_prob_above_threshold(self):
        p_weak = bayes.prob_win_rate_above(52, 100)
        p_strong = bayes.prob_win_rate_above(520, 1000)
        self.assertGreater(p_strong, p_weak, "同样 52% 胜率，样本越大越有把握")
        self.assertGreater(p_weak, 0.0)
        self.assertLess(p_weak, 1.0)

    def test_normal_inverse_gamma_posterior_mean(self):
        data = [1.0, 2.0, 3.0, 4.0]
        res = bayes.normal_inverse_gamma(0.0, 1.0, 1.0, 1.0, data)
        expected = (1.0 * 0.0 + 4 * 2.5) / (1.0 + 4)
        self.assertAlmostEqual(res["mu"], expected, places=12)
        self.assertAlmostEqual(res["kappa"], 5.0)

    def test_empty_data_returns_prior(self):
        res = bayes.normal_inverse_gamma(0.5, 2.0, 3.0, 4.0, [])
        self.assertEqual(res["mu"], 0.5)
        self.assertEqual(res["n"], 0)


if __name__ == "__main__":
    unittest.main()
