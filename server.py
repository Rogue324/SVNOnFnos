"""FnSVN: small, dependency-free administration server for svnserve."""
import argparse
from collections import deque
import ctypes
import errno
from datetime import datetime, timezone
import hashlib
import hmac
import ipaddress
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
import os
from pathlib import Path
import re
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
from urllib.parse import unquote, urlsplit, parse_qs, quote
import xml.etree.ElementTree as ET

NAME = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,63}\Z")
STATIC = Path(__file__).parent / "static"


class Error(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def name(value):
    if not isinstance(value, str) or not NAME.fullmatch(value):
        raise Error("名称须以英文字母开头，使用字母、数字、下划线或短横线，最长64字符")
    return value


def password(value):
    if not isinstance(value, str) or not 12 <= len(value) <= 128:
        raise Error("密码须为12至128个字符")
    if any(c in value for c in "\r\n\x00") or value != value.strip():
        raise Error("密码不能包含换行或首尾空格")
    return value


def svn_password(value):
    if not isinstance(value, str):
        raise Error("SVN 账户密码必须是文本")
    # svnserve's INI format cannot represent embedded line breaks/NUL or
    # preserve leading/trailing whitespace. Length is otherwise unrestricted.
    if any(c in value for c in "\r\n\x00") or value != value.strip():
        raise Error("SVN 密码不能包含换行、空字符或首尾空白")
    return value


def atomic(path, value):
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
        stream.write(value)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(tmp, path)


def run(*args, timeout=60):
    try:
        result = subprocess.run(args, capture_output=True, timeout=timeout)
    except FileNotFoundError:
        raise Error("未找到应用自带的 Subversion 工具，请检查原生运行库与服务日志", 503)
    except subprocess.TimeoutExpired:
        raise Error("SVN 操作超时", 504)
    if result.returncode:
        logging.warning("SVN command failed: %s", result.stderr.decode("utf-8", "replace")[:2000])
        raise Error("SVN 操作失败，请查看服务日志", 400)
    return result.stdout.decode("utf-8", "replace")


def port_number(value):
    try:
        value = int(value)
    except (ValueError, TypeError):
        raise ValueError("SVN port must be an integer between 1024 and 65535") from None
    if not 1024 <= value <= 65535:
        raise ValueError("SVN port must be between 1024 and 65535")
    return value


def connection_host(value):
    if not isinstance(value, str) or len(value) > 253:
        raise Error("连接地址只需填写 IP 或域名，不能包含协议、端口或路径")
    value = value.strip()
    if not value:
        return ""
    try:
        return str(ipaddress.ip_address(value[1:-1] if value.startswith("[") and value.endswith("]") else value))
    except ValueError:
        if any(c in value for c in "/:@[]\\?#%") or any(c.isspace() for c in value):
            raise Error("连接地址只需填写 IP 或域名，不能包含协议、端口或路径")
    try:
        domain = value.rstrip(".").encode("idna").decode("ascii").lower()
    except UnicodeError:
        raise Error("域名格式无效") from None
    if not domain or any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in domain.split(".")):
        raise Error("域名格式无效")
    if all(label.isdecimal() for label in domain.split(".")):
        raise Error("IP 地址格式无效")
    return domain


def repository_directory(data, value):
    path = Path(value).resolve()
    protected = (data / "config", data / "backups", data / "deleted")
    if path == Path(path.anchor) or path == data or path in data.parents or any(path.is_relative_to(p) or p.is_relative_to(path) for p in protected):
        raise Error("请使用独立的 SVN 仓库目录，不能使用文件系统根目录、应用配置或备份目录")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.TemporaryDirectory(prefix=".fnsvn-access-", dir=path) as probe:
        source = Path(probe) / "write-test"
        source.write_bytes(b"FnSVN access check")
        source.rename(Path(probe) / "renamed")
    return path


def promote_repository(source, destination):
    """Publish a completed copy without replacing even an empty destination."""
    if os.name == "nt":
        os.rename(source, destination)  # Windows rename fails if destination exists.
        return
    if sys.platform != "linux":
        raise OSError("Repository migration requires Linux or Windows")
    library = ctypes.CDLL(None, use_errno=True)
    rename = library.renameat2
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    if rename(-100, os.fsencode(source), -100, os.fsencode(destination), 1) != 0:  # RENAME_NOREPLACE
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code), str(destination))


def start_svn(store, requested=3690):
    """Start the configured port strictly; never take over another service."""
    port = port_number(requested)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        if os.name == "nt":
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        else:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind(("0.0.0.0", port))
        except OSError as exc:
            raise RuntimeError(f"SVN port {port} is unavailable: {exc}. Change the SVN port in the fnOS application settings or stop the conflicting service.") from exc
    child = None
    try:
        with (store.root / "svnserve.log").open("w+b") as errors:
            child = subprocess.Popen(["svnserve", "--daemon", "--foreground", "--threads", "--root", str(store.repos),
                                      "--listen-host", "0.0.0.0", "--listen-port", str(port)], stderr=errors)
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and child.poll() is None:
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=0.2) as connection:
                        connection.settimeout(0.5)
                        greeting = connection.recv(256)
                    if greeting.startswith(b"( success"):
                        time.sleep(0.05)
                        if child.poll() is None:
                            logging.info("svnserve ready on 0.0.0.0:%s; repositories: %s", port, store.repos)
                            return child, port
                except OSError:
                    pass
                time.sleep(0.05)
            if child.poll() is None:
                child.terminate()
                child.wait(timeout=5)
            errors.seek(0)
            detail = errors.read().decode("utf-8", "replace").strip()
        raise RuntimeError(f"svnserve failed on port {port}: {detail or 'No SVN greeting received within 5 seconds'}")
    except BaseException:
        if child is not None and child.poll() is None:
            child.terminate()
            child.wait(timeout=5)
        raise


class Store:
    def __init__(self, root, repos=None):
        self.root = Path(root).resolve()
        self.repos = (Path(repos) if repos else self.root / "repos").resolve()
        protected = (self.root / "config", self.root / "backups", self.root / "deleted")
        if self.repos == self.root or self.repos in self.root.parents or any(self.repos.is_relative_to(p) or p.is_relative_to(self.repos) for p in protected):
            raise RuntimeError("Repository directory must be separate from application configuration and backups")
        self.lock = threading.RLock()
        self.backup_lock = threading.Lock()
        self.sessions = {}
        self.attempts = {}
        for folder in (self.root, self.repos, self.root / "config", self.root / "backups"):
            folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.statefile = self.root / "config" / "state.json"
        self.state = json.loads(self.statefile.read_text("utf-8")) if self.statefile.exists() else {
            "admin": None, "users": {}, "repositories": {}}
        self.recover_rename()
        # If power was lost between moving a deletion into quarantine and
        # unregistering it, restore that still-registered repository on startup.
        missing_keys = {key for key in self.state["repositories"] if not (self.repos / name(key)).exists()}
        if missing_keys and (self.root / "deleted").is_dir():
            for metadata in (self.root / "deleted").glob("*/metadata.json"):
                try:
                    saved = json.loads(metadata.read_text("utf-8"))
                    key = saved.get("name")
                    if key not in missing_keys:
                        continue
                    expected = self.repos / name(key)
                    archived = metadata.parent / key
                    if (Path(saved.get("originalPath", "")).resolve() == expected.resolve()
                            and not archived.is_symlink() and (archived / "format").is_file()):
                        promote_repository(archived, expected)
                        missing_keys.remove(key)
                        logging.warning("Recovered interrupted deletion of %s", key)
                except (OSError, ValueError, Error):
                    logging.exception("Could not recover deletion metadata: %s", metadata)
        missing = [key for key in self.state["repositories"] if not (self.repos / name(key) / "format").is_file()]
        if missing:
            raise RuntimeError(f"Repository directory {self.repos} does not contain registered repositories: {', '.join(missing)}. Keep the previous directory or stop the application and migrate all repositories first.")
        # Check actual write access as the application user, including ACLs.
        probe = self.repos / (".fnsvn-write-test-" + secrets.token_hex(8))
        created = False
        try:
            with probe.open("xb") as stream:
                created = True
                stream.write(b"FnSVN storage check\n")
        except OSError as exc:
            raise RuntimeError(f"Repository directory is not writable by FnSVN: {self.repos}. Grant the application read/write access in fnOS.") from exc
        finally:
            if created:
                probe.unlink(missing_ok=True)
        self.sync()

    def recover_rename(self):
        journal = self.root / "config" / "rename-journal.json"
        if not journal.is_file():
            return
        entry = json.loads(journal.read_text("utf-8"))
        old, new = name(entry["oldName"]), name(entry["newName"])
        source, target = self.repos / old, self.repos / new
        if (old == new or Path(entry["sourcePath"]).resolve() != source.resolve()
                or Path(entry["targetPath"]).resolve() != target.resolve()
                or source.is_symlink() or target.is_symlink()):
            raise RuntimeError("Unfinished repository rename has incompatible paths; inspect config/rename-journal.json")
        repositories = self.state["repositories"]
        if (old in repositories) == (new in repositories):
            raise RuntimeError("Unfinished repository rename has inconsistent registration; inspect config/rename-journal.json")
        if source.is_dir() and not target.exists():
            if new in repositories:
                repositories[old] = repositories.pop(new)
        elif target.is_dir() and not source.exists():
            if old in repositories:
                promote_repository(target, source)
        else:
            raise RuntimeError("Unfinished repository rename has ambiguous directories; no data was overwritten")
        # Persist recovered permissions before retiring the recovery record.
        self.sync()
        journal.unlink()
        logging.warning("Recovered interrupted repository rename: %s -> %s", old, new)

    def sync(self):
        # svnserve's built-in CRAM-MD5 authentication requires a cleartext password file.
        config = self.root / "config"
        atomic(config / "passwd", "[users]\n" + "".join(f"{u} = {p}\n" for u, p in self.state["users"].items()))
        authz = ""
        for repo, perms in self.state["repositories"].items():
            authz += f"[{repo}:/]\n* =\n"
            authz += "".join(f"{u} = {v}\n" for u, v in perms.items()) + "\n"
        atomic(config / "authz", authz)
        atomic(self.statefile, json.dumps(self.state, ensure_ascii=False, indent=2))

    def available(self):
        return all(shutil.which(cmd) for cmd in ("svn", "svnadmin", "svnlook", "svnserve"))

    def repo(self, key):
        name(key)
        if key not in self.state["repositories"]:
            raise Error("仓库不存在", 404)
        return self.repos / key

    def summary(self, key):
        path = str(self.repo(key))
        return {"name": key, "revision": int(run("svnlook", "youngest", path).strip()),
                "uuid": run("svnlook", "uuid", path).strip()}

    def create_repo(self, key):
        name(key)
        with self.lock:
            dest = self.repos / key
            if key in self.state["repositories"] or dest.exists():
                raise Error("仓库名已存在", 409)
            run("svnadmin", "create", str(dest))
            self.configure_repo(dest)
            self.state["repositories"][key] = {}
            self.sync()
            return self.summary(key)

    def configure_repo(self, path):
        config = self.root / "config"
        atomic(path / "conf" / "svnserve.conf", "[general]\nanon-access = none\nauth-access = write\n"
               f"password-db = {(config / 'passwd').as_posix()}\nauthz-db = {(config / 'authz').as_posix()}\nrealm = FnSVN\n")

    def scan(self, raw, depth=3):
        if not isinstance(raw, str) or not Path(raw).is_absolute() or any(ord(c) < 32 for c in raw):
            raise Error("请填写飞牛上的绝对目录路径")
        root = Path(raw).resolve()
        if root == Path(root.anchor) or not root.is_dir():
            raise Error("请选择存在且应用可读取的具体目录，不支持扫描文件系统根目录")
        if isinstance(depth, bool) or not isinstance(depth, int) or not 0 <= depth <= 5:
            raise Error("扫描深度须为0至5")
        managed_paths = {self.repo(key).resolve() for key in self.state["repositories"]}
        found, warnings, pending = [], [], deque([(root, 0)])
        visited, entries_seen, deadline, truncated = 0, 0, time.monotonic() + 20, False
        excluded = [(self.root / folder).resolve() for folder in ("deleted", "backups", "config")]
        while pending:
            if visited >= 5000 or entries_seen >= 10000 or len(found) >= 100 or time.monotonic() > deadline:
                truncated = True
                break
            path, level = pending.popleft()
            visited += 1
            if path.name.startswith(".fnsvn-") or any(path == p or path.is_relative_to(p) for p in excluded):
                continue
            try:
                if (path / "format").is_file() and (path / "db").is_dir() and (path / "conf").is_dir():
                    try:
                        uuid = run("svnlook", "uuid", str(path), timeout=3).strip()
                        revision = int(run("svnlook", "youngest", str(path), timeout=3).strip())
                        suggested = path.name if NAME.fullmatch(path.name) else "repo-" + uuid[:8]
                        if (self.repos / suggested).exists() and path.resolve() not in managed_paths:
                            suggested = suggested[:54] + "-imported"
                        found.append({"name": path.name, "path": str(path), "uuid": uuid, "revision": revision,
                                      "managed": path.resolve() in managed_paths,
                                      "suggestedName": suggested})
                    except (Error, ValueError):
                        if len(warnings) < 30:
                            warnings.append(f"无法读取仓库：{path}，请检查格式兼容性及目录权限")
                    continue  # Never descend into an SVN database.
                if (path / ".svn").is_dir():
                    if len(warnings) < 30:
                        warnings.append(f"已跳过客户端工作副本：{path}；需要服务器仓库目录（含db/conf/format）")
                    continue
                if level < depth:
                    with os.scandir(path) as entries:
                        for entry in entries:
                            entries_seen += 1
                            if entries_seen >= 10000 or time.monotonic() > deadline:
                                truncated = True
                                break
                            if entry.is_dir(follow_symlinks=False) and not entry.name.startswith("."):
                                pending.append((Path(entry.path), level + 1))
                            if len(pending) >= 5000:
                                truncated = True
                                break
            except OSError:
                if path == root:
                    raise Error("无法读取扫描目录，请授予 FnSVN 应用用户读取权限", 403) from None
                if len(warnings) < 30:
                    warnings.append(f"无权读取，已跳过：{path}")
        return {"repositories": found, "warnings": warnings, "truncated": truncated}

    def browse(self, key, path=""):
        repo = self.repo(key)
        if "\x00" in path or "\\" in path or any(p in (".", "..") for p in path.split("/")):
            raise Error("无效的仓库路径")
        url = repo.as_uri() + "/" + quote(path.strip("/"), safe="/") + "@HEAD"
        listing = ET.fromstring(run("svn", "list", "--xml", "--non-interactive", url))
        logs = ET.fromstring(run("svn", "log", "--xml", "--non-interactive", "-l", "30", repo.as_uri() + "@HEAD"))
        result = self.summary(key)
        result.update(entries=[{"name": e.findtext("name"), "kind": e.get("kind")} for e in listing.findall(".//entry")],
                      log=[{"revision": int(e.get("revision")), "author": e.findtext("author", ""),
                            "date": e.findtext("date", ""), "message": e.findtext("msg", "")} for e in logs.findall("logentry")],
                      permissions=self.state["repositories"][key])
        return result

    def backup(self, key):
        repo = self.repo(key)
        if not self.backup_lock.acquire(blocking=False):
            raise Error("已有备份任务正在运行", 409)
        target = self.root / "backups" / f"{key}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{secrets.token_hex(3)}.svndump"
        tmp = target.with_suffix(".partial")
        try:
            with tmp.open("wb") as output:
                proc = subprocess.run(["svnadmin", "dump", "--quiet", str(repo)], stdout=output,
                                      stderr=subprocess.PIPE, timeout=3600)
            if proc.returncode:
                raise Error("备份失败，请查看服务日志", 500)
            os.chmod(tmp, 0o600)
            os.replace(tmp, target)
            return {"filename": target.name}
        except subprocess.TimeoutExpired:
            raise Error("备份超时", 504)
        finally:
            tmp.unlink(missing_ok=True)
            self.backup_lock.release()


def current_settings(server):
    return {"repositoriesPath": str(server.store.repos), "svnHost": server.svn_host, "svnPort": server.svn_port,
            "webAuthRequired": server.auth_required,
            "setupRequired": server.auth_required and not bool(server.store.state["admin"])}


def pause_svn(server):
    server.maintenance = True
    child = server.svn_process
    if child is not None and child.poll() is None:
        child.terminate()
        child.wait(timeout=20)


def resume_svn(server):
    try:
        if not server.web_only and not server.stop_event.is_set():
            server.svn_process, _ = start_svn(server.store, server.svn_port)
    finally:
        server.maintenance = False


def copy_repository(source, destination):
    run("svnadmin", "hotcopy", str(source), str(destination), timeout=3600)
    uuid = run("svnlook", "uuid", str(destination)).strip()
    revision = int(run("svnlook", "youngest", str(destination)).strip())
    if uuid != run("svnlook", "uuid", str(source)).strip():
        raise Error("仓库复制的 UUID 不匹配，未接管仓库", 500)
    return uuid, revision


def import_repository(server, body):
    key = name(body.get("name"))
    raw = body.get("path")
    if not isinstance(raw, str) or not Path(raw).is_absolute() or any(ord(c) < 32 for c in raw):
        raise Error("源仓库路径必须是绝对路径")
    source = Path(raw).resolve()
    if not (source / "format").is_file() or not (source / "db").is_dir() or not (source / "conf").is_dir():
        raise Error("所选路径不是 SVN 服务器仓库，不能使用客户端工作副本")
    store = server.store
    if not server.settings_lock.acquire(blocking=False):
        raise Error("已有仓库任务进行中，请稍后重试", 409)
    stage, promoted, registered = None, False, False
    store.lock.acquire()
    try:
        with store.lock, store.backup_lock:
            dest = store.repos / key
            if dest.exists() or key in store.state["repositories"]:
                raise Error("目标名称已存在，请更换接管名称", 409)
            if source == store.repos or store.repos.is_relative_to(source):
                raise Error("不能接管包含当前仓库根目录的路径")
            uuid = run("svnlook", "uuid", str(source)).strip()
            if any(run("svnlook", "uuid", str(store.repo(k))).strip() == uuid for k in store.state["repositories"]):
                raise Error("相同 UUID 的仓库已纳入管理，不要重复接管", 409)
            pause_svn(server)
            stage = Path(tempfile.mkdtemp(prefix=".fnsvn-import-", dir=store.repos))
            copied = stage / key
            copy_repository(source, copied)
            if (copied / "conf").is_symlink():
                raise Error("复制仓库的 conf 目录是符号链接，未接管；请使用独立的标准仓库配置目录")
            # Preserve original auth configuration in the copy for reference;
            # the source repository and its users are never modified.
            original = copied / "conf" / "svnserve.conf"
            if original.is_file():
                shutil.copyfile(original, copied / "conf" / "svnserve.conf.before-fnsvn")
            store.configure_repo(copied)
            if server.stop_event.is_set():
                raise Error("应用正在停止，已取消接管，源仓库保留", 503)
            promote_repository(copied, dest)
            promoted = True
            stage.rmdir()
            stage = None
            store.state["repositories"][key] = {}
            registered = True
            store.sync()
            return store.summary(key)
    except BaseException:
        if registered:
            store.state["repositories"].pop(key, None)
            store.sync()
        if promoted:
            logging.error("Unregistered import copy retained at %s; original source retained at %s", store.repos / key, source)
        raise
    finally:
        if stage is not None:
            logging.warning("Unfinished import retained at %s; source unchanged", stage)
        try:
            if server.maintenance:
                resume_svn(server)
        finally:
            store.lock.release()
            server.settings_lock.release()


def delete_repository(server, key, confirmation):
    if confirmation != key:
        raise Error("请准确输入仓库名称以确认删除")
    store = server.store
    if not server.settings_lock.acquire(blocking=False):
        raise Error("已有仓库任务进行中，请稍后重试", 409)
    moved, removed, archive = False, False, None
    original_permissions = None
    store.lock.acquire()
    try:
        with store.lock, store.backup_lock:
            source = store.repo(key)
            if source.is_symlink() or source.resolve().parent != store.repos.resolve():
                raise Error("仓库路径不是根目录下的直接目录，拒绝删除")
            if not source.is_dir() or not (source / "format").is_file():
                raise Error("仓库文件不存在或格式不完整", 409)
            original_permissions = dict(store.state["repositories"][key])
            pause_svn(server)
            parent = store.root / "deleted" / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + secrets.token_hex(6))
            parent.mkdir(parents=True, mode=0o700)
            archive = parent / key
            atomic(parent / "metadata.json", json.dumps({"name": key, "originalPath": str(source),
                                                         "permissions": original_permissions}, ensure_ascii=False, indent=2))
            try:
                promote_repository(source, archive)
                moved = True
            except OSError as exc:
                if exc.errno != errno.EXDEV:
                    raise
                # Across volumes, copy first; no original files are removed
                # until the archived UUID and revision have been checked.
                _, revision = copy_repository(source, archive)
                if revision != int(run("svnlook", "youngest", str(source)).strip()):
                    raise Error("回收副本版本校验失败，原仓库未删除", 500)
            del store.state["repositories"][key]
            removed = True
            store.sync()
            warning = ""
            if not moved:
                # The exact non-symlink direct child was checked above. The
                # verified archive is outside the directory served by SVN.
                if source.is_symlink() or source.resolve().parent != store.repos.resolve():
                    warning = "原目录路径发生变化，未清理；回收副本已保留"
                else:
                    try:
                        shutil.rmtree(source)
                    except OSError:
                        warning = "仓库已移出管理，回收副本已保留，但原目录有残留，请核对后清理"
            return {"ok": True, "archivedPath": str(archive), "warning": warning}
    except BaseException:
        if moved and archive is not None:
            promote_repository(archive, store.repos / key)
        if removed and original_permissions is not None:
            store.state["repositories"][key] = original_permissions
            store.sync()
        raise
    finally:
        try:
            if server.maintenance:
                resume_svn(server)
        finally:
            store.lock.release()
            server.settings_lock.release()


def rename_repository(server, key, new_name):
    new_name = name(new_name)
    store = server.store
    if not server.settings_lock.acquire(blocking=False):
        raise Error("已有仓库任务进行中，请稍后重试", 409)
    store.lock.acquire()
    moved, registered, journal_created = False, False, False
    journal = store.root / "config" / "rename-journal.json"
    permissions = None
    try:
        with store.backup_lock:
            source = store.repo(key)
            if new_name == key:
                return store.summary(key)
            target = store.repos / new_name
            if source.is_symlink() or source.resolve().parent != store.repos.resolve():
                raise Error("仓库不是根目录下的直接目录，拒绝改名")
            if not (source / "format").is_file():
                raise Error("仓库目录不存在或格式不完整", 409)
            if new_name in store.state["repositories"] or target.exists() or target.is_symlink():
                raise Error("新名称已存在，请选择其他名称", 409)
            if journal.exists():
                raise Error("存在尚未恢复的改名记录，请重启应用完成恢复", 409)
            permissions = dict(store.state["repositories"][key])
            pause_svn(server)
            atomic(journal, json.dumps({"oldName": key, "newName": new_name,
                                       "sourcePath": str(source), "targetPath": str(target)}, ensure_ascii=False))
            journal_created = True
            promote_repository(source, target)
            moved = True
            del store.state["repositories"][key]
            store.state["repositories"][new_name] = permissions
            registered = True
            store.sync()
            result = store.summary(new_name)
            journal.unlink()
            journal_created = False
            return result
    except BaseException:
        # Keep the journal if recovery itself fails; startup will resolve the
        # on-disk names and state without discarding either repository.
        if moved:
            promote_repository(store.repos / new_name, store.repos / key)
        if registered:
            store.state["repositories"].pop(new_name, None)
            store.state["repositories"][key] = permissions
            store.sync()
        if journal_created:
            journal.unlink(missing_ok=True)
        raise
    finally:
        try:
            if server.maintenance:
                if journal.exists():
                    # An unsuccessful rollback must not serve mismatched paths
                    # and authz. Restart performs recovery before binding SVN.
                    logging.error("Repository rename requires recovery; stopping service with journal %s", journal)
                    server.stop_event.set()
                    server.maintenance = False
                else:
                    resume_svn(server)
        finally:
            store.lock.release()
            server.settings_lock.release()


def save_settings(server, body):
    """Copy repositories while SVN is paused; never remove the source data."""
    raw = body.get("repositoriesPath")
    if not isinstance(raw, str) or not raw or not Path(raw).is_absolute() or any(ord(c) < 32 for c in raw):
        raise Error("仓库存放目录必须为绝对路径")
    host = connection_host(body.get("svnHost", ""))
    auth_required = body.get("webAuthRequired", server.auth_required)
    if not isinstance(auth_required, bool):
        raise Error("Web 登录验证选项必须为布尔值")
    if server.auth_required and not auth_required and body.get("confirmDisableAuth") is not True:
        raise Error("关闭 Web 登录后，所有能访问管理端口的人均可进入；确认后才能关闭", 409)
    if not server.settings_lock.acquire(blocking=False):
        raise Error("已有设置任务正在执行，请等待完成", 409)
    store = server.store
    previous = store.repos
    stage = None
    saved = False
    settings_path = server.settings_file
    old_text = None
    old_host = server.svn_host
    old_auth = server.auth_required
    promoted = []
    store.lock.acquire()
    try:
        old_text = settings_path.read_text("utf-8") if settings_path.exists() else None
        with store.lock, store.backup_lock:
            candidate = Path(raw).resolve()
            changing = candidate != previous
            if changing and (candidate.is_relative_to(previous) or previous.is_relative_to(candidate)):
                raise Error("新旧仓库目录不能相互包含，请选择独立目录")
            try:
                destination = repository_directory(store.root, raw)
            except OSError as exc:
                raise Error("目录不可读写，请在飞牛中授予 FnSVN 应用用户读写权限", 403) from exc
            keys = list(store.state["repositories"])
            if changing and any((destination / key).exists() for key in keys):
                raise Error("目标目录已有同名仓库。为避免覆盖，请选择没有同名仓库的专用目录", 409)
            values = json.loads(old_text) if old_text is not None else {}
            values.update(repos_path=str(destination), svn_host=host, web_auth_required=auth_required)
            values.setdefault("svn_port", server.svn_port)
            if changing:
                # Threads keep all sessions in the managed svnserve process, so
                # stopping it also closes writers before taking the copies.
                server.maintenance = True
                if server.svn_process is not None:
                    server.svn_process.terminate()
                    server.svn_process.wait(timeout=20)
                stage = Path(tempfile.mkdtemp(prefix=".fnsvn-migration-", dir=destination))
                for key in keys:
                    if server.stop_event.is_set():
                        raise Error("应用正在停止，已取消目录切换；原仓库保留", 503)
                    source, copied = previous / key, stage / key
                    run("svnadmin", "hotcopy", str(source), str(copied), timeout=3600)
                    if (run("svnlook", "uuid", str(source)) != run("svnlook", "uuid", str(copied)) or
                            run("svnlook", "youngest", str(source)) != run("svnlook", "youngest", str(copied))):
                        raise Error("仓库复制校验失败，已保留原仓库目录", 500)
                for key in keys:
                    if (destination / key).exists():
                        raise Error("复制期间目标出现同名目录，已停止切换以避免覆盖", 409)
                    promote_repository(stage / key, destination / key)
                    promoted.append(destination / key)
                stage.rmdir()
                stage = None
            if server.stop_event.is_set():
                raise Error("应用正在停止，请重新启动后保存设置", 503)
            settings_path.parent.mkdir(parents=True, exist_ok=True)
            atomic(settings_path, json.dumps(values, ensure_ascii=False, indent=2))
            saved = True
            store.repos = destination
            server.svn_host = host
            server.auth_required = auth_required
            if changing and not server.web_only:
                server.svn_process, _ = start_svn(store, server.svn_port)
            if not old_auth and auth_required:
                store.sessions.clear()
            result = current_settings(server)
            if changing:
                result["previousRepositoriesPath"] = str(previous)
            return result
    except BaseException as exc:
        store.repos = previous
        server.svn_host = old_host
        server.auth_required = old_auth
        if saved:
            if old_text is None:
                settings_path.unlink(missing_ok=True)
            else:
                atomic(settings_path, old_text)
        if server.maintenance and not server.web_only and not server.stop_event.is_set():
            # Stop any partially restarted child before restoring the original.
            if server.svn_process is not None and server.svn_process.poll() is None:
                server.svn_process.terminate()
                server.svn_process.wait(timeout=20)
            server.svn_process, _ = start_svn(store, server.svn_port)
        if (stage is not None or promoted) and isinstance(exc, Exception):
            raise Error(f"目录切换未完成，原配置与原仓库已恢复。目标 {raw} 可能保留复制副本；请选择其他空目录重试，或核对后自行处理副本。原因：{exc}", getattr(exc, "status", 500)) from exc
        raise
    finally:
        # A failed copy may remain as a clearly named staging folder. Keeping
        # partial copies avoids deleting anything in a user-selected directory.
        if stage is not None:
            logging.warning("Unfinished migration copy retained at %s; original repositories unchanged", stage)
        server.maintenance = False
        store.lock.release()
        server.settings_lock.release()


class Handler(BaseHTTPRequestHandler):
    server_version = "FnSVN/0.4.0"

    def log_message(self, fmt, *args):
        # Do not log cookies, credentials or request bodies.
        logging.info("%s %s", self.client_address[0], fmt % args)

    @property
    def store(self):
        return self.server.store

    def send(self, status, body, content_type="application/json; charset=utf-8", extra=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        # The fnOS desktop embeds this application from another port/origin.
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def body(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 65536:
                raise Error("请求内容大小无效", 413)
            obj = json.loads(self.rfile.read(length))
            if not isinstance(obj, dict):
                raise ValueError()
            return obj
        except (ValueError, UnicodeDecodeError):
            raise Error("请求需要 JSON 对象")

    def authenticated(self):
        if not self.server.auth_required:
            return True
        cookie = cookies.SimpleCookie()
        try:
            cookie.load(self.headers.get("Cookie", ""))
            token = cookie["fnsvn_session"].value
        except (KeyError, cookies.CookieError):
            return False
        with self.store.lock:
            now = time.time()
            self.store.sessions = {k: v for k, v in self.store.sessions.items() if v > now}
            return token in self.store.sessions

    def auth(self, setup):
        body = self.body()
        with self.store.lock:
            ip = self.client_address[0]
            now = time.monotonic()
            attempts = self.store.attempts.setdefault(ip, deque(maxlen=10))
            while attempts and attempts[0] < now - 300:
                attempts.popleft()
            if len(attempts) >= 10:
                raise Error("尝试次数过多，请5分钟后重试", 429)
            attempts.append(now)
            if setup:
                if self.store.state["admin"]:
                    raise Error("管理员已经初始化", 409)
                username = name(body.get("username"))
                secret = password(body.get("password"))
                salt = secrets.token_hex(16)
                digest = hashlib.scrypt(secret.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()
                self.store.state["admin"] = {"username": username, "salt": salt, "hash": digest}
                self.store.sync()
            else:
                admin = self.store.state["admin"]
                secret = body.get("password", "")
                if not isinstance(secret, str) or len(secret) > 128:
                    raise Error("用户名或密码错误", 401)
                if not admin:
                    raise Error("请先初始化管理员", 401)
                digest = hashlib.scrypt(secret.encode(), salt=bytes.fromhex(admin["salt"]), n=16384, r=8, p=1).hex()
                if body.get("username") != admin["username"] or not hmac.compare_digest(digest, admin["hash"]):
                    raise Error("用户名或密码错误", 401)
            attempts.clear()
            if len(self.store.sessions) >= 100:
                self.store.sessions.clear()
            token = secrets.token_urlsafe(32)
            self.store.sessions[token] = time.time() + 8 * 3600
        self.send(200, {"ok": True}, extra={"Set-Cookie": f"fnsvn_session={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age=28800"})

    def dispatch(self):
        parsed = urlsplit(self.path)
        parts = [unquote(p) for p in parsed.path.strip("/").split("/")]
        method = self.command
        if method != "GET" and self.headers.get("X-FnSVN-Request") != "1":
            raise Error("请求缺少安全标记", 403)
        if parts == ["api", "health"] and method == "GET":
            child = getattr(self.server, "svn_process", None)
            maintenance = self.server.maintenance
            healthy = maintenance or (self.store.available() and child is not None and child.poll() is None)
            return self.send(200 if healthy else 503, {"ok": healthy, "maintenance": maintenance})
        if parts == ["api", "session"] and method == "GET":
            return self.send(200, {"authenticated": self.authenticated(), "setupRequired": self.server.auth_required and not bool(self.store.state["admin"]),
                                   "authRequired": self.server.auth_required})
        if parts in (["api", "login"], ["api", "setup"]) and method == "POST":
            return self.auth(parts[-1] == "setup")
        if parts[0] != "api":
            if method != "GET":
                raise Error("方法不支持", 405)
            files = {"/": ("index.html", "text/html; charset=utf-8"),
                     "/index.html": ("index.html", "text/html; charset=utf-8"),
                     "/style.css": ("style.css", "text/css; charset=utf-8"),
                     "/app.js": ("app.js", "text/javascript; charset=utf-8")}
            if parsed.path not in files:
                raise Error("页面不存在", 404)
            filename, mime = files[parsed.path]
            return self.send(200, (STATIC / filename).read_bytes(), mime)
        if not self.authenticated():
            raise Error("请先登录", 401)
        if self.server.maintenance and method != "GET":
            raise Error("仓库目录正在切换，请完成后再进行修改", 409)
        if parts == ["api", "settings"] and method == "GET":
            return self.send(200, current_settings(self.server))
        if parts == ["api", "settings"] and method == "PUT":
            return self.send(200, save_settings(self.server, self.body()))
        if parts == ["api", "logout"] and method == "POST":
            if not self.server.auth_required:
                return self.send(200, {"ok": True, "authRequired": False})
            cookie = cookies.SimpleCookie(self.headers.get("Cookie", ""))
            with self.store.lock:
                self.store.sessions.pop(cookie["fnsvn_session"].value, None)
            return self.send(200, {"ok": True}, extra={"Set-Cookie": "fnsvn_session=; Path=/; Max-Age=0; HttpOnly; SameSite=Strict"})
        if parts == ["api", "overview"] and method == "GET":
            with self.store.lock:
                return self.send(200, {"repositories": [self.store.summary(k) for k in self.store.state["repositories"]],
                                      "users": [{"name": u} for u in self.store.state["users"]],
                                      "svnAvailable": self.store.available(), "svnPort": self.server.svn_port,
                                      "repositoriesPath": str(self.store.repos), "svnHost": self.server.svn_host,
                                      "webAuthRequired": self.server.auth_required})
        if parts == ["api", "repositories"] and method == "POST":
            return self.send(201, self.store.create_repo(self.body().get("name")))
        if parts == ["api", "repositories", "scan"] and method == "POST":
            body = self.body()
            with self.store.lock:
                return self.send(200, self.store.scan(body.get("path"), body.get("depth", 3)))
        if parts == ["api", "repositories", "import"] and method == "POST":
            return self.send(201, import_repository(self.server, self.body()))
        if parts == ["api", "users"] and method == "POST":
            body = self.body()
            username, secret = name(body.get("username")), svn_password(body.get("password"))
            with self.store.lock:
                # Existing users may rotate passwords without changing permissions.
                self.store.state["users"][username] = secret
                self.store.sync()
            return self.send(200, {"ok": True})
        if len(parts) == 3 and parts[:2] == ["api", "users"] and method == "DELETE":
            username = name(parts[2])
            with self.store.lock:
                if username not in self.store.state["users"]:
                    raise Error("用户不存在", 404)
                del self.store.state["users"][username]
                for perms in self.store.state["repositories"].values():
                    perms.pop(username, None)
                self.store.sync()
            return self.send(200, {"ok": True})
        if len(parts) >= 3 and parts[:2] == ["api", "repositories"]:
            key = parts[2]
            self.store.repo(key)
            if len(parts) == 3 and method == "DELETE":
                return self.send(200, delete_repository(self.server, key, self.body().get("confirmName")))
            if len(parts) == 3 and method == "GET":
                return self.send(200, self.store.browse(key, parse_qs(parsed.query).get("path", [""])[0]))
            if len(parts) == 4 and parts[3] == "rename" and method == "POST":
                return self.send(200, rename_repository(self.server, key, self.body().get("name")))
            if len(parts) == 4 and parts[3] == "permissions" and method == "PUT":
                perms = self.body().get("permissions")
                with self.store.lock:
                    if not isinstance(perms, dict) or any(u not in self.store.state["users"] or p not in ("", "r", "rw") for u, p in perms.items()):
                        raise Error("无效的用户或权限")
                    self.store.state["repositories"][key] = perms
                    self.store.sync()
                return self.send(200, {"ok": True})
            if len(parts) == 4 and parts[3] == "backup" and method == "POST":
                return self.send(201, self.store.backup(key))
        if parts == ["api", "backups"] and method == "GET":
            return self.send(200, {"backups": [{"filename": p.name, "size": p.stat().st_size} for p in
                                             sorted((self.store.root / "backups").glob("*.svndump"), reverse=True)]})
        if len(parts) == 3 and parts[:2] == ["api", "backups"] and method == "GET":
            filename = parts[2]
            if not re.fullmatch(r"[A-Za-z0-9_-]+\.svndump", filename):
                raise Error("无效的备份文件名")
            path = self.store.root / "backups" / filename
            if not path.is_file():
                raise Error("备份不存在", 404)
            with path.open("rb") as stream:
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
                self.send_header("Content-Length", str(path.stat().st_size))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                shutil.copyfileobj(stream, self.wfile)
            return
        raise Error("接口不存在", 404)

    def handle_request(self):
        self.connection.settimeout(30)
        try:
            self.dispatch()
        except Error as exc:
            self.send(exc.status, {"error": str(exc)})
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            pass
        except Exception:
            logging.exception("Request failed")
            self.send(500, {"error": "服务内部错误，请查看日志"})

    do_GET = do_POST = do_PUT = do_DELETE = handle_request


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", default=os.getenv("FNSVN_DATA", "data"))
    parser.add_argument("--host", default=os.getenv("FNSVN_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.getenv("FNSVN_PORT", "18880")))
    parser.add_argument("--svn-port", type=port_number, default=os.getenv("FNSVN_SVN_PORT", "3690"),
                        help="SVN listening port (default: 3690); occupied ports fail without switching")
    parser.add_argument("--repos", default=os.getenv("FNSVN_REPOS"), help="Repository storage directory (default: DATA/repos)")
    parser.add_argument("--settings-file", default=os.getenv("FNSVN_SETTINGS_FILE"), help="Persisted fnOS service settings file")
    parser.add_argument("--web-only", action="store_true", help="Development only: do not start svnserve")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if os.name != "nt":
        os.umask(0o077)
    settings_file = Path(args.settings_file).resolve() if args.settings_file else Path(args.data).resolve() / "config/service-settings.json"
    saved_settings = json.loads(settings_file.read_text("utf-8")) if settings_file.exists() else {}
    store = Store(args.data, args.repos or saved_settings.get("repos_path"))
    http = ThreadingHTTPServer((args.host, args.port), Handler)
    http.store = store
    http.settings_file = settings_file
    http.svn_host = connection_host(saved_settings.get("svn_host", ""))
    http.auth_required = saved_settings.get("web_auth_required", True)
    if not isinstance(http.auth_required, bool):
        raise RuntimeError("web_auth_required must be true or false in service settings")
    http.maintenance = False
    http.settings_lock = threading.Lock()
    http.stop_event = threading.Event()
    http.web_only = args.web_only
    child = None
    try:
        if not args.web_only:
            if not store.available():
                raise RuntimeError("Subversion tools missing; check bundled native runtime or use --web-only for UI development")
            child, svn_port = start_svn(store, args.svn_port)
        else:
            svn_port = args.svn_port or 3690
        http.svn_process = child
        http.svn_port = svn_port
        stop = http.stop_event

        def shutdown(*_):
            stop.set()

        def watch():
            while not stop.wait(0.5):
                current = http.svn_process
                if not http.maintenance and current is not None and current.poll() is not None:
                    logging.error("svnserve exited; stopping FnSVN")
                    stop.set()
            http.shutdown()

        signal.signal(signal.SIGTERM, shutdown)
        signal.signal(signal.SIGINT, shutdown)
        threading.Thread(target=watch, daemon=True).start()
        logging.info("FnSVN listening on %s:%s", args.host, args.port)
        http.serve_forever(poll_interval=0.5)
    finally:
        http.server_close()
        current = getattr(http, "svn_process", child)
        if current is not None and current.poll() is None:
            current.terminate()
            try:
                current.wait(timeout=20)
            except subprocess.TimeoutExpired:
                current.kill()
                current.wait()


if __name__ == "__main__":
    main()
