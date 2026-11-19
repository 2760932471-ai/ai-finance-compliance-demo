"""Cross-platform local launcher; opens the browser only after the port is bound."""
import sys
import threading
import webbrowser

if sys.version_info < (3, 10):
    raise SystemExit("Please install Python 3.10 or newer from python.org.")

from server import create_server

try:
    server = create_server()
except OSError:
    raise SystemExit("Port 8765 is in use. Open http://127.0.0.1:8765 or run: python server.py --port 8766")

url = "http://127.0.0.1:8765"
print(f"AI Finance Compliance Demo: {url}\nKeep this terminal open. Press Ctrl+C to stop.", flush=True)
threading.Timer(.6, lambda: webbrowser.open(url)).start()
try:
    server.serve_forever()
except KeyboardInterrupt:
    pass
finally:
    server.server_close()
