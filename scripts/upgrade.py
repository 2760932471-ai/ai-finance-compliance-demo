"""Install the release's listed application files into an existing local Mac demo."""
import argparse
import ast
import filecmp
import errno
import json
import os
import shutil
import signal
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = Path("scripts/upgrade-files.txt")
RECOVERY = ".mac-update-pending.json"
ROOT_FILES = {
    ".env.example", "README.md", "requirements.txt", "server.py", "bootstrap.py", "launch.py",
    "start-demo.sh", "start-demo.cmd", "start.ps1", "stop.ps1", "启动系统.cmd", "停止系统.cmd",
}
MANAGED_TREES = {"app","compliance","static","assets","examples","scripts"}
ESSENTIAL = {MANIFEST, *map(Path, (
    "app/schemes.py","app/casework.py","app/word_text.py","compliance/supervision.py",
    "static/index.html","static/schemes.js","static/supervision.js","scripts/upgrade.py",
    "server.py","launch.py","bootstrap.py","start-demo.sh",
))}


def checked_name(name):
    rel = Path(name)
    if (not name or rel.is_absolute() or any(p in {"..", "__pycache__"} for p in rel.parts)
            or any(p.startswith(".") for p in rel.parts if p != ".env.example")):
        raise ValueError(f"非法受管路径：{name}")
    tree = rel.parts[0]
    root_file = len(rel.parts) == 1 and (name in ROOT_FILES or rel.suffix == ".command")
    schema = rel.parts[:2] == ("docs", "schemas")
    if not (root_file or tree in MANAGED_TREES or schema):
        raise ValueError(f"文件超出应用升级范围：{name}")
    return rel


def entries(source):
    manifest = source / MANIFEST
    paths = [checked_name(s.strip()) for s in manifest.read_text(encoding="utf-8").splitlines() if s.strip()]
    if len(set(paths)) != len(paths) or not ESSENTIAL.issubset(paths):
        raise ValueError("升级清单缺少 v0.8 必要文件或文件重复，请重新解压完整源码包。")
    return paths


def safe_file(root, rel, required=False):
    """Never follow a link from an application path into user data or another directory."""
    p = root
    for part in rel.parts[:-1]:
        p = p / part
        if p.is_symlink() or (p.exists() and not p.is_dir()):
            raise ValueError(f"受管路径中出现符号链接或非目录：{rel}")
    p = root / rel
    if p.is_symlink() or (p.exists() and not p.is_file()):
        raise ValueError(f"受管文件不是普通文件：{rel}")
    if required and not p.is_file():
        raise ValueError(f"升级包或备份缺少文件：{rel}")
    return p


def atomic_copy(src, dest):
    dest.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=dest.parent, prefix=".mac-update-", delete=False) as out:
        temp = Path(out.name)
    try:
        shutil.copy2(src, temp)
        os.replace(temp, dest)
    finally:
        temp.unlink(missing_ok=True)


def recover(target):
    journal = target / RECOVERY
    if journal.is_symlink():
        raise ValueError("遇到无效的升级恢复记录。")
    if not journal.exists():
        return
    info = json.loads(journal.read_text(encoding="utf-8"))
    backup_name = info["backup"]
    if Path(backup_name).name != backup_name or not backup_name.startswith(".mac-update-backup."):
        raise ValueError("升级备份目录无效。")
    backup = target / backup_name
    if backup.is_symlink() or not backup.is_dir():
        raise ValueError("升级备份不存在。")
    # Validate the whole recovery plan before replacing any files.
    plan = [(checked_name(rel), existed) for rel, existed in info["files"].items()]
    for rel, existed in plan:
        if type(existed) is not bool:
            raise ValueError("升级恢复记录无效。")
        safe_file(target,rel)
        if existed:
            safe_file(backup,rel,required=True)
    for rel, existed in reversed(plan):
        dest = target / rel
        if existed:
            atomic_copy(backup / rel,dest)
        else:
            dest.unlink(missing_ok=True)
    for name in sorted(info.get("new_dirs",[]), key=lambda s:len(Path(s).parts), reverse=True):
        directory = Path(name)
        if directory == Path(".") or directory not in {parent for rel, _ in plan for parent in rel.parents}:
            raise ValueError("非法的目录恢复记录。")
        try:
            (target / directory).rmdir()
        except FileNotFoundError:
            pass
        except OSError as exc:
            if exc.errno not in (errno.EEXIST,errno.ENOTEMPTY):
                raise
    journal.unlink()
    print("已从备份恢复中断前的程序文件。", flush=True)


@contextmanager
def upgrade_lock(target):
    import fcntl  # macOS/POSIX runtime only. The manifest can be inspected on Windows.
    fd = os.open(target / ".mac-update.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("另一个升级操作正在运行。") from None
        yield


def upgrade(source, target):
    source, target = Path(source).resolve(), Path(target).resolve()
    if source == target or source in target.parents or target in source.parents:
        raise ValueError("请将新版解压到独立目录，再选择旧版项目根目录。")
    if not target.is_dir() or not all(safe_file(target,Path(p)).is_file() for p in (
            "start-demo.sh","app/model.py","compliance/seed.py")):
        raise ValueError("所选目录缺少旧版演示程序，请选择包含 start-demo.sh 的原目录。")

    with upgrade_lock(target):
        recover(target)
        files = entries(source)
        for rel in files:
            safe_file(source,rel,required=True)
            safe_file(target,rel)

        with tempfile.TemporaryDirectory(dir=target, prefix=".mac-update-stage.") as folder:
            stage = Path(folder)
            changed = {}
            for rel in files:
                src = source / rel
                dest = target / rel
                if dest.exists() and filecmp.cmp(src,dest,shallow=False) and (src.stat().st_mode & 0o111)==(dest.stat().st_mode & 0o111):
                    continue
                staged = stage / rel
                staged.parent.mkdir(parents=True,exist_ok=True)
                shutil.copy2(src,staged)
                if staged.suffix == ".py":
                    ast.parse(staged.read_bytes(), filename=str(rel))
                changed[str(rel)] = dest.exists()
            if not changed:
                print("程序文件已是此版本。", flush=True)
                return None

            backup = Path(tempfile.mkdtemp(dir=target,prefix=".mac-update-backup."))
            # Back up all old files before publishing the first updated file.
            for name, existed in changed.items():
                if existed:
                    dest = backup / name
                    dest.parent.mkdir(parents=True,exist_ok=True)
                    shutil.copy2(target / name,dest)
            journal = target / RECOVERY
            draft = stage / "journal.json"
            new_dirs = {str(d) for name in changed for d in Path(name).parents if not (target/d).exists()}
            draft.write_text(json.dumps({"backup":backup.name,"files":changed,"new_dirs":sorted(new_dirs)},ensure_ascii=False),encoding="utf-8")
            os.replace(draft,journal)
            try:
                for name in changed:
                    dest = target / name
                    dest.parent.mkdir(parents=True,exist_ok=True)
                    os.replace(stage / name,dest)
                journal.unlink()
            except BaseException:
                recover(target)
                raise
            print(f"已更新 {len(changed)} 个文件。旧文件备份位于 {backup}",flush=True)
            return backup


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("target", type=Path)
    args = parser.parse_args()
    def interrupted(*_):
        raise KeyboardInterrupt()
    signal.signal(signal.SIGTERM,interrupted)
    try:
        upgrade(ROOT,args.target)
    except (OSError, ValueError, SyntaxError) as exc:
        raise SystemExit(f"升级未完成：{exc}") from None
    except KeyboardInterrupt:
        raise SystemExit("升级已中断。重新运行更新器可恢复待处理的程序备份。") from None


if __name__ == "__main__":
    main()
