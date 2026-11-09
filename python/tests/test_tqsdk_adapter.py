"""TqSdk 适配器的兼容性测试（不联网、不装 tqsdk）。

用假的 tqsdk 模块注入 sys.modules，验证：
1. adj_type 从 'F' 开始试，被拒时逐级回退（3.x 只认 None/'F'/'B'，不认 'BFD'）
2. 纳秒时间戳能正确转成 YYYY-MM-DD，并过滤无效行
3. 全部失败时抛出可读的 TqSdkError
"""

import os
import sys
import types
import unittest

from qcta import data as dm


class _FakeRow(dict):
    pass


class _FakeSerial:
    def __init__(self, rows):
        self._rows = rows
        self.columns = ["datetime", "open", "high", "low", "close", "volume", "close_oi"]

    def __len__(self):
        return len(self._rows)

    def __iter__(self):
        return iter(self._rows)

    def iterrows(self):
        for r in self._rows:
            yield None, r


def _make_fake_tqsdk(accept_adj, rows):
    mod = types.ModuleType("tqsdk")

    class TqAuth:
        def __init__(self, user, password):
            self.user = user

    class TqApi:
        def __init__(self, auth=None):
            self.closed = False

        def get_kline_serial(self, symbol, duration, data_length=200, adj_type=None):
            if adj_type not in accept_adj:
                raise Exception("adj_type 参数只支持 None (不复权) ｜ 'F' (前复权) ｜ 'B' (后复权)")
            return _FakeSerial(rows)

        def wait_update(self, deadline=None):
            return True

        def close(self):
            self.closed = True

    mod.TqAuth = TqAuth
    mod.TqApi = TqApi
    return mod


DAILY_NS = 86400 * 10 ** 9
BASE_NS = 1704153600 * 10 ** 9  # 2024-01-02 00:00 UTC+8 附近


def rows(n=5):
    out = []
    for i in range(n):
        out.append(_FakeRow({
            "datetime": BASE_NS + i * DAILY_NS,
            "open": 3000.0 + i, "high": 3010.0 + i, "low": 2990.0 + i, "close": 3005.0 + i,
            "volume": 1000.0, "close_oi": 5000.0,
        }))
    return out


class TestTqSdkAdapter(unittest.TestCase):
    def setUp(self):
        os.environ["QCTA_TQ_USER"] = "13800000000"
        os.environ["QCTA_TQ_PASS"] = "x"
        self._saved = sys.modules.get("tqsdk")

    def tearDown(self):
        if self._saved is None:
            sys.modules.pop("tqsdk", None)
        else:
            sys.modules["tqsdk"] = self._saved
        os.environ.pop("QCTA_TQ_USER", None)
        os.environ.pop("QCTA_TQ_PASS", None)

    def test_prefers_front_adjusted(self):
        sys.modules["tqsdk"] = _make_fake_tqsdk({"F", "B", None}, rows())
        bars, adj = dm.fetch_tqsdk("rb")
        self.assertEqual(adj, "F", "应优先用前复权 'F'")
        self.assertEqual(len(bars), 5)
        self.assertEqual(bars[0].date, "2024-01-02")

    def test_falls_back_when_F_rejected(self):
        sys.modules["tqsdk"] = _make_fake_tqsdk({None}, rows())
        bars, adj = dm.fetch_tqsdk("rb")
        self.assertIsNone(adj, "全被拒时回退到不复权 None")
        self.assertEqual(len(bars), 5)

    def test_all_rejected_raises_readable_error(self):
        sys.modules["tqsdk"] = _make_fake_tqsdk(set(), rows())
        with self.assertRaises(dm.TqSdkError) as ctx:
            dm.fetch_tqsdk("rb")
        self.assertIn("adj_type", str(ctx.exception))

    def test_invalid_rows_are_skipped(self):
        bad = rows(3)
        bad[1]["close"] = float("nan")
        sys.modules["tqsdk"] = _make_fake_tqsdk({"F"}, bad)
        bars, _ = dm.fetch_tqsdk("rb")
        self.assertEqual(len(bars), 2, "NaN 行应被过滤")

    def test_missing_credentials_raises(self):
        os.environ.pop("QCTA_TQ_USER", None)
        os.environ.pop("QCTA_TQ_PASS", None)
        old = dm.credentials_path
        dm.credentials_path = lambda: os.path.join(os.sep, "nonexistent", "credentials.json")
        try:
            with self.assertRaises(dm.TqSdkError) as ctx:
                dm.fetch_tqsdk("rb")
            self.assertIn("快期账号", str(ctx.exception))
        finally:
            dm.credentials_path = old


if __name__ == "__main__":
    unittest.main()
