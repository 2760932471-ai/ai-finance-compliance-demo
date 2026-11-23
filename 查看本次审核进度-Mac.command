#!/bin/sh
# Read status from the running service, including older versions.
exec python3 - <<'PY'
from datetime import datetime
import json
import sys
import urllib.request
from urllib.error import URLError

client = urllib.request.build_opener(urllib.request.ProxyHandler({}))
def read(path):
    with client.open('http://127.0.0.1:8765/api/' + path, timeout=20) as response:
        return json.load(response)

try:
    runs = [r for project in read('projects') for r in project.get('runs', [])]
    active = [r for r in runs if r['status'] == 'processing']
    if not runs:
        sys.exit('本机服务中还没有审核记录。')
    for row in active or [max(runs, key=lambda r: r['created_at'])]:
        run = read('runs/' + row['id'])
        print('审核编号', run['id'], '｜状态', run['status'], '｜阶段', run['phase'], str(run['progress'])+'%')
        events = run.get('events') or []
        if events:
            event = events[-1]
            minutes = max(0, (datetime.now().astimezone()-datetime.fromisoformat(event['time'])).total_seconds()/60)
            print('最近进度', event['time'], '（距现在 %.1f 分钟）' % minutes)
            if run['phase'] == '视觉识别':
                print(event['message'])
        for batch in run.get('recognition_usage', []):
            pages = batch.get('evidence_ids', [])
            result = batch.get('error') or ('已使用缓存' if batch.get('cached') else '已返回并通过格式校验')
            print('视觉批次', len(pages), '页｜', result)
        if run.get('error'):
            print('运行提示', run['error'])
except (URLError, OSError, ValueError, KeyError) as exc:
    sys.exit('无法读取本机 8765 端口的审核进度，请确认原启动窗口仍开着。' + str(exc))
PY
