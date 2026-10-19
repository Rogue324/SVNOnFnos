"""Real svnserve credential-cache regressions; never uses the user's SVN config.

Run on Windows with: py -3.12 -m unittest discover -s tests -p test_auth_cache.py -v
The bundled CLI is preferred; each test owns its server PID and config directory.
"""
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from server import Store, start_svn

BIN = ROOT / ".tools" / "svn" / "bin"
TOOLS = all((BIN / (name + ".exe")).is_file() or shutil.which(name)
            for name in ("svn", "svnserve", "svnadmin", "svnlook"))


@unittest.skipUnless(TOOLS and os.name == "nt", "Windows SVN credential store required")
class AuthenticationCache(unittest.TestCase):
    def setUp(self):
        env = patch.dict(os.environ, {"PATH": str(BIN) + os.pathsep + os.environ["PATH"]})
        env.start()
        self.addCleanup(env.stop)
        # Keep SVN's Windows path conversion out of this authentication test.
        self.tmp = tempfile.TemporaryDirectory(prefix="fnsvn-auth-", dir=ROOT)
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.config = self.root / "client-config"
        self.config.mkdir()
        (self.config / "config").write_text("[auth]\nstore-auth-creds = yes\n", encoding="ascii")
        (self.config / "servers").write_text("[global]\nstore-passwords = yes\nstore-auth-creds = yes\n", encoding="ascii")
        self.store = Store(self.root / "server")
        self.store.create_repo("game")
        self.store.state["users"] = {"writer": "test-writer-password", "reader": "test-reader-password"}
        self.store.state["repositories"]["game"] = {"writer": "rw", "reader": "r"}
        self.store.sync()
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        self.child, self.port = start_svn(self.store, port)
        self.addCleanup(self.stop_server)
        self.url = f"svn://127.0.0.1:{self.port}/game"

    def stop_server(self):
        if self.child.poll() is None:
            self.child.terminate()
        self.child.wait(timeout=10)

    def svn(self, *args, success=True):
        result = subprocess.run(["svn", *map(str, args), "--non-interactive", "--config-dir", str(self.config)],
                                capture_output=True, text=True, errors="replace", timeout=20)
        self.assertEqual(success, result.returncode == 0, result.stderr)
        return result

    def cache_writer(self):
        self.svn("list", self.url, "--username", "writer", "--password", "test-writer-password")
        self.assertTrue(list((self.config / "auth" / "svn.simple").glob("*")), "No isolated cache was created")

    def test_fresh_process_commit_reuses_cached_password(self):
        work = self.root / "checkout"
        self.svn("checkout", self.url, work, "--username", "writer", "--password", "test-writer-password")
        file = work / "hello.txt"
        file.write_text("first commit\n", encoding="ascii")
        self.svn("add", file)
        # No username/password; a new process must use the on-disk cache.
        self.svn("commit", work, "-m", "cached authentication")
        self.assertEqual(1, self.store.summary("game")["revision"])
        file.write_text("second commit\n", encoding="ascii")
        self.svn("commit", work, "-m", "reuse cache again")
        self.assertEqual(2, self.store.summary("game")["revision"])

    def test_reader_authenticates_but_cannot_commit(self):
        work = self.root / "readonly-checkout"
        self.svn("checkout", self.url, work, "--username", "reader", "--password", "test-reader-password")
        file = work / "denied.txt"
        file.write_text("cannot commit\n", encoding="ascii")
        self.svn("add", file)
        denied = self.svn("commit", work, "-m", "read only", success=False)
        self.assertIn("E170001", denied.stderr)
        self.assertIn("Authorization failed", denied.stderr)

    def test_switching_hostname_does_not_reuse_ip_cache(self):
        self.cache_writer()
        alternate = f"svn://localhost:{self.port}/game"
        denied = self.svn("list", alternate, success=False)
        self.assertIn("E170001", denied.stderr)
        self.svn("list", alternate, "--username", "writer", "--password", "test-writer-password")
        self.svn("list", alternate)
        self.assertEqual(2, len(list((self.config / "auth" / "svn.simple").glob("*"))))

    def test_changed_password_invalidates_cached_password(self):
        self.cache_writer()
        self.store.state["users"]["writer"] = "replacement-writer-password"
        self.store.sync()
        denied = self.svn("list", self.url, success=False)
        self.assertIn("E170001", denied.stderr)
        self.svn("list", self.url, "--username", "writer", "--password", "replacement-writer-password")
        self.svn("list", self.url)

    def test_changing_realm_requires_new_authentication(self):
        self.cache_writer()
        conf = self.store.repo("game") / "conf" / "svnserve.conf"
        conf.write_text(conf.read_text("utf-8").replace("realm = FnSVN", "realm = FnSVN-changed"), encoding="utf-8")
        denied = self.svn("list", self.url, success=False)
        self.assertIn("E170001", denied.stderr)
        self.svn("list", self.url, "--username", "writer", "--password", "test-writer-password")
        self.svn("list", self.url)

    def test_no_auth_cache_causes_next_process_to_require_password(self):
        self.svn("list", self.url, "--username", "writer", "--password", "test-writer-password", "--no-auth-cache")
        denied = self.svn("list", self.url, success=False)
        self.assertIn("E170001", denied.stderr)


if __name__ == "__main__":
    unittest.main()
