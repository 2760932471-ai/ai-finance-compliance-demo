"""Start the local service with the user's Codex CLI; offline is opt-in."""
import argparse
import os
import sys
import threading
import webbrowser


def main(argv=None):
    if sys.version_info < (3, 11):
        raise SystemExit('Python 3.11+ is required.')
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--live', action='store_true', help='Use Codex (default)')
    mode.add_argument('--offline', action='store_true', help='Explicit offline demonstration')
    parser.add_argument('--port', type=int)
    parser.add_argument('--no-browser', action='store_true')
    args = parser.parse_args(argv)
    # Explicit startup mode takes precedence over an old .env disabling AI.
    os.environ['AUDIT_AI'] = 'off' if args.offline else 'codex'
    os.environ['AUDIT_VISUAL_OCR'] = 'off' if args.offline else 'on'
    if args.offline:
        os.environ['AUDIT_DECISION_PROVIDER'] = 'off'
    from server import create_server
    from app import model
    if not args.offline:
        diagnostic = model.diagnostic(check_login=True)
        if not diagnostic['executable_found']:
            raise SystemExit('未找到 Codex CLI。请安装 Codex CLI，执行 codex login 后重新启动。\n如已安装，请在 .env 中设置 AUDIT_CODEX_PATH 为可执行文件的完整路径。')
        if diagnostic['login'] != '已登录':
            raise SystemExit('Codex 登录检查未通过。请在终端执行 codex login，完成后重新启动。')
        print('Codex CLI 已找到且已登录；已启用 AI 审核和原图识别。实际推理在提交审核后执行，需要联网。', flush=True)
    port = args.port or int(os.environ.get('PORT', '8765'))
    try:
        server = create_server(port)
    except OSError as exc:
        raise SystemExit(f'端口 {port} 不可用。请停止旧窗口，或运行 python3 bootstrap.py --port 8766') from exc
    url = f'http://127.0.0.1:{server.server_port}'
    print(f'AI Finance Audit: {url}\nMode: {"offline demonstration" if args.offline else "Codex AI"}\nPress Ctrl+C to stop.', flush=True)
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


if __name__ == '__main__':
    main()
