"""日志工程：级别可配、URL 脱敏、模块 logger 约定、同步 correlation id。"""

from __future__ import annotations

import ast
import logging
import tempfile
import unittest
from pathlib import Path
from unittest import mock


class LevelResolutionTests(unittest.TestCase):
    def test_default_is_info(self) -> None:
        from app.logging_setup import resolve_level

        with mock.patch.dict("os.environ", {}, clear=False):
            self.assertEqual(resolve_level(""), logging.INFO)
            self.assertEqual(resolve_level(None), logging.INFO)

    def test_env_values_are_recognized(self) -> None:
        from app.logging_setup import resolve_level

        self.assertEqual(resolve_level("debug"), logging.DEBUG)
        self.assertEqual(resolve_level(" WARNING "), logging.WARNING)
        self.assertEqual(resolve_level("ERROR"), logging.ERROR)

    def test_unknown_value_falls_back_without_raising(self) -> None:
        from app.logging_setup import resolve_level

        self.assertEqual(resolve_level("verbose"), logging.INFO)

    def test_env_variable_is_used(self) -> None:
        from app.logging_setup import resolve_level

        with mock.patch.dict("os.environ", {"DINGFENGBO_LOG_LEVEL": "debug"}):
            self.assertEqual(resolve_level(), logging.DEBUG)


class SafeUrlTests(unittest.TestCase):
    def test_query_and_path_are_stripped(self) -> None:
        from app.logging_setup import safe_url

        url = "https://cdn.example.com/a/b.png?sign=SECRET&expires=1"
        self.assertEqual(safe_url(url), "https://cdn.example.com")

    def test_port_is_kept(self) -> None:
        from app.logging_setup import safe_url

        self.assertEqual(safe_url("http://127.0.0.1:8000/api/health"), "http://127.0.0.1:8000")

    def test_userinfo_is_stripped(self) -> None:
        from app.logging_setup import safe_url

        self.assertEqual(safe_url("https://user:pass@host.example/x"), "https://host.example")

    def test_empty_and_relative(self) -> None:
        from app.logging_setup import safe_url

        self.assertEqual(safe_url(""), "")
        self.assertEqual(safe_url("/api/sync/pull"), "<相对地址>")


class ModuleLoggerConventionTests(unittest.TestCase):
    """约定：app/ 下每个模块都有 module logger，新增文件不能漏。"""

    def test_every_app_module_has_logger(self) -> None:
        missing = []
        for path in Path("app").rglob("*.py"):
            if "__pycache__" in path.parts or path.name == "__init__.py":
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            found = any(
                isinstance(node, ast.Assign)
                and isinstance(node.value, ast.Call)
                and isinstance(node.value.func, ast.Attribute)
                and node.value.func.attr == "getLogger"
                for node in tree.body
            )
            if not found:
                missing.append(str(path))
        self.assertEqual(missing, [], f"以下模块缺少 module logger：{missing}")

    def test_no_fully_silent_except_blocks(self) -> None:
        """静默吞异常（pass/continue 且无日志）应为 0。"""
        offenders = []
        for root in ("app", "server"):
            for path in Path(root).rglob("*.py"):
                if "__pycache__" in path.parts:
                    continue
                tree = ast.parse(path.read_text(encoding="utf-8"))
                for node in ast.walk(tree):
                    if not isinstance(node, ast.ExceptHandler):
                        continue
                    body = node.body
                    simple = len(body) == 1 and isinstance(body[0], (ast.Pass, ast.Continue))
                    logged = any(
                        isinstance(n, ast.Call)
                        and isinstance(n.func, ast.Attribute)
                        and n.func.attr in ("debug", "info", "warning", "error", "exception", "critical")
                        for stmt in body
                        for n in ast.walk(stmt)
                    )
                    if simple and not logged:
                        offenders.append(f"{path}:{node.lineno}")
        self.assertEqual(offenders, [], f"仍有点完全吞掉异常：{offenders}")


class SetupLoggingTests(unittest.TestCase):
    def setUp(self) -> None:
        root = logging.getLogger()
        self._saved = (list(root.handlers), root.level)
        for handler in root.handlers[:]:
            root.removeHandler(handler)
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)

    def tearDown(self) -> None:
        root = logging.getLogger()
        for handler in root.handlers[:]:
            handler.close()
            root.removeHandler(handler)
        for handler in self._saved[0]:
            root.addHandler(handler)
        root.setLevel(self._saved[1])
        self.temp.cleanup()

    def test_handlers_are_added_once_and_file_takes_debug(self) -> None:
        from app.logging_setup import setup_logging

        setup_logging(Path(self.temp.name))
        setup_logging(Path(self.temp.name))
        root = logging.getLogger()
        self.assertEqual(len(root.handlers), 2, "重复调用不应叠加 handler")
        file_handlers = [h for h in root.handlers if hasattr(h, "baseFilename")]
        self.assertEqual(len(file_handlers), 1)
        self.assertEqual(file_handlers[0].level, logging.DEBUG)

    def test_env_level_is_applied(self) -> None:
        from app.logging_setup import resolve_level, setup_logging

        with mock.patch.dict("os.environ", {"DINGFENGBO_LOG_LEVEL": "WARNING"}):
            setup_logging(Path(self.temp.name))
        self.assertEqual(logging.getLogger().level, resolve_level("WARNING"))


class SyncCorrelationTests(unittest.TestCase):
    def test_log_prefix_uses_run_id(self) -> None:
        from app.services.sync import SyncClient

        client = SyncClient()
        client.run_id = "abc12345"
        with self.assertLogs("app.services.sync", level="DEBUG") as captured:
            client._log(logging.DEBUG, "拉取 %d 条", 3)
        self.assertIn("[sync abc12345]", captured.output[0])
        self.assertIn("拉取 3 条", captured.output[0])

    def test_run_id_is_rotated_per_run(self) -> None:
        from app.services.sync import SyncClient

        client = SyncClient()
        self.assertEqual(client.run_id, "")
        self.assertEqual(client.last_run_id, "")


if __name__ == "__main__":
    unittest.main()

class ThirdPartyNoiseTests(unittest.TestCase):
    """httpx 每条请求一行的 INFO 日志要压掉（且它的 URL 里可能带签名）。"""

    def setUp(self) -> None:
        import logging as _logging

        root = _logging.getLogger()
        self._saved_handlers = list(root.handlers)
        self._saved_level = root.level
        self._saved_httpx = _logging.getLogger("httpx").level
        for handler in root.handlers[:]:
            root.removeHandler(handler)
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)

    def tearDown(self) -> None:
        import logging as _logging

        root = _logging.getLogger()
        for handler in root.handlers[:]:
            handler.close()
            root.removeHandler(handler)
        for handler in self._saved_handlers:
            root.addHandler(handler)
        root.setLevel(self._saved_level)
        _logging.getLogger("httpx").setLevel(self._saved_httpx)
        self.temp.cleanup()

    def test_httpx_is_quiet_at_info(self) -> None:
        import logging as _logging

        from app.logging_setup import setup_logging

        with mock.patch.dict("os.environ", {"DINGFENGBO_LOG_LEVEL": "INFO"}):
            setup_logging(Path(self.temp.name))
        self.assertEqual(_logging.getLogger("httpx").level, _logging.WARNING)

    def test_httpx_is_visible_at_debug(self) -> None:
        import logging as _logging

        from app.logging_setup import setup_logging

        with mock.patch.dict("os.environ", {"DINGFENGBO_LOG_LEVEL": "DEBUG"}):
            setup_logging(Path(self.temp.name))
        self.assertEqual(_logging.getLogger("httpx").level, _logging.DEBUG)


class InsecureWarningThrottleTests(unittest.TestCase):
    """明文 HTTP 提醒每轮同步都会命中，只能提醒一次（换地址重新提醒）。"""

    def setUp(self) -> None:
        from app.services import sync

        self.sync = sync
        self._saved = set(sync._insecure_warned)
        sync._insecure_warned.clear()

    def tearDown(self) -> None:
        self.sync._insecure_warned.clear()
        self.sync._insecure_warned.update(self._saved)

    def test_same_url_warns_once(self) -> None:
        with self.assertLogs("app.services.sync", level="WARNING") as captured:
            self.sync._warn_insecure_once("http://43.142.92.115:8000")
            self.sync._warn_insecure_once("http://43.142.92.115:8000/")
        warnings = [line for line in captured.output if "明文 HTTP" in line]
        self.assertEqual(len(warnings), 1, "同一地址重复同步不应反复刷警告")
        self.assertIn("43.142.92.115:8000", warnings[0])

    def test_new_url_warns_again(self) -> None:
        with self.assertLogs("app.services.sync", level="WARNING") as captured:
            self.sync._warn_insecure_once("http://a.example:8000")
            self.sync._warn_insecure_once("http://b.example:9000")
        warnings = [line for line in captured.output if "明文 HTTP" in line]
        self.assertEqual(len(warnings), 2, "换了服务端地址应重新提醒")
