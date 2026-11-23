#!/bin/sh
# Use the running audit app's interpreter, installed adapter and account.
set -eu
tool_dir=$(CDPATH='' cd "$(dirname "$0")" && pwd -P)
project=${1:-}
if [ -z "$project" ]; then
    pid=$(/usr/sbin/lsof -nP -tiTCP:8765 -sTCP:LISTEN | head -n 1) || true
    if [ -n "$pid" ]; then
        project=$(/usr/sbin/lsof -a -p "$pid" -d cwd -Fn | sed -n 's/^n//p' | head -n 1)
    fi
fi
if [ ! -f "$project/app/model.py" ] || [ ! -x "$project/.venv/bin/python" ]; then
    project=$(/usr/bin/osascript -e 'POSIX path of (choose folder with prompt "请选择正在使用的审核启动包目录（含 start-demo.sh）")')
fi
if [ ! -f "$project/server.py" ] || [ ! -f "$project/app/model.py" ] || [ ! -x "$project/.venv/bin/python" ]; then
    echo '未找到审核运行环境，请选择已经启动过的审核包目录。' >&2
    exit 1
fi
cd "$project"
exec .venv/bin/python "$tool_dir/check.py"
