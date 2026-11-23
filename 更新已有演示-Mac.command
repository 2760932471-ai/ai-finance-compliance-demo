#!/bin/sh
# Update the program in place so existing evidence paths and project data stay valid.
set -eu
source_dir=$(CDPATH='' cd "$(dirname "$0")" && pwd -P)
if [ "$#" -gt 0 ]; then
    target_dir=$1
else
    target_dir=$(/usr/bin/osascript -e 'POSIX path of (choose folder with prompt "选择旧版演示文件夹（里面有 start-demo.sh 和 data），先停止旧服务")')
fi
target_dir=$(CDPATH='' cd "$target_dir" && pwd -P)
if [ "$source_dir" = "$target_dir" ]; then
    exec sh "$source_dir/start-demo.sh"
fi
if [ ! -f "$target_dir/start-demo.sh" ] || [ ! -f "$target_dir/app/model.py" ] || [ ! -f "$target_dir/compliance/seed.py" ]; then
    echo '所选文件夹不是已集成的财务审核演示。请重新打开更新程序并选择旧版根目录。' >&2
    exit 1
fi
backup=$(mktemp -d "$target_dir/.mac-update-backup.XXXXXX")
for item in app/model.py app/mac_codex.py server.py static/app.js static/style.css launch.py start-demo.sh README.md 启动财务审核-Mac.command; do
    mkdir -p "$backup/$(dirname "$item")"
    if [ -f "$target_dir/$item" ]; then cp -p "$target_dir/$item" "$backup/$item"; fi
    cp -p "$source_dir/$item" "$target_dir/$item.new"
    mv -f "$target_dir/$item.new" "$target_dir/$item"
done
echo "已更新原程序。代码备份位于 $backup"
exec sh "$target_dir/start-demo.sh"
