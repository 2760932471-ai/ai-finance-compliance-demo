#!/bin/sh
# Update files in place to preserve the original evidence paths and project database.
set -eu
source_dir=$(CDPATH='' cd "$(dirname "$0")" && pwd -P)
if [ "$#" -gt 0 ]; then
    target_dir=$1
    shift
else
    target_dir=$(/usr/bin/osascript -e 'POSIX path of (choose folder with prompt "请先停止旧服务，再选择含 start-demo.sh 和 data 的旧版演示文件夹")')
fi
target_dir=$(CDPATH='' cd "$target_dir" && pwd -P)
if [ "$source_dir" != "$target_dir" ]; then
    if [ -x "$target_dir/.venv/bin/python" ]; then
        python="$target_dir/.venv/bin/python"
    else
        python=python3
    fi
    "$python" "$source_dir/scripts/upgrade.py" "$target_dir"
fi
exec sh "$target_dir/start-demo.sh" "$@"
