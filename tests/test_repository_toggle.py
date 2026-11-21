import http.client
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest

import server


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class Client:
    def __init__(self, port):
        self.port = port
        self.cookie = ""

    def request(self, method, path, body=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=15)
        headers = {}
        if self.cookie:
            headers["Cookie"] = self.cookie
        if method != "GET":
            headers["X-FnSVN-Request"] = "1"
        payload = None
        if body is not None:
            payload = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        conn.request(method, path, payload, headers)
        resp = conn.getresponse()
        raw = resp.read()
        set_cookie = resp.getheader("Set-Cookie")
        if set_cookie:
            self.cookie = set_cookie.split(";", 1)[0]
        conn.close()
        return resp.status, json.loads(raw.decode("utf-8")) if raw else {}


def svn_cmd(*args):
    return subprocess.run(
        ["svn", "--non-interactive", "--no-auth-cache", *args],
        capture_output=True,
        text=True,
        timeout=30,
    )


class RepositoryToggleIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.data = self.root / "data"
        self.http_port = free_port()
        self.svn_port = free_port()
        self.proc = subprocess.Popen(
            [
                sys.executable,
                str(Path(__file__).resolve().parent.parent / "server.py"),
                "--data", str(self.data),
                "--host", "127.0.0.1",
                "--port", str(self.http_port),
                "--svn-port", str(self.svn_port),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.addCleanup(self.stop_server)
        self.client = Client(self.http_port)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            try:
                status, data = self.client.request("GET", "/api/health")
                if status == 200 and data.get("ok"):
                    break
            except OSError:
                time.sleep(0.1)
        else:
            raise RuntimeError("FnSVN test server did not start in time")
        status, _ = self.client.request("POST", "/api/setup", {"username": "admin", "password": "correct-horse-password"})
        self.assertEqual(200, status)

    def stop_server(self):
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()
        if self.proc.stdout:
            self.proc.stdout.close()
        if self.proc.stderr:
            self.proc.stderr.close()

    def test_disable_blocks_read_and_write_and_reenable_restores_permissions(self):
        self.assertEqual(201, self.client.request("POST", "/api/repositories", {"name": "alpha"})[0])
        self.assertEqual(201, self.client.request("POST", "/api/repositories", {"name": "beta"})[0])
        self.assertEqual(200, self.client.request("POST", "/api/users", {"username": "alice", "password": "secret"})[0])
        self.assertEqual(200, self.client.request("PUT", "/api/repositories/alpha/permissions", {"permissions": {"alice": "rw"}})[0])
        self.assertEqual(200, self.client.request("PUT", "/api/repositories/beta/permissions", {"permissions": {"alice": "rw"}})[0])
        self.assertEqual(200, self.client.request("PUT", "/api/repositories/alpha/access-rules", {"groups": {"devs": ["alice"]}, "paths": {"/docs": {"@devs": "rw"}}})[0])

        alpha_url = f"svn://127.0.0.1:{self.svn_port}/alpha"
        beta_url = f"svn://127.0.0.1:{self.svn_port}/beta"
        wc_alpha = self.root / "wc-alpha"
        wc_beta = self.root / "wc-beta"

        # Initial checkout and commit succeed on both repos.
        res = svn_cmd("checkout", "--username", "alice", "--password", "secret", alpha_url, str(wc_alpha))
        self.assertEqual(0, res.returncode, res.stderr)
        (wc_alpha / "hello.txt").write_text("v1\n", encoding="utf-8")
        self.assertEqual(0, svn_cmd("add", str(wc_alpha / "hello.txt")).returncode)
        res = svn_cmd("commit", "--username", "alice", "--password", "secret", "-m", "init", str(wc_alpha))
        self.assertEqual(0, res.returncode, res.stderr)

        res = svn_cmd("checkout", "--username", "alice", "--password", "secret", beta_url, str(wc_beta))
        self.assertEqual(0, res.returncode, res.stderr)

        # Invalid payload rejected.
        status, _ = self.client.request("PUT", "/api/repositories/alpha/status", {"enabled": "no"})
        self.assertEqual(400, status)

        # Disable alpha.
        status, toggled = self.client.request("PUT", "/api/repositories/alpha/status", {"enabled": False})
        self.assertEqual(200, status)
        self.assertFalse(toggled["enabled"])

        # Overview, detail browse, and access-preview reflect disabled state.
        _, overview = self.client.request("GET", "/api/overview")
        by_name = {r["name"]: r for r in overview["repositories"]}
        self.assertFalse(by_name["alpha"]["enabled"])
        self.assertTrue(by_name["beta"]["enabled"])

        status, detail = self.client.request("GET", "/api/repositories/alpha")
        self.assertEqual(200, status)
        self.assertFalse(detail["enabled"])
        self.assertEqual([{"name": "hello.txt", "kind": "file"}], detail["entries"])

        status, preview = self.client.request("GET", "/api/repositories/alpha/access-preview?user=alice&path=/docs")
        self.assertEqual(200, status)
        self.assertEqual("", preview["permission"])
        self.assertTrue(preview["disabled"])

        # Both read (checkout/info/update) and write (commit) are completely blocked on alpha.
        self.assertNotEqual(0, svn_cmd("info", "--username", "alice", "--password", "secret", alpha_url).returncode)
        self.assertNotEqual(0, svn_cmd("checkout", "--username", "alice", "--password", "secret", alpha_url, str(self.root / "wc-alpha-2")).returncode)
        self.assertNotEqual(0, svn_cmd("update", "--username", "alice", "--password", "secret", str(wc_alpha)).returncode)
        (wc_alpha / "hello.txt").write_text("v2\n", encoding="utf-8")
        self.assertNotEqual(0, svn_cmd("commit", "--username", "alice", "--password", "secret", "-m", "blocked", str(wc_alpha)).returncode)

        # Beta remains completely unaffected.
        (wc_beta / "ok.txt").write_text("beta\n", encoding="utf-8")
        self.assertEqual(0, svn_cmd("add", str(wc_beta / "ok.txt")).returncode)
        res = svn_cmd("commit", "--username", "alice", "--password", "secret", "-m", "beta ok", str(wc_beta))
        self.assertEqual(0, res.returncode, res.stderr)

        # Re-enable alpha: saved permissions are automatically restored.
        status, toggled = self.client.request("PUT", "/api/repositories/alpha/status", {"enabled": True})
        self.assertEqual(200, status)
        self.assertTrue(toggled["enabled"])

        status, preview = self.client.request("GET", "/api/repositories/alpha/access-preview?user=alice&path=/docs")
        self.assertEqual(200, status)
        self.assertEqual("rw", preview["permission"])
        self.assertFalse(preview["disabled"])

        res = svn_cmd("commit", "--username", "alice", "--password", "secret", "-m", "restored", str(wc_alpha))
        self.assertEqual(0, res.returncode, res.stderr)

    def test_rename_and_delete_preserve_or_clean_disabled_state(self):
        self.assertEqual(201, self.client.request("POST", "/api/repositories", {"name": "legacy"})[0])
        self.assertEqual(200, self.client.request("PUT", "/api/repositories/legacy/status", {"enabled": False})[0])

        status, renamed = self.client.request("POST", "/api/repositories/legacy/rename", {"name": "archived"})
        self.assertEqual(200, status)
        self.assertEqual("archived", renamed["name"])
        self.assertFalse(renamed["enabled"])

        state = json.loads((self.data / "config" / "state.json").read_text("utf-8"))
        self.assertEqual(["archived"], state.get("disabled_repositories"))

        status, deleted = self.client.request("DELETE", "/api/repositories/archived", {"confirmName": "archived"})
        self.assertEqual(200, status)
        state = json.loads((self.data / "config" / "state.json").read_text("utf-8"))
        self.assertEqual([], state.get("disabled_repositories"))
        metadata = json.loads((Path(deleted["archivedPath"]).parent / "metadata.json").read_text("utf-8"))
        self.assertFalse(metadata["enabled"])


class RepositoryToggleStartupTests(unittest.TestCase):
    def test_startup_reapplies_disabled_svnserve_conf(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp) / "data"
            store = server.Store(data)
            store.create_repo("repo1")
            store.set_repo_enabled("repo1", False)
            # Simulate stale svnserve.conf before restart.
            store.configure_repo(store.repo("repo1"), enabled=True)
            self.assertIn("auth-access = write", (store.repo("repo1") / "conf" / "svnserve.conf").read_text("utf-8"))
            reloaded = server.Store(data)
            self.assertFalse(reloaded.is_repo_enabled("repo1"))
            self.assertIn("auth-access = none", (reloaded.repo("repo1") / "conf" / "svnserve.conf").read_text("utf-8"))


if __name__ == "__main__":
    unittest.main()
