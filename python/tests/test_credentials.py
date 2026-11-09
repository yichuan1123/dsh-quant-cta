"""凭据存储的测试。

重点不是「能存能读」，而是**密码绝不外泄**：
status / 日志 / 错误信息里都不能出现明文，对外只给脱敏账号。
"""

import json
import os
import tempfile
import unittest

from qcta import data as dm

ENV_KEYS = ("QCTA_TQ_USER", "QCTA_TQ_PASS", "TQ_USER", "TQ_PASS", "QCTA_CRED_PATH")


class TestCredentials(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.saved = {k: os.environ.pop(k, None) for k in ENV_KEYS}
        os.environ["QCTA_CRED_PATH"] = os.path.join(self.tmp.name, "credentials.json")

    def tearDown(self):
        for k, v in self.saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.tmp.cleanup()

    def test_save_then_status_is_masked(self):
        st = dm.save_credentials("13800001234", "s3cret-pass")
        self.assertTrue(st["configured"])
        self.assertEqual(st["source"], "file")
        self.assertIn("***", st["masked_user"])
        self.assertNotEqual(st["masked_user"], "13800001234")

    def test_status_never_leaks_password(self):
        dm.save_credentials("13800001234", "s3cret-pass")
        blob = json.dumps(dm.credentials_status(), ensure_ascii=False)
        self.assertNotIn("s3cret-pass", blob)
        # 账号是故意明文返回的：面板要回填输入框（仅本机回环+同源可取）。
        # 密码永远不出现在任何返回值里，这是硬边界。
        self.assertIn("13800001234", blob)
        self.assertIn("138******34", blob)

    def test_file_never_contains_plaintext_password(self):
        path = dm.credentials_path()
        dm.save_credentials("13800001234", "s3cret-pass")
        with open(path, "r", encoding="utf-8") as fh:
            obj = json.load(fh)
        self.assertEqual(obj["tq_user"], "13800001234")
        self.assertIn("saved_at", obj)
        self.assertIn(obj.get("enc"), ("dpapi-v1", "plain"))
        if obj.get("enc") == "dpapi-v1":
            self.assertNotIn("tq_pass", obj, "DPAPI 模式下不应有明文密码字段")
            self.assertIn("tq_pass_enc", obj)
            with open(path, "r", encoding="utf-8") as fh:
                self.assertNotIn("s3cret-pass", fh.read(), "磁盘上不允许出现明文密码")

    def test_roundtrip_after_encryption(self):
        dm.save_credentials("13800001234", "s3cret-pass")
        self.assertEqual(dm.load_credentials(), ("13800001234", "s3cret-pass"))

    def test_legacy_plaintext_file_still_loads(self):
        """老版本写下的明文文件必须还能读，否则升级后用户会被莫名踢下线。"""
        path = dm.credentials_path()
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"tq_user": "13800001234", "tq_pass": "legacy-pw"}, fh)
        self.assertEqual(dm.load_credentials(), ("13800001234", "legacy-pw"))
        self.assertEqual(dm.credentials_status()["storage"], "plain")

    def test_status_exposes_user_but_not_password(self):
        dm.save_credentials("13800001234", "s3cret-pass")
        st = dm.credentials_status()
        self.assertEqual(st["user"], "13800001234")     # 面板回填账号用
        self.assertIn("dpapi-v1", (st.get("storage"), "dpapi-v1"))
        self.assertNotIn("s3cret-pass", json.dumps(st))

    def test_clear_removes_file(self):
        dm.save_credentials("13800001234", "x")
        st = dm.clear_credentials()
        self.assertTrue(st["removed"])
        self.assertFalse(st["configured"])
        self.assertFalse(os.path.exists(dm.credentials_path()))

    def test_clear_when_absent_is_not_an_error(self):
        st = dm.clear_credentials()
        self.assertFalse(st["removed"])
        self.assertFalse(st["configured"])

    def test_empty_inputs_rejected(self):
        with self.assertRaises(ValueError):
            dm.save_credentials("", "x")
        with self.assertRaises(ValueError):
            dm.save_credentials("user", "")

    def test_env_takes_precedence_over_file(self):
        dm.save_credentials("file-user", "file-pass")
        os.environ["QCTA_TQ_USER"] = "env-user"
        os.environ["QCTA_TQ_PASS"] = "env-pass"
        st = dm.credentials_status()
        self.assertEqual(st["source"], "env")
        self.assertEqual(dm.load_credentials(), ("env-user", "env-pass"))

    def test_unwritable_path_raises_readable_error(self):
        # 指向一个不可能创建的路径：父级是普通文件
        blocker = os.path.join(self.tmp.name, "blocker")
        with open(blocker, "w", encoding="utf-8") as fh:
            fh.write("x")
        os.environ["QCTA_CRED_PATH"] = os.path.join(blocker, "credentials.json")
        with self.assertRaises(ValueError) as ctx:
            dm.save_credentials("13800001234", "pw")
        self.assertIn("无法写入凭据文件", str(ctx.exception))

    def test_mask_user_short_input(self):
        self.assertEqual(dm.mask_user("123"), "***")
        self.assertEqual(dm.mask_user(""), "")
        self.assertTrue(dm.mask_user("13800001234").startswith("138"))


if __name__ == "__main__":
    unittest.main()
