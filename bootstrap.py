"""Create a project-local environment and install declared dependencies once."""
import os
from pathlib import Path
import subprocess
import sys


def main():
    if sys.version_info < (3, 11):
        raise SystemExit('Install Python 3.11 or newer from python.org first.')
    root = Path(__file__).resolve().parent
    python = root / '.venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    if not python.is_file():
        print('Creating local .venv ...', flush=True)
        subprocess.run([sys.executable, '-m', 'venv', str(root / '.venv')], check=True)
    check = subprocess.run([str(python), '-c', 'import pypdf,pypdfium2,PIL,reportlab,sklearn'], capture_output=True)
    if check.returncode:
        print('Installing requirements.txt dependencies (internet required on first launch) ...', flush=True)
        subprocess.run([str(python), '-m', 'pip', 'install', '-r', str(root / 'requirements.txt')], check=True)
    if '--check' in sys.argv[1:]:
        print('Runtime ready. Start with start-demo.cmd or sh start-demo.sh.')
        return 0
    return subprocess.call([str(python), str(root / 'launch.py'), *sys.argv[1:]], cwd=root)


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except subprocess.CalledProcessError:
        raise SystemExit('Setup failed. Check Python venv support, internet access and the pip error above.')
