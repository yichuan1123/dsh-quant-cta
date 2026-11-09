"""最优配比求解器的测试。

数学正确性靠「定义」验：ERC 的定义就是风险贡献相等，最小方差的定义
就是组合方差最小——直接测这些性质，而不是测某个实现细节的数值。
"""

import math
import unittest

from qcta.optimizer import (annualized_vol, apply_sector_caps, mean_variance,
                            min_variance, optimize, project_capped, risk_parity,
                            sample_cov, shrink_cov)


def diag_cov(vars_):
    n = len(vars_)
    return [[vars_[i] if i == j else 0.0 for j in range(n)] for i in range(n)]


def corr_to_cov(vols, corr):
    n = len(vols)
    return [[vols[i] * vols[j] * corr[i][j] for j in range(n)] for i in range(n)]


class TestBasicStats(unittest.TestCase):
    def test_sample_cov_two_assets(self):
        a = [0.01, -0.01, 0.02, 0.0]
        b = [0.02, -0.02, 0.04, 0.0]
        cov, t = sample_cov([a, b])
        self.assertEqual(t, 4)
        # b = 2a：协方差 = 2·var(a)，所以比值是 2（方差是 4 倍）
        self.assertAlmostEqual(cov[0][1] / cov[0][0], 2.0, places=6)
        self.assertAlmostEqual(cov[1][1] / cov[0][0], 4.0, places=6)
        self.assertAlmostEqual(cov[0][0], 0.00016666666666666666, places=12)

    def test_shrink_moves_toward_diagonal(self):
        a = [0.01, -0.01, 0.02, 0.0, 0.005]
        b = [0.02, -0.02, 0.04, 0.0, 0.01]
        cov, t = sample_cov([a, b])
        shrunk = shrink_cov(cov, [a, b], t)
        # 收缩后相关性绝对值必须变小（向对角靠）
        before = abs(cov[0][1]) / math.sqrt(cov[0][0] * cov[1][1])
        after = abs(shrunk[0][1]) / math.sqrt(shrunk[0][0] * shrunk[1][1])
        self.assertLessEqual(after, before + 1e-12)

    def test_shrink_singular_matrix_stays_invertible_ish(self):
        n, t = 6, 3
        matrix = [[0.001 * ((i * 7 + k * 3) % 5) for k in range(t)] for i in range(n)]
        cov, obs = sample_cov(matrix)
        shrunk = shrink_cov(cov, matrix, obs)
        for i in range(n):
            self.assertGreater(shrunk[i][i], 0.0)


class TestProjection(unittest.TestCase):
    def test_sums_to_one(self):
        w = project_capped([3.0, 1.0, 1.0, 1.0], 1.0)
        self.assertAlmostEqual(sum(w), 1.0, places=10)

    def test_non_negative(self):
        w = project_capped([-5.0, 1.0, 2.0], 1.0)
        self.assertTrue(all(x >= 0 for x in w))

    def test_cap_respected(self):
        w = project_capped([50.0, 1.0, 1.0, 1.0], 0.4)
        self.assertAlmostEqual(sum(w), 1.0, places=10)
        self.assertTrue(all(x <= 0.4 + 1e-9 for x in w))

    def test_infeasible_cap_falls_back_to_equal(self):
        # cap·n < 1 时不可能满足，退化为等权
        w = project_capped([1.0, 1.0, 1.0], 0.1)
        self.assertAlmostEqual(sum(w), 1.0, places=10)

    def test_sector_cap(self):
        w = [0.5, 0.3, 0.2]
        sectors = ["black", "black", "agri"]
        out = apply_sector_caps(w, sectors, 0.6)
        self.assertAlmostEqual(sum(out), 1.0, places=9)
        self.assertLessEqual(out[0] + out[1], 0.6 + 1e-9)


class TestRiskParity(unittest.TestCase):
    def test_risk_contributions_equal(self):
        vols = [0.012, 0.02, 0.008]
        corr = [[1.0, 0.3, 0.1], [0.3, 1.0, 0.2], [0.1, 0.2, 1.0]]
        cov = corr_to_cov(vols, corr)
        w = risk_parity(cov)
        self.assertAlmostEqual(sum(w), 1.0, places=9)
        rc = [w[i] * sum(cov[i][j] * w[j] for j in range(3)) for i in range(3)]
        total = sum(rc)
        shares = [x / total for x in rc]
        for s in shares:
            self.assertAlmostEqual(s, 1.0 / 3.0, places=6)

    def test_diagonal_cov_matches_inverse_vol(self):
        cov = diag_cov([0.0001, 0.0004, 0.0016])   # 波动 1% / 2% / 4%
        w = risk_parity(cov)
        # 等风险下权重的反比就是波动反比
        inv = [1 / math.sqrt(cov[i][i]) for i in range(3)]
        s = sum(inv)
        for i in range(3):
            self.assertAlmostEqual(w[i], inv[i] / s, places=6)

    def test_two_assets_analytic(self):
        # 两资产、零相关：w1/w2 = σ2/σ1
        cov = diag_cov([0.0004, 0.0001])
        w = risk_parity(cov)
        self.assertAlmostEqual(w[0] / w[1], 0.5, places=6)


class TestMinVariance(unittest.TestCase):
    def test_beats_equal_weight(self):
        vols = [0.03, 0.01, 0.02]
        corr = [[1.0, 0.2, 0.1], [0.2, 1.0, 0.3], [0.1, 0.3, 1.0]]
        cov = corr_to_cov(vols, corr)
        w_mv = min_variance(cov)
        w_eq = [1 / 3, 1 / 3, 1 / 3]
        self.assertLessEqual(annualized_vol(cov, w_mv), annualized_vol(cov, w_eq) + 1e-9)

    def test_weights_are_valid(self):
        cov = diag_cov([0.0001, 0.0009, 0.0004])
        w = min_variance(cov)
        self.assertAlmostEqual(sum(w), 1.0, places=6)
        self.assertTrue(all(x >= 0 for x in w))


class TestMeanVariance(unittest.TestCase):
    def test_prefers_higher_return(self):
        cov = diag_cov([0.0001, 0.0001])
        w = mean_variance(cov, [0.001, 0.0001], risk_aversion=50.0)
        self.assertGreater(w[0], w[1])


class TestOptimizeEntry(unittest.TestCase):
    def _matrix(self, n, t, seed_shift=0):
        out = []
        for i in range(n):
            out.append([0.001 * math.sin((k + i * 3 + seed_shift) * 0.7) for k in range(t)])
        return out

    def test_weights_sum_to_one_and_respect_cap(self):
        codes = ["rb", "cu", "m", "TA", "au"]
        sectors = ["黑色", "有色", "农产品", "能化", "贵金属"]
        matrix = self._matrix(len(codes), 60)
        for method in ("equal_weight", "inverse_vol", "risk_parity", "min_variance"):
            with self.subTest(method=method):
                res = optimize(method, matrix, codes, sectors, max_weight=0.3, sector_cap=0.5)
                w = res["weights"]
                self.assertAlmostEqual(sum(w.values()), 1.0, places=6)
                self.assertTrue(all(v <= 0.3 + 1e-9 for v in w.values()))

    def test_single_symbol(self):
        res = optimize("risk_parity", [[0.01, 0.02]], ["rb"], ["黑色"])
        self.assertEqual(res["weights"], {"rb": 1.0})

    def test_empty(self):
        res = optimize("risk_parity", [], [], [])
        self.assertEqual(res["weights"], {})

    def test_deterministic(self):
        codes = ["rb", "cu", "m", "TA"]
        matrix = self._matrix(4, 50)
        a = optimize("risk_parity", matrix, codes, ["a", "b", "c", "d"])
        b = optimize("risk_parity", matrix, codes, ["a", "b", "c", "d"])
        self.assertEqual(a["weights"], b["weights"])

    def test_unknown_method_raises(self):
        with self.assertRaises(ValueError):
            optimize("bogus", [[0.01]], ["rb"], ["黑色"])

    def test_diagnostics_present(self):
        res = optimize("risk_parity", self._matrix(3, 40), ["rb", "cu", "m"], ["b", "c", "a"])
        d = res["diagnostics"]
        for key in ("method", "n", "ann_vol_pct", "hhi", "effective_n", "risk_contributions"):
            self.assertIn(key, d)


if __name__ == "__main__":
    unittest.main()
