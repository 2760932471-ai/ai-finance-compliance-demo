"""Local deployment boundary. These controls are not multi-user authentication."""
import re
from pathlib import Path
from . import store


def local_file(path, directory):
    """Resolve symlinks/junctions before allowing access to a stored artifact."""
    candidate = Path(path).resolve()
    boundary = Path(directory).resolve()
    if not candidate.is_relative_to(boundary):
        raise PermissionError('文件不在允许的数据目录内')
    if not candidate.is_file():
        raise FileNotFoundError('文件不存在')
    return candidate


def request_origin(headers, port):
    if hasattr(headers,'get_all') and (len(headers.get_all('Host',[]))!=1 or len(headers.get_all('Origin',[]))>1):
        raise PermissionError('请求来源头无效')
    allowed = {f'127.0.0.1:{port}', f'localhost:{port}'}
    if port == 80:
        allowed.update(('127.0.0.1', 'localhost'))
    if headers.get('Host', '').lower() not in allowed:
        raise PermissionError('仅支持本机指定端口访问')
    origin = headers.get('Origin')
    if origin and origin not in {f'http://{host}' for host in allowed}:
        raise PermissionError('不允许跨来源请求')
    if headers.get('Sec-Fetch-Site', '').lower() == 'cross-site':
        raise PermissionError('不允许跨站请求')


def event(action, resource='', status=200):
    """Store metadata only: never request bodies, credentials or model output."""
    return store.put('security_event', {
        'id': store.uid(), 'time': store.now(), 'action': action[:60],
        'resource': re.sub(r'[^a-zA-Z0-9/_-]', '', resource)[:160],
        'status': status,
    })


def parameter_error(error):
    message = str(error)
    if re.search(r'[A-Za-z]:[\\/]|Traceback|attribute|[\\/]', message):
        return '请求参数无效，请检查输入内容'
    return message[:180] or '请求参数无效'
