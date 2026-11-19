import http.client
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import commit_rules
import history
import server

spec = importlib.util.spec_from_file_location("service_settings_bugfix", ROOT / "fnos/app/service-settings.py")
service_settings = importlib.util.module_from_spec(spec)
spec.loader.exec_module(service_settings)


class CommitRulesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="fnsvn-rules-")
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name) / "repo"
        (self.repo / "hooks").mkdir(parents=True)

    def test_rejects_malformed_or_control_char_urls_and_trims_valid(self):
        for bad in (
            "http://",
            "https://",
            "ftp://example.com/hook",
            "https://example.com/hook\r\nX-Injected: 1",
            "https://example.com/with space",
            "https://example.com/\x00",
        ):
            with self.subTest(url=bad):
                with self.assertRaises(ValueError):
                    commit_rules.save(self.repo, {"minMessageLength": 0, "notificationUrl": bad})
        saved = commit_rules.save(self.repo, {"minMessageLength": 4, "notificationUrl": "  https://example.com/hook  "})
        self.assertEqual("https://example.com/hook", saved["notificationUrl"])
        self.assertEqual(4, saved["minMessageLength"])

    @unittest.skipIf(os.name == "nt", "POSIX locale coercion only applies on non-Windows")
    def test_run_hook_sets_utf8_locale_for_svnlook(self):
        commit_rules.save(self.repo, {"minMessageLength": 5, "notificationUrl": ""})
        captured = {}

        def fake_check_output(cmd, stderr=None, env=None):
            captured["env"] = env
            return "修复登录问题".encode("utf-8")

        with patch.dict(os.environ, {"LC_ALL": "C"}, clear=False), \
             patch.object(commit_rules.subprocess, "check_output", side_effect=fake_check_output):
            code = commit_rules.run_hook("pre-commit", str(self.repo), "1-1")
        self.assertEqual(0, code)
        self.assertEqual("C.UTF-8", captured["env"]["LC_ALL"])


class HistoryFilterTests(unittest.TestCase):
    def test_missing_date_is_skipped_when_end_date_filter_is_set(self):
        store = Mock()
        store.repo.return_value = Path("/tmp/fake-repo")
        xml_log = (
            '<?xml version="1.0" encoding="UTF-8"?><log>'
            '<logentry revision="2"><author>alice</author><msg>no date</msg></logentry>'
            '<logentry revision="1"><author>alice</author><date>2026-05-01T10:00:00.000000Z</date><msg>dated</msg></logentry>'
            '</log>'
        )

        def fake_run(*args, **kwargs):
            if args[0] == "svnlook":
                return "2\n"
            return xml_log

        result = history.history(store, "demo", {"to": ["2026-06-01"]}, fake_run, server.Error)
        self.assertEqual([1], [entry["revision"] for entry in result["entries"]])


class AccessValidationAndSettingsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="fnsvn-bugfix-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.store = server.Store(self.root / "data")
        repo_dir = self.store.repos / "demo"
        (repo_dir / "db").mkdir(parents=True)
        (repo_dir / "conf").mkdir(parents=True)
        (repo_dir / "format").write_text("5\n", encoding="utf-8")
        self.store.state["users"]["alice"] = "secret"
        self.store.state["repositories"]["demo"] = {"alice": "r"}
        self.store.state["path_permissions"] = {"demo": {"/trunk": {"alice": "rw"}}}
        self.store.sync()

    def test_access_preview_and_rules_reject_invalid_paths(self):
        httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        httpd.base_path = ""
        httpd.store = self.store
        httpd.auth_required = False
        httpd.maintenance = False
        httpd.svn_port = 3690
        httpd.svn_host = ""
        httpd.svn_process = None
        worker = threading.Thread(target=httpd.serve_forever, daemon=True)
        worker.start()
        self.addCleanup(lambda: (httpd.shutdown(), httpd.server_close(), worker.join(timeout=5)))

        host, port = httpd.server_address

        def request(method, path, body=None):
            conn = http.client.HTTPConnection(host, port, timeout=5)
            headers = {}
            payload = None
            if body is not None:
                headers = {"Content-Type": "application/json", "X-FnSVN-Request": "1"}
                payload = json.dumps(body)
            try:
                conn.request(method, path, payload, headers)
                resp = conn.getresponse()
                return resp.status, json.loads(resp.read().decode("utf-8"))
            finally:
                conn.close()

        status, data = request("GET", "/api/repositories/demo/access-preview?user=alice&path=/trunk/")
        self.assertEqual(200, status)
        self.assertEqual("rw", data["permission"])

        for bad in ("//trunk", "/trunk//sub", "/trunk\\sub", "/trunk%00"):
            with self.subTest(path=bad):
                status, _ = request("GET", f"/api/repositories/demo/access-preview?user=alice&path={bad}")
                self.assertEqual(400, status)

        status, _ = request("PUT", "/api/repositories/demo/access-rules", {"groups": {}, "paths": {"/trunk\x00bad": {"alice": "r"}}})
        self.assertEqual(400, status)

    def test_save_settings_skips_terminate_when_svn_already_exited(self):
        old_dir = self.root / "old"
        new_dir = self.root / "new"
        old_dir.mkdir()
        new_dir.mkdir()
        store = Mock()
        store.root = self.root / "data2"
        store.root.mkdir()
        store.repos = old_dir
        store.lock = threading.RLock()
        store.backup_lock = threading.Lock()
        store.state = {"admin": None, "repositories": {}}
        app = Mock()
        app.store = store
        app.settings_lock = threading.Lock()
        app.settings_file = self.root / "service-settings.json"
        app.svn_host = ""
        app.auth_required = True
        app.svn_port = 3690
        app.maintenance = False
        app.stop_event = threading.Event()
        app.web_only = True
        app.svn_process = Mock()
        app.svn_process.poll.return_value = 0

        server.save_settings(app, {"repositoriesPath": str(new_dir)})
        app.svn_process.terminate.assert_not_called()

    def test_service_settings_validate_wraps_mkdir_and_scandir_permission_errors(self):
        var = self.root / "pkgvar"
        var.mkdir()
        target = self.root / "denied"
        with patch.dict(os.environ, {"TRIM_PKGVAR": str(var)}, clear=False):
            with patch.object(Path, "mkdir", side_effect=PermissionError("denied")):
                with self.assertRaisesRegex(ValueError, "不可读写"):
                    service_settings.validate({"repos_path": str(target)})
            target.mkdir()
            with patch.object(service_settings.os, "scandir", side_effect=PermissionError("denied")):
                with self.assertRaisesRegex(ValueError, "不可读写"):
                    service_settings.validate({"repos_path": str(target)})


if __name__ == "__main__":
    unittest.main()
