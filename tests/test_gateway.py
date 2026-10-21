"""Real Unix-socket HTTP regressions for the fnOS unified gateway.

No NAS, reverse-proxy headers, Subversion executables, or TCP web port required.
Run: py -3.12 -m unittest discover -s tests -p test_gateway.py -v
"""
import http.client
from http.cookies import SimpleCookie
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from urllib.parse import urljoin, urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import server

PREFIX = "/app/fnsvn"


class UnixConnection(http.client.HTTPConnection):
    def __init__(self, path):
        super().__init__("nas.example", timeout=5)
        self.path = str(path)

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self.path)


class PageLinks(HTMLParser):
    def __init__(self):
        super().__init__()
        self.base = None
        self.assets = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "base":
            self.base = attrs.get("href")
        if tag == "script" and attrs.get("src"):
            self.assets.append(attrs["src"])
        if tag == "link" and attrs.get("rel") == "stylesheet":
            self.assets.append(attrs["href"])


class GatewayHTTP(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="fngw-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.socket_path = self.root / "app.sock"
        # Windows Python builds may omit AF_UNIX. Still exercise the same
        # production Handler and prefix/auth behavior over loopback TCP there.
        self.unix = hasattr(socket, "AF_UNIX")
        if self.unix:
            self.http = server.UnixHTTPServer(str(self.socket_path), server.Handler)
        else:
            self.http = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.http.base_path = PREFIX
        self.http.store = server.Store(self.root / "data")
        self.http.auth_required = True
        self.http.maintenance = False
        self.http.svn_port = 3690
        self.http.svn_host = ""
        self.http.svn_process = None
        self.worker = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.worker.start()
        self.addCleanup(self.stop_server)

    def stop_server(self):
        self.http.shutdown()
        self.http.server_close()
        self.worker.join(timeout=5)
        self.assertFalse(self.worker.is_alive())
        if self.unix:
            self.assertFalse(self.socket_path.exists(), "Socket must be removed on server_close")

    def request(self, path, data=None, cookie=None, method="GET", expected=200):
        headers = {}
        if data is not None:
            headers.update({"Content-Type": "application/json", "X-FnSVN-Request": "1"})
        if cookie:
            headers["Cookie"] = cookie
        conn = (UnixConnection(self.socket_path) if self.unix else
                http.client.HTTPConnection(*self.http.server_address, timeout=5))
        try:
            conn.request(method, path, json.dumps(data) if data is not None else None, headers)
            reply = conn.getresponse()
            payload = reply.read()
            response_headers = dict(reply.getheaders())
            self.assertEqual(expected, reply.status, payload.decode("utf-8", "replace"))
            return payload, response_headers
        finally:
            conn.close()

    def authenticate(self, endpoint="setup"):
        _, headers = self.request(PREFIX + "/api/" + endpoint,
                                  {"username": "admin", "password": "gateway-test-password"}, method="POST")
        cookies = SimpleCookie(headers["Set-Cookie"])
        session = cookies["fnsvn_session"]
        self.assertEqual(PREFIX + "/", session["path"])
        self.assertTrue(session["httponly"])
        return "fnsvn_session=" + session.value

    def test_entry_with_or_without_slash_resolves_static_assets(self):
        for path in (PREFIX, PREFIX + "/", PREFIX + "/index.html"):
            with self.subTest(path=path):
                html, headers = self.request(path)
                self.assertIn("text/html", headers["Content-Type"])
                page = PageLinks()
                page.feed(html.decode())
                self.assertEqual(PREFIX + "/", page.base)
                self.assertGreaterEqual(len(page.assets), 2)
                base = urljoin("https://nas.example" + path, page.base)
                for asset in page.assets:
                    resolved = urlsplit(urljoin(base, asset))
                    self.assertEqual("nas.example", resolved.netloc)
                    self.assertTrue(resolved.path.startswith(PREFIX + "/"), asset)
                    content, asset_headers = self.request(resolved.path)
                    self.assertTrue(content)
                    self.assertNotIn("text/html", asset_headers["Content-Type"])

    def test_gateway_does_not_expose_root_or_prefix_siblings(self):
        for path in ("/", "/api/session", "/app.js", "/style.css", "/app/fnsvn-other/api/session"):
            with self.subTest(path=path):
                self.request(path, expected=404)

    def test_setup_login_logout_and_session_cookie_under_gateway_prefix(self):
        self.request(PREFIX + "/api/overview", expected=401)
        body, _ = self.request(PREFIX + "/api/session")
        self.assertTrue(json.loads(body)["setupRequired"])
        setup_cookie = self.authenticate()
        body, _ = self.request(PREFIX + "/api/overview", cookie=setup_cookie)
        self.assertEqual([], json.loads(body)["repositories"])
        _, headers = self.request(PREFIX + "/api/logout", {}, setup_cookie, "POST")
        expired = SimpleCookie(headers["Set-Cookie"])["fnsvn_session"]
        self.assertEqual(PREFIX + "/", expired["path"])
        self.assertEqual("0", expired["max-age"])
        self.request(PREFIX + "/api/overview", cookie=setup_cookie, expected=401)
        login_cookie = self.authenticate("login")
        body, _ = self.request(PREFIX + "/api/session", cookie=login_cookie)
        self.assertTrue(json.loads(body)["authenticated"])

    def test_backup_download_remains_authenticated_and_prefixed(self):
        cookie = self.authenticate()
        backups = self.http.store.root / "backups"
        backups.mkdir(exist_ok=True)
        dump = b"SVN-fs-dump-format-version: 2\n\n"
        (backups / "game_20261022.svndump").write_bytes(dump)
        path = PREFIX + "/api/backups/game_20261022.svndump"
        self.request(path, expected=401)
        body, _ = self.request(PREFIX + "/api/backups", cookie=cookie)
        self.assertEqual("game_20261022.svndump", json.loads(body)["backups"][0]["filename"])
        body, headers = self.request(path, cookie=cookie)
        self.assertEqual(dump, body)
        self.assertIn("attachment", headers["Content-Disposition"])
        self.request("/api/backups/game_20261022.svndump", cookie=cookie, expected=404)

    @unittest.skipIf(os.name == "nt", "Windows does not expose Unix socket POSIX mode bits")
    def test_socket_allows_gateway_process_to_connect(self):
        self.assertEqual(0o666, stat.S_IMODE(self.socket_path.stat().st_mode))


@unittest.skipUnless(hasattr(socket, "AF_UNIX"), "Unix sockets unavailable")
class GatewaySocketLifecycle(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="fngw-")
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "app.sock"

    def test_regular_file_is_never_overwritten(self):
        self.path.write_text("keep this file", encoding="utf-8")
        with self.assertRaises((OSError, RuntimeError, ValueError)):
            server.UnixHTTPServer(str(self.path), server.Handler)
        self.assertEqual("keep this file", self.path.read_text("utf-8"))

    def test_live_socket_cannot_be_replaced(self):
        first = server.UnixHTTPServer(str(self.path), server.Handler)
        self.addCleanup(first.server_close)
        with self.assertRaises((OSError, RuntimeError, ValueError)):
            server.UnixHTTPServer(str(self.path), server.Handler)
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.connect(str(self.path))

    def test_stale_socket_is_replaced_and_removed_on_close(self):
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as stale:
            stale.bind(str(self.path))
        self.assertTrue(self.path.exists())
        http = server.UnixHTTPServer(str(self.path), server.Handler)
        http.server_close()
        self.assertFalse(self.path.exists())

    @unittest.skipIf(os.name == "nt", "Windows terminate kills without delivering SIGTERM")
    def test_cli_sigterm_removes_socket(self):
        proc = subprocess.Popen([sys.executable, str(ROOT / "server.py"), "--web-only",
                                 "--unix-socket", str(self.path), "--base-path", PREFIX,
                                 "--data", str(Path(self.tmp.name) / "data")],
                                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        try:
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                if proc.poll() is not None:
                    self.fail(proc.stderr.read().decode("utf-8", "replace"))
                try:
                    conn = UnixConnection(self.path)
                    conn.request("GET", PREFIX + "/api/session")
                    response = conn.getresponse()
                    self.assertEqual(200, response.status)
                    response.read()
                    break
                except OSError:
                    time.sleep(0.05)
                finally:
                    conn.close()
            else:
                self.fail("Unix HTTP server did not become ready")
            proc.terminate()
            self.assertEqual(0, proc.wait(timeout=10))
            self.assertFalse(self.path.exists())
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=5)
            proc.stderr.close()


if __name__ == "__main__":
    unittest.main()
