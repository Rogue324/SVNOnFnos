"""Persist fnOS wizard input and launch the service without sourcing user input."""
import json
import os
from pathlib import Path
import sys
import tempfile


def settings():
    var = Path(os.environ["TRIM_PKGVAR"])
    path = var / "service-settings.json"
    current = json.loads(path.read_text("utf-8")) if path.exists() else {}
    port = os.environ.get("wizard_svn_port", "").strip() or str(current.get("svn_port", 3690))
    if not port.isascii() or not port.isdecimal() or not 1024 <= int(port) <= 65535:
        raise ValueError("SVN 端口必须是 1024 至 65535 的整数。")
    raw = os.environ.get("wizard_repos_path", "").strip() or current.get("repos_path") or str(var / "data" / "repos")
    if not isinstance(raw, str) or any(ord(c) < 32 for c in raw) or not Path(raw).is_absolute():
        raise ValueError("仓库存放目录必须是绝对路径，不能包含控制字符。")
    root = Path(raw).resolve()
    if root == Path(root.anchor):
        raise ValueError("请使用 SVN 专用子目录，不能使用文件系统根目录。")
    auth = current.get("web_auth_required", True)
    if not isinstance(auth, bool):
        raise ValueError("网页登录验证配置必须为布尔值。")
    auth_choice = os.environ.get("wizard_web_auth", "").strip() or "keep"
    if auth_choice not in ("enabled", "disabled", "keep"):
        raise ValueError("网页登录验证选项无效。")
    if auth_choice != "keep":
        auth = auth_choice == "enabled"
    return path, {"svn_port": int(port), "repos_path": str(root), "svn_host": current.get("svn_host", ""),
                  "web_auth_required": auth}


def validate(value):
    root = Path(value["repos_path"])
    data = (Path(os.environ["TRIM_PKGVAR"]) / "data").resolve()
    protected = (data / "config", data / "backups", data / "deleted")
    if root == data or root in data.parents or any(root.is_relative_to(p) or p.is_relative_to(root) for p in protected):
        raise ValueError("仓库目录必须与应用配置和备份目录分开，请使用 SVN 专用子目录。")
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Test actual access as the package user; never rewrite existing directory ACLs.
    try:
        with tempfile.TemporaryDirectory(prefix=".fnsvn-access-", dir=root) as probe:
            test = Path(probe) / "write-test"
            test.write_text("FnSVN access check", encoding="utf-8")
            test.rename(Path(probe) / "rename-test")
    except OSError as exc:
        raise ValueError("仓库目录不可读写，请在飞牛中授权 FnSVN 应用用户访问此目录。") from exc
    state = Path(os.environ["TRIM_PKGVAR"]) / "data" / "config" / "state.json"
    if state.exists():
        registered = json.loads(state.read_text("utf-8")).get("repositories", {})
        saved_path = Path(os.environ["TRIM_PKGVAR"]) / "service-settings.json"
        old = json.loads(saved_path.read_text("utf-8")) if saved_path.exists() else {}
        previous = Path(old.get("repos_path") or str(data / "repos")).resolve()
        if registered and root != previous:
            raise ValueError("已有仓库时，请在 FnSVN 网页的「系统设置」中更改保存目录，应用将暂停写入并复制、核对仓库后切换；配置向导不能直接切换到其他副本。")
        if any(not (root / name / "db").is_dir() or not (root / name / "conf").is_dir() for name in registered):
            raise ValueError("所选目录缺少已登记仓库；请保留原目录，或停用应用后完整迁移仓库再修改。应用不会自动移动或删除仓库。")


def main():
    action = sys.argv[1]
    # Startup uses persisted settings only; old wizard environment cannot override them.
    if action == "run":
        os.environ.pop("wizard_svn_port", None)
        os.environ.pop("wizard_repos_path", None)
        os.environ.pop("wizard_web_auth", None)
    path, value = settings()
    if action in ("apply", "validate"):
        validate(value)
        if action == "apply":
            path.parent.mkdir(parents=True, exist_ok=True)
            fd, temporary = tempfile.mkstemp(prefix=".service-settings-", dir=path.parent)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as stream:
                    json.dump(value, stream, ensure_ascii=False, indent=2)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, path)
            finally:
                Path(temporary).unlink(missing_ok=True)
    elif action == "run":
        app = Path(os.environ["TRIM_APPDEST"])
        # Re-enter the wrapper so the bundled ELF loader and library paths survive exec.
        python = str(app / "runtime" / "bin" / "python3")
        os.execv(python, [python, "-u", str(app / "server.py"),
                 "--data", str(path.parent / "data"), "--unix-socket", str(app / "app.sock"),
                 "--base-path", "/app/fnsvn",
                 "--svn-port", str(value["svn_port"]), "--repos", value["repos_path"], "--settings-file", str(path)])
    else:
        raise ValueError("Unknown settings action")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError) as exc:
        message = "FnSVN 配置失败：" + str(exc)
        print(message, file=sys.stderr)
        if os.environ.get("TRIM_TEMP_LOGFILE"):
            Path(os.environ["TRIM_TEMP_LOGFILE"]).write_text(message + "\n", encoding="utf-8")
        sys.exit(1)
