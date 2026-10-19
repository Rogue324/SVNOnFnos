"""Real HTTP + svnserve integration; requires svn, svnadmin, svnlook, svnserve on PATH."""
import http.cookiejar
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from server import Store, start_svn, port_number


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@unittest.skipUnless(all(shutil.which(s) for s in ("svn", "svnadmin", "svnlook", "svnserve")), "Subversion tools required")
class Integration(unittest.TestCase):
    custom_repos = False
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="fnsvn-test-")
        cls.data = Path(cls.tmp.name) / "data"
        cls.repos = Path(cls.tmp.name) / "shared repos" if cls.custom_repos else cls.data / "repos"
        cls.port = free_port()
        cls.svn_port = free_port()
        cls.base = f"http://127.0.0.1:{cls.port}"
        cls.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        cls.log = (Path(cls.tmp.name) / "server.log").open("w+")
        cls.proc = subprocess.Popen([sys.executable, str(ROOT / "server.py"), "--host", "127.0.0.1",
                                     "--port", str(cls.port), "--data", str(cls.data), "--repos", str(cls.repos)],
                                    env={**os.environ, "FNSVN_SVN_PORT": str(cls.svn_port)},
                                    stdout=cls.log, stderr=cls.log)
        for _ in range(100):
            try:
                urllib.request.urlopen(cls.base + "/api/health", timeout=1).close()
                return
            except (OSError, urllib.error.URLError):
                if cls.proc.poll() is not None:
                    cls.log.seek(0)
                    error = cls.log.read()
                    cls.log.close()
                    cls.tmp.cleanup()
                    raise RuntimeError(error)
                time.sleep(0.1)
        raise RuntimeError("Server did not start")

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        cls.proc.wait(timeout=10)
        # Windows terminate does not deliver SIGTERM; stop only this test's svnserve PID.
        # Test run uses a child cleanup helper below on Windows.
        if os.name == "nt":
            subprocess.run(["powershell", "-NoProfile", "-Command",
                            f"Get-CimInstance Win32_Process -Filter \"Name='svnserve.exe'\" | Where-Object {{ $_.CommandLine -like '*{cls.repos}*' }} | ForEach-Object {{ Stop-Process -Id $_.ProcessId -PassThru | Wait-Process }}"],
                           capture_output=True)
        cls.log.close()
        cls.tmp.cleanup()

    def api(self, path, data=None, method="GET", expected=200, header=True):
        request = urllib.request.Request(self.base + path, method=method,
                                        data=json.dumps(data).encode() if data is not None else None,
                                        headers={"Content-Type": "application/json", **({"X-FnSVN-Request": "1"} if header else {})})
        try:
            result = self.opener.open(request, timeout=20)
        except urllib.error.HTTPError as exc:
            result = exc
        with result:
            self.assertEqual(expected, result.status, result.read().decode() if result.status != expected else "")
            payload = result.read()
            return json.loads(payload) if payload else None

    def svn(self, *args, success=True):
        result = subprocess.run(["svn", *args, "--non-interactive", "--no-auth-cache"], capture_output=True, timeout=20)
        self.assertEqual(success, result.returncode == 0, result.stderr.decode("utf-8", "replace"))
        return result.stdout.decode("utf-8", "replace")

    def test_complete_workflow(self):
        self.api("/api/overview", expected=401)
        self.assertTrue(self.api("/api/session")["setupRequired"])
        self.api("/api/setup", {"username": "admin", "password": "secure-admin-password"}, "POST", expected=403, header=False)
        self.api("/api/setup", {"username": "admin", "password": "secure-admin-password"}, "POST")
        self.api("/api/setup", {"username": "other", "password": "secure-admin-password"}, "POST", expected=409)
        self.api("/api/repositories", {"name": "../escape"}, "POST", expected=400)
        self.api("/api/repositories", {"name": "game"}, "POST", expected=201)
        self.api("/api/repositories", {"name": "game"}, "POST", expected=409)
        self.api("/api/users", {"username": "alice", "password": "secure-alice-password"}, "POST")
        self.api("/api/users", {"username": "bob", "password": "secure-bob-password"}, "POST")
        self.api("/api/repositories/game/permissions", {"permissions": {"alice": "rw", "bob": "r"}}, "PUT")
        url = f"svn://127.0.0.1:{self.svn_port}/game"
        work = str(Path(self.tmp.name) / "checkout")
        self.svn("list", url, success=False)
        self.svn("checkout", url, work, "--username", "alice", "--password", "wrong", success=False)
        self.svn("checkout", url, work, "--username", "alice", "--password", "secure-alice-password")
        file = Path(work) / "hello.txt"
        file.write_text("hello SVN\n", encoding="utf-8")
        self.svn("add", str(file))
        self.svn("commit", work, "-m", "Initial commit", "--username", "alice", "--password", "secure-alice-password")
        self.assertIn("hello.txt", self.svn("list", url, "--username", "bob", "--password", "secure-bob-password"))
        self.svn("mkdir", url + "/denied", "-m", "blocked", "--username", "bob", "--password", "secure-bob-password", success=False)
        detail = self.api("/api/repositories/game")
        self.assertEqual(1, detail["revision"])
        self.assertEqual("hello.txt", detail["entries"][0]["name"])
        self.assertEqual("Initial commit", detail["log"][0]["message"])
        self.api("/api/repositories/game?path=../config", expected=400)
        self.api("/api/repositories/game/permissions", {"permissions": {"ghost": "rw"}}, "PUT", expected=400)
        filename = self.api("/api/repositories/game/backup", {}, "POST", expected=201)["filename"]
        self.assertEqual(filename, self.api("/api/backups")["backups"][0]["filename"])
        with self.opener.open(self.base + "/api/backups/" + filename) as response:
            dump = response.read()
        restore = Path(self.tmp.name) / "restored"
        subprocess.run(["svnadmin", "create", str(restore)], check=True)
        result = subprocess.run(["svnadmin", "load", str(restore)], input=dump, capture_output=True)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("1", subprocess.check_output(["svnlook", "youngest", str(restore)]).decode().strip())
        self.api("/api/users/bob", method="DELETE")
        self.svn("list", url, "--username", "bob", "--password", "secure-bob-password", success=False)
        self.api("/api/users", {"username": "alice", "password": "changed-alice-password"}, "POST")
        self.svn("list", url, "--username", "alice", "--password", "secure-alice-password", success=False)
        self.svn("list", url, "--username", "alice", "--password", "changed-alice-password")
        reopened = Store(self.data, self.repos)
        self.assertEqual({"alice": "rw"}, reopened.state["repositories"]["game"])
        self.api("/api/logout", {}, "POST")
        self.api("/api/overview", expected=401)
        self.api("/api/login", {"username": "admin", "password": "incorrect"}, "POST", expected=401)
        self.api("/api/login", {"username": "admin", "password": "secure-admin-password"}, "POST")
        overview = self.api("/api/overview")
        self.assertEqual(1, overview["repositories"][0]["revision"])
        self.assertEqual(self.svn_port, overview["svnPort"])
        self.assertEqual(str(self.repos.resolve()), overview["repositoriesPath"])
        self.assertTrue((self.repos / "game" / "format").is_file())
        if self.custom_repos:
            self.assertFalse((self.data / "repos").exists())
        with self.assertRaisesRegex(RuntimeError, "does not contain registered repositories"):
            Store(self.data, Path(self.tmp.name) / "wrong-empty-directory")


class CustomStorageIntegration(Integration):
    custom_repos = True


class StartupValidation(unittest.TestCase):
    def test_occupied_port_fails_without_switching(self):
        with tempfile.TemporaryDirectory(prefix="fnsvn-port-test-") as tmp, socket.socket() as occupied:
            occupied.bind(("0.0.0.0", 0))
            occupied.listen(1)
            port = occupied.getsockname()[1]
            with self.assertRaisesRegex(RuntimeError, f"SVN port {port} is unavailable"):
                start_svn(Store(tmp), port)
            self.assertNotEqual(-1, occupied.fileno())

    def test_invalid_port_rejected(self):
        for port in ("not-a-port", 0, 1023, 65536):
            with self.assertRaises(ValueError):
                port_number(port)


if __name__ == "__main__":
    unittest.main(verbosity=2)
