import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import urllib.request

MARKER = '# FnSVN managed hook v1'
DEFAULT = {'minMessageLength': 0, 'notificationUrl': ''}

def load(repo):
    path = Path(repo) / 'hooks' / 'fnsvn-rules.json'
    return json.loads(path.read_text('utf-8')) if path.exists() else dict(DEFAULT)

def save(repo, config):
    length = config.get('minMessageLength', 0)
    url = config.get('notificationUrl', '')
    if type(length) is not int or not 0 <= length <= 10000 or not isinstance(url, str) or len(url) > 2048 or (url and not url.startswith(('http://', 'https://'))):
        raise ValueError('无效的提交规则或通知地址')
    hooks = Path(repo) / 'hooks'
    hooks.mkdir(exist_ok=True)
    # Refuse ambiguous or externally modified installations before touching anything.
    for hook in ('pre-commit', 'post-commit'):
        target = hooks / hook
        original = hooks / (hook + '.fnsvn-original')
        if target.is_symlink() or original.is_symlink():
            raise ValueError('hooks 中存在符号链接，无法安全安装')
        if original.exists() and (not target.is_file() or (MARKER not in target.read_text('utf-8', errors='replace') and target.read_bytes() != original.read_bytes())):
            raise ValueError('已有 FnSVN 原始 hook 备份，无法安全覆盖')
    # Copy the hook runner into the repository so migrations and imports retain it.
    runner = hooks / 'fnsvn-hook.py'
    runner_tmp = hooks / 'fnsvn-hook.py.tmp'
    runner_tmp.write_bytes(Path(__file__).read_bytes())
    os.replace(runner_tmp, runner)
    python = shlex.quote(sys.executable)
    svnlook_path = shutil.which('svnlook') or 'svnlook'
    for hook in ('pre-commit', 'post-commit'):
        target = hooks / hook
        original = hooks / (hook + '.fnsvn-original')
        if target.exists() and MARKER not in target.read_text('utf-8', errors='replace'):
            if not original.exists():
                original_tmp = hooks / (hook + '.fnsvn-original.tmp')
                shutil.copy2(target, original_tmp)
                os.replace(original_tmp, original)
        # Preserve original hook arguments, exit code and stderr. FnSVN rules run only after success.
        script = f'''#!/bin/sh
{MARKER}
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
if [ -x "$HERE/{hook}.fnsvn-original" ]; then
  "$HERE/{hook}.fnsvn-original" "$@" || exit $?
fi
export FNSVN_SVNLOOK={shlex.quote(svnlook_path)}
exec {python} "$HERE/fnsvn-hook.py" {hook} "$@"
'''
        wrapper_tmp = hooks / (hook + '.fnsvn-wrapper.tmp')
        wrapper_tmp.write_text(script, encoding='utf-8', newline='\n')
        wrapper_tmp.chmod(0o755)
        os.replace(wrapper_tmp, target)
    temp = hooks / 'fnsvn-rules.json.tmp'
    temp.write_text(json.dumps({'minMessageLength': length, 'notificationUrl': url}, ensure_ascii=False), encoding='utf-8')
    os.replace(temp, hooks / 'fnsvn-rules.json')
    return load(repo)

def run_hook(kind, repo, revision):
    config = load(repo)
    svnlook = os.environ.get('FNSVN_SVNLOOK') or shutil.which('svnlook') or 'svnlook'
    flag = '-t' if kind == 'pre-commit' else '-r'
    def get(field):
        return subprocess.check_output([svnlook, field, flag, revision, repo], stderr=subprocess.PIPE).decode('utf-8', errors='replace').strip()
    if kind == 'pre-commit':
        if len(get('log')) < config['minMessageLength']:
            print('提交说明至少需要 %s 个字符' % config['minMessageLength'], file=sys.stderr)
            return 1
    elif config['notificationUrl']:
        payload = json.dumps({'repository': Path(repo).name, 'revision': int(revision), 'author': get('author'), 'message': get('log')}, ensure_ascii=False).encode('utf-8')
        req = urllib.request.Request(config['notificationUrl'], data=payload, headers={'Content-Type': 'application/json'}, method='POST')
        try:
            with urllib.request.urlopen(req, timeout=5) as response:
                response.read(1024)
        except Exception as exc:
            print('FnSVN notification failed: ' + str(exc), file=sys.stderr)
    return 0

if __name__ == '__main__':
    try:
        sys.exit(run_hook(*sys.argv[1:4]))
    except Exception as exc:
        print('FnSVN hook error: ' + str(exc), file=sys.stderr)
        sys.exit(1)
