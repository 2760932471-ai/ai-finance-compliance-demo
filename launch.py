"""Start one integrated local service; offline demo is the default."""
import argparse
import os
import sys
import threading
import webbrowser

if sys.version_info < (3, 11):
    raise SystemExit('Python 3.11+ is required.')
parser = argparse.ArgumentParser()
parser.add_argument('--live', action='store_true', help='Enable configured Codex model calls')
parser.add_argument('--port', type=int)
parser.add_argument('--no-browser', action='store_true')
args = parser.parse_args()
if not args.live:
    os.environ['AUDIT_AI'] = 'off'
    os.environ['AUDIT_VISUAL_OCR'] = 'off'
    os.environ['AUDIT_DECISION_PROVIDER'] = 'off'
from server import create_server

port = args.port or int(os.environ.get('PORT', '8765'))
try:
    server = create_server(port)
except OSError as exc:
    raise SystemExit(f'Cannot bind port {port}. Try: python launch.py --port 8766') from exc
url = f'http://127.0.0.1:{server.server_port}'
print(f'AI Finance Audit v0.5: {url}\nMode: {"configured AI" if args.live else "offline demonstration"}\nPress Ctrl+C to stop.', flush=True)
if not args.no_browser:
    timer = threading.Timer(.6, lambda: webbrowser.open(url))
    timer.daemon = True
    timer.start()
try:
    server.serve_forever()
except KeyboardInterrupt:
    pass
finally:
    server.server_close()
