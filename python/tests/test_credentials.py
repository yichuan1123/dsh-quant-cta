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
        self.assertNotIn("13800001234", blob)

    def test_file_content_shape(self):
        path = dm.credentials_path()
        dm.save_credentials("13800001234", "s3cret-pass")
        with open(path, "r", encoding="utf-8") as fh:
            obj = json.load(fh)
        self.assertEqual(obj["tq_user"], "13800001234")
        self.assertEqual(obj["tq_pass"], "s3cret-pass")
        self.assertIn("saved_at", obj)

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

    def test_mask_user_short_input(self):
        self.assertEqual(dm.mask_user("123"), "***")
        self.assertEqual(dm.mask_user(""), "")
        self.assertTrue(dm.mask_user("13800001234").startswith("138"))


if __name__ == "__main__":
    unittest.main()
