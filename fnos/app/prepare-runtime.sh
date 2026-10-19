#!/bin/bash
set -eu
umask 077
APP="${TRIM_APPDEST:?Missing TRIM_APPDEST}"
VAR="${TRIM_PKGVAR:?Missing TRIM_PKGVAR}"
fail() {
    printf '%s\n' "$1" > "${TRIM_TEMP_LOGFILE:-/dev/stderr}"
    exit 1
}
case "$(uname -m)" in
    x86_64|amd64) ;;
    *) fail 'This FnSVN package requires an x86-64 fnOS system.' ;;
esac
# Windows packaging may omit Linux executable permission bits.
# Only package-local files are touched; no system installation is required.
find "$APP" -type d -exec chmod u+rwx,go-w {} +
find "$APP" -type f -exec chmod u+rw,go-w {} +
find "$APP/runtime" -type f \( -path '*/bin/*' -o -name 'ld-linux*.so*' -o -name 'ld-*.so*' \) -exec chmod u+x {} +
export PATH="$APP/runtime/bin:$PATH"
"$APP/runtime/bin/python3" -c 'import sqlite3,ssl,hashlib; hashlib.scrypt(b"probe",salt=b"runtime-check",n=16384,r=8,p=1); print("Python runtime OK")' || fail 'Bundled Python cannot run on this NAS. Inspect runtime dependencies and execution permissions.'
for command in svn svnadmin svnlook svnserve; do
    "$APP/runtime/bin/$command" --version --quiet >/dev/null || fail "Bundled $command cannot run on this NAS."
done
mkdir -p "$VAR/data" "$VAR/logs" || fail 'Cannot initialize package-owned data directories.'
