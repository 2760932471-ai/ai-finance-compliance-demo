"""Locate Codex Desktop or provision the official macOS executable for this app."""
import base64
import hashlib
import os
from pathlib import Path
import platform
import shutil
import subprocess
import tarfile
import tempfile

VERSION = '0.171.0'
PACKAGES = {
    'arm64': ('aarch64', 'arm64', 'hIYTkRUpuYpkVfTq4cpby0PXqhAVcIgE7f6FoyY1lt2RyARj3GXS1Can/dIYUBItVhXzhb98HYHZNzCX74QRlQ=='),
    'x86_64': ('x86_64', 'x64', 'pwN5uNEKx/mZjwBEf/6CKq6SJMfJS5kruV3sJQAk7ebIPvC58Ote3CicIxETDZtjLj35rNv3PLPd7mHCg6hGPw=='),
}


def runtime_path():
    cache = Path.home() / 'Library' / 'Application Support' / 'FinanceAudit' / 'codex'
    return cache / VERSION / platform.machine() / 'codex'


def find_existing():
    candidates = [
        folder / 'Codex.app' / 'Contents' / 'Resources' / 'codex'
        for folder in (Path('/Applications'), Path.home() / 'Applications')
    ]
    candidates.append(runtime_path())
    return next((str(p) for p in candidates if p.is_file() and os.access(p, os.X_OK)), '')


def unpack(archive, destination, machine):
    triple, _, expected = PACKAGES[machine]
    with archive.open('rb') as source:
        digest = hashlib.file_digest(source, 'sha512').digest()
    if digest != base64.b64decode(expected):
        raise ValueError('模型组件下载不完整或校验失败，请重新启动。')
    member = f'package/vendor/{triple}-apple-darwin/bin/codex'
    with tarfile.open(archive, 'r:gz') as package:
        entry = package.getmember(member)
        if not entry.isfile():
            raise ValueError('官方包缺少模型运行程序。')
        with package.extractfile(entry) as source, destination.open('xb') as target:
            shutil.copyfileobj(source, target)
    destination.chmod(0o755)


def provision():
    machine = platform.machine()
    if machine not in PACKAGES:
        raise ValueError(f'暂不支持此 Mac 架构（{machine}）。')
    dest = runtime_path()
    dest.parent.mkdir(parents=True, exist_ok=True)
    slug = PACKAGES[machine][1]
    url = f'https://registry.npmjs.org/@openai/codex/-/codex-{VERSION}-darwin-{slug}.tgz'
    print('正在下载官方 Codex 模型连接组件，首次启动需要几分钟。', flush=True)
    with tempfile.TemporaryDirectory(dir=dest.parent) as folder:
        archive = Path(folder) / 'package.tgz'
        binary = Path(folder) / 'codex'
        subprocess.run(['/usr/bin/curl', '--fail', '--location', '--proto', '=https',
                        '--connect-timeout', '15', '--max-time', '600', '--retry', '1',
                        '--output', str(archive), url], check=True)
        unpack(archive, binary, machine)
        subprocess.run([str(binary), '--version'], check=True, timeout=20)
        os.replace(binary, dest)
    return str(dest)
