"""Package distributable source only; exclude credentials, model weights and runtime data."""
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED
ROOT = Path(__file__).resolve().parents[1]


def package():
    target = ROOT.parent / 'AI财务审核-v0.7-本地演示源码.zip'
    folders = {'app','compliance','static','tests','scripts','docs','assets','examples','.devcontainer'}
    files = {'README.md','requirements.txt','server.py','bootstrap.py','launch.py','start-demo.cmd','start-demo.sh',
             'start.ps1','stop.ps1','启动系统.cmd','停止系统.cmd','.env.example','.gitignore','.gitattributes'}
    with ZipFile(target, 'w', ZIP_DEFLATED) as z:
        for p in sorted(ROOT.rglob('*')):
            if not p.is_file():continue
            rel = p.relative_to(ROOT)
            if rel.parts[0] not in folders and rel.as_posix() not in files:continue
            if '__pycache__' in rel.parts or p.suffix in ('.pyc','.pyo') or p.name == '.env':continue
            z.write(p, 'ai-finance-compliance-demo/' + rel.as_posix())
    with ZipFile(target) as z:
        assert z.testzip() is None
        assert not any('/data/' in n or '/.venv/' in n or n.endswith('/.env') for n in z.namelist())
    return target


if __name__ == '__main__':
    path = package()
    print(path)
    print(f'{path.stat().st_size:,} bytes')
