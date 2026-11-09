"""显著性检验的测试。

重点验证三件事：
1. PSR 的公式实现正确（对照手算的 z）
2. 样本量越大、试错次数越多，判定越严格（方向必须对）
3. 肥尾/负偏应该**降低**置信度（这正是 PSR 比裸 t 检验强的地方）
"""

import math
import random
import unittest

from qcta import significance as S
from qcta.probability import kurtosis, norm_cdf, norm_ppf, skewness, std, mean


def iid_normal(n, mu, sigma, seed=1):
    rng = random.Random(seed)
    return [rng.gauss(mu, sigma) for _ in range(n)]


def fat_tail(n, seed=2):
    """负偏 + 肥尾：小概率大亏。"""
    rng = random.Random(seed)
    out = []
    for _ in range(n):
        if rng.random() < 0.03:
            out.append(-0.06 + rng.gauss(0, 0.005))
        else:
            out.append(0.0035 + rng.gauss(0, 0.004))
    return out


class TestSharpe(unittest.TestCase):
    def test_per_period_and_annualized(self):
        rets = [0.01, 0.02, -0.01, 0.015, 0.005]
        sr = S.sharpe_per_period(rets)
        self.assertAlmostEqual(sr, mean(rets) / std(rets))
        self.assertAlmostEqual(S.annualized_sharpe(rets), sr * math.sqrt(252))

    def test_zero_vol_safe(self):
        self.assertEqual(S.sharpe_per_period([0.01] * 10), 0.0)


class TestPSR(unittest.TestCase):
    def test_matches_manual_formula(self):
        rets = iid_normal(500, 0.001, 0.01, seed=11)
        res = S.psr(rets)
        sr = mean(rets) / std(rets)
        g3 = skewness(rets)
        g4 = kurtosis(rets)
        z = sr * math.sqrt(len(rets) - 1) / math.sqrt(1 - g3 * sr + (g4 - 1) / 4 * sr * sr)
        self.assertAlmostEqual(res["z"], z, places=9)
        self.assertAlmostEqual(res["psr"], norm_cdf(z), places=9)

    def test_more_data_raises_confidence(self):
        small = S.psr(iid_normal(250, 0.001, 0.01, seed=3))
        big = S.psr(iid_normal(2000, 0.001, 0.01, seed=3))
        self.assertGreater(big["psr"], small["psr"], "同样的夏普，样本越长越可信")

    def test_negative_mean_gives_low_probability(self):
        res = S.psr(iid_normal(500, -0.001, 0.01, seed=5))
        self.assertLess(res["psr"], 0.1)

    def test_fat_tail_lowers_confidence(self):
        """同样的正夏普，负偏+肥尾必须给出更低的 PSR。"""
        normal = S.psr(iid_normal(500, 0.0012, 0.008, seed=7))
        fat = S.psr(fat_tail(500, seed=7))
        self.assertGreater(normal["psr"], fat["psr"])
        self.assertGreater(fat["denom"], 1.0, "肥尾应让分母 > 1（惩罚）")

    def test_short_sample_returns_neutral(self):
        self.assertEqual(S.psr([0.01, 0.02])["psr"], 0.5)


class TestDSR(unittest.TestCase):
    def test_single_trial_equals_psr(self):
        rets = iid_normal(600, 0.001, 0.01, seed=9)
        self.assertAlmostEqual(S.deflated_sharpe(rets, 1)["dsr"], S.psr(rets)["psr"], places=10)

    def test_more_trials_lower_dsr(self):
        rets = iid_normal(600, 0.001, 0.01, seed=9)
        d10 = S.deflated_sharpe(rets, 10)["dsr"]
        d1000 = S.deflated_sharpe(rets, 1000)["dsr"]
        self.assertGreater(d10, d1000, "试得越多，同样的夏普越不可信")

    def test_expected_max_sharpe_grows_with_trials(self):
        a = S.expected_max_sharpe(10, 0.0004)
        b = S.expected_max_sharpe(1000, 0.0004)
        self.assertGreater(b, a)
        self.assertGreater(a, 0.0)
        self.assertEqual(S.expected_max_sharpe(1, 0.0004), 0.0)


class TestMinTRL(unittest.TestCase):
    def test_strong_sharpe_needs_less_data(self):
        strong = S.min_track_record_length(iid_normal(2000, 0.004, 0.01, seed=13))
        weak = S.min_track_record_length(iid_normal(2000, 0.0004, 0.01, seed=13))
        self.assertLess(strong, weak, "夏普越高，所需样本越短")

    def test_negative_sharpe_is_infinite(self):
        self.assertEqual(S.min_track_record_length(iid_normal(500, -0.002, 0.01, seed=15)),
                         float("inf"))

    def test_formula_is_self_consistent(self):
        """把 n_min 代回 PSR 公式（矩保持为全样本估计），应恰好得到设定置信度。

        注意不能「截断样本再重算」——那样 SR 本身也变了，测的就不是同一个命题。
        """
        rets = iid_normal(2000, 0.003, 0.01, seed=17)
        n_min = S.min_track_record_length(rets, confidence=0.95)
        sr = S.sharpe_per_period(rets)
        g3, g4 = skewness(rets), kurtosis(rets)
        z = sr * math.sqrt(n_min - 1) / math.sqrt(1 - g3 * sr + (g4 - 1) / 4 * sr * sr)
        self.assertAlmostEqual(norm_cdf(z), 0.95, delta=0.01)


class TestBootstrapCI(unittest.TestCase):
    def test_ci_brackets_point_estimate(self):
        rets = iid_normal(600, 0.0008, 0.01, seed=19)
        ci = S.bootstrap_sharpe_ci(rets, n_boot=200, seed=1)
        self.assertLess(ci["lo"], ci["point"])
        self.assertGreater(ci["hi"], ci["point"])
        self.assertGreater(ci["hi"] - ci["lo"], 0.0)

    def test_short_sample_degrades_gracefully(self):
        ci = S.bootstrap_sharpe_ci([0.01] * 5)
        self.assertEqual(ci["n_boot"], 0)

    def test_cagr_ci_brackets_point(self):
        eq = [1.0]
        for i in range(400):
            eq.append(eq[-1] * (1.0 + 0.0005 + (0.01 if i % 2 else -0.009)))
        ci = S.bootstrap_cagr_ci(eq, n_boot=150, seed=2)
        self.assertLess(ci["lo"], ci["point"])
        self.assertGreater(ci["hi"], ci["point"])


class TestHypothesisTests(unittest.TestCase):
    def test_t_test_zero_mean_high_p(self):
        rets = iid_normal(400, 0.0, 0.01, seed=21)
        self.assertGreater(S.t_test_mean(rets)["p"], 0.05)

    def test_t_test_strong_mean_low_p(self):
        # 确定性构造：均值恰好 0.002、标准差恰好 0.0005 → t = 80
        # （不用随机样本：seed 抽到偏离 2σ 时测试会给出误导性的失败）
        rets = [0.002 + (0.0005 if i % 2 else -0.0005) for i in range(400)]
        self.assertLess(S.t_test_mean(rets)["p"], 1e-12)

    def test_wilcoxon_detects_shift(self):
        # 同样用确定性构造：全部为正 → 符号秩检验必然极显著
        rets = [0.001 + (0.0005 if i % 2 else -0.0002) for i in range(400)]
        self.assertLess(S.wilcoxon_signed_rank(rets)["p"], 1e-6)

    def test_wilcoxon_symmetric_high_p(self):
        rets = iid_normal(400, 0.0, 0.01, seed=27)
        self.assertGreater(S.wilcoxon_signed_rank(rets)["p"], 0.05)


class TestMultipleTesting(unittest.TestCase):
    def test_bh_passes_small_pvalues(self):
        res = S.benjamini_hochberg([0.001, 0.02, 0.5], q=0.05)
        self.assertEqual(res["pass"], [True, True, False])

    def test_bh_all_high_p_fails(self):
        res = S.benjamini_hochberg([0.4, 0.6, 0.9], q=0.05)
        self.assertEqual(res["pass"], [False, False, False])

    def test_bh_empty(self):
        self.assertEqual(S.benjamini_hochberg([])["m"], 0)


if __name__ == "__main__":
    unittest.main()
