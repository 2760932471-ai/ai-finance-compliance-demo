"""Build a GitHub Pages mirror exclusively from isolated, public synthetic app data.

Usage: .venv/bin/python scripts/build_public_preview/build.py /path/to/site
Do not build into the application. The target may also be a gh-pages checkout.
"""
import argparse
import hashlib
from html import escape
from io import BytesIO
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
from threading import Thread
import time
from urllib.error import HTTPError
from urllib.parse import quote, urlencode
from urllib.request import build_opener, ProxyHandler, Request
from zipfile import ZipFile

TOOLS = Path(__file__).resolve().parent
ROOT = TOOLS.parents[1]
PROBE_TOKEN = 'public-read-only'


def fixture_zip(documents):
    body = BytesIO()
    with ZipFile(body, 'w') as archive:
        for filename, content in documents.items():
            archive.writestr(filename, content.encode('utf-8'))
    return body.getvalue()


class PublicSnapshots:
    def __init__(self, local_url, target, token):
        self.url = local_url
        self.target = target
        self.token = token
        self.opener = build_opener(ProxyHandler({}))
        self.gets = {}
        self.downloads = {}
        self.lab_runs = {}
        self.recall = {}
        self.tools = {}

    def request(self, route, data=None, headers=None, raw=False):
        sent_headers = {}
        if data is not None:
            sent_headers['X-Session-Token'] = self.token
            if not isinstance(data, bytes):
                data = json.dumps(data, ensure_ascii=False).encode('utf-8')
                sent_headers['Content-Type'] = 'application/json'
        sent_headers.update(headers or {})
        req = Request(self.url + route, data=data, headers=sent_headers)
        try:
            with self.opener.open(req, timeout=60) as res:
                response = res.read()
        except HTTPError as exc:
            raise RuntimeError(f'Could not snapshot {route}: {exc.code} {exc.read()[:350]!r}') from exc
        return response if raw else json.loads(response)

    def capture(self, route):
        result = self.request(route)
        self.gets[route] = result
        return result

    def attachment(self, route, ext='txt'):
        name = hashlib.sha256(route.encode('utf-8')).hexdigest()[:24] + '.' + ext
        path = self.target / 'public-docs' / name
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(self.request(route, raw=True))
        self.downloads[route] = 'public-docs/' + name
        return path

    def historical_risk_views(self):
        screening = self.request('/api/compliance/run', {})
        self.capture('/api/compliance/state')
        self.gets['/api/compliance/run'] = screening
        self.capture('/api/compliance/run?id='+screening['id'])
        self.capture('/api/compliance/tools')
        self.request('/api/compliance/graph-scenario', {'revision':0})
        self.capture('/api/compliance/graph-data')
        self.capture('/api/compliance/graph-template')
        for name in ('metrics', 'query'):
            self.tools[name] = self.request('/api/compliance/tools/call', {'name':name})
        for path in ('/api/compliance/export/config','/api/compliance/export/data',
                     '/api/compliance/graph-template','/api/compliance/tools','/api/compliance/lab/data'):
            self.attachment(path,'json')
        for fmt in ('html','csv','json'):
            self.attachment('/api/compliance/export/'+fmt+'?run='+screening['id'],fmt)
        self.graph = self.request('/api/compliance/tools/call', {'name':'graph'})
        for model in self.request('/api/compliance/lab/state')['active']:
            args = {'model_id':model['id'], 'start':'2026-10', 'end':'2026-10'}
            run = self.request('/api/compliance/lab/run',args)
            self.lab_runs[model['id']] = run['id']
            self.capture('/api/compliance/lab/run?id='+run['id'])
            self.attachment('/api/compliance/lab/export?id='+run['id'],'json')
        self.capture('/api/compliance/lab/state')

    def schemes(self):
        self.capture('/api/schemes/manage')
        summaries = self.capture('/api/schemes')
        manuals = {
            'contract': ('虚构合同条款核验口径',
                         '核对待审合同的付款起算条件与适用模板。付款日期应结合验收记录及该日期已生效的补充协议。'),
            'carrier': ('虚构载具核验口径',
                        '核对登记载具编号和现场凭证中的载具编号。文字记录一致仍需检查现场标识能否辨认。'),
        }
        for scheme in summaries:
            sid = scheme['id']
            if sid in manuals:
                title, content = manuals[sid]
                doc = self.request('/api/knowledge', {'scheme_id':sid, 'title':title, 'text':content,
                                                      'source':'内置虚构业务手册'})
                self.request('/api/knowledge/status', {'id':doc['id'], 'scheme_id':sid, 'status':'published'})
            route = '/api/schemes/'+quote(sid, safe='')
            detail = self.request(route)
            self.request(route+'/trial', {'revision':detail['revision']})
            self.capture(route)
            path = '/api/knowledge?'+urlencode({'scheme_id':sid})
            corpus = []
            for doc in self.capture(path):
                chunk = self.capture('/api/knowledge/chunks?'+urlencode({'id':doc['id'],'scheme_id':sid}))
                if sid=='finance-t1':
                    self.gets['/api/knowledge/chunks?'+urlencode({'id':doc['id']})] = chunk
                if doc.get('status')=='published':
                    corpus.extend(chunk['chunks'])
            self.recall[sid] = corpus

    def sample_projects(self):
        fixtures = [
            ('contract', '虚构样例 · 采购合同核对', {
                '待审合同.txt':'虚构合同 HT-2026-071。第四条：验收前累计付款上限 40,000.00 元。\n合同付款起算条件：交付后30日\n甲方名称：示例一公司\n乙方名称：示例青禾设备有限公司',
                '标准模板.txt':'虚构采购合同模板\n模板付款起算条件：验收后30日'}),
            ('carrier', '虚构样例 · 载具资料核对', {
                '登记记录.txt':'虚构登记清单\n登记载具编号：ZJ-022\n登记单位：示例运输公司',
                '现场凭证.txt':'虚构现场签收凭证\n现场载具编号：ZJ-022'}),
            ('finance-t1', '虚构样例 · 活动费用核对', {
                '报销主单.txt':'虚构报销单 BX-DEMO-001\n项目：经销商活动。报销金额 1600.00 元。活动日期 2026-07-08。'})
        ]
        for scheme_id, title, documents in fixtures:
            archive = fixture_zip(documents)
            headers={'Content-Type':'application/zip','X-Project-Name':quote(title),
                     'X-Filename':'demonstration.zip','X-Scheme-Id':scheme_id}
            if scheme_id == 'contract':
                headers['X-Supervision-Task'] = self.task_id
            after = self.request('/api/upload',archive,headers)
            route = '/api/runs/'+after['id']
            for _ in range(100):
                run = self.request(route)
                if run['status'] not in ('queued','processing'):
                    break
                time.sleep(.08)
            else:
                raise ValueError('Public example did not finish')
            if run.get('scheme_id')!=scheme_id:
                raise ValueError(f'Public example did not bind {scheme_id}')
            for doc in run.get('documents', []):
                self.attachment(route+'/documents/'+doc['id']+'/original')
            report = self.request(route+'/report', {'format':'pdf'})
            for fmt, details in report['formats'].items():
                self.attachment(details['url'],fmt)
            self.gets[route] = self.request(route)
            if scheme_id=='contract':
                self.contract_run = run

    def assign_sample_task(self):
        initial = self.request('/api/supervision/state')
        trigger = next(row for row in initial['rows'] if row['clue'])
        assigned = self.request('/api/supervision/assign', {
            'event_id':trigger['id'], 'question':'核对付款当日的验收状态、上限和补充协议生效日期。',
            'actor':'示例派单人', 'owner':'示例办理人', 'due':'2026-12-01', 'revision':initial['revision']})
        self.task_id = assigned['result']['task_id']
        self.assignment_revision = assigned['revision']

    def supervision(self):
        feedback = self.request('/api/supervision/transition', {
            'task_id':self.task_id, 'action':'feedback', 'actor':'示例办理人',
            'note':'已核对付款日期早于补充协议生效日期，提请复核。', 'evidence':'PAY-002、采购合同第四条及补充协议第一条',
            'run_id':self.contract_run['id'],
            'evidence_ids':[next(e['id'] for e in self.contract_run['evidence'] if '40,000' in e['text'])],
            'version':1, 'revision':self.assignment_revision})
        self.request('/api/supervision/transition', {
            'task_id':self.task_id, 'action':'rectify', 'actor':'示例复核人',
            'note':'请补充超限付款的例外批准或整改记录，留待复验。',
            'evidence':'虚构合同第四条及 PAY-002 付款来源行', 'version':2, 'revision':feedback['revision']})
        state = self.capture('/api/supervision/state')
        self.capture('/api/supervision/template')
        def references(value):
            if isinstance(value, dict):
                for v in value.values():
                    yield from references(v)
            elif isinstance(value, list):
                for v in value:
                    yield from references(v)
            elif isinstance(value, str) and value.startswith('/api/supervision/evidence?'):
                yield value
        for path in set(references(state)):
            attachment = self.attachment(path, 'html')
            original = attachment.read_text(encoding='utf-8')
            self.gets[path] = original
            attachment.write_text(
                '<!doctype html><html lang="zh-CN"><meta charset="utf-8">'
                '<meta name="viewport" content="width=device-width,initial-scale=1">'
                '<link rel="icon" href="../favicon.svg"><title>示例证据原文</title>'
                '<style>body{max-width:48rem;margin:6vh auto;padding:0 1.25rem;color:#17273d}'
                'pre{font:16px/1.8 system-ui,sans-serif;white-space:pre-wrap}</style>'
                '<pre>' + escape(original) + '</pre></html>', encoding='utf-8')

    def write(self):
        bootstrap = self.request('/api/bootstrap')
        bootstrap['token'] = PROBE_TOKEN
        bootstrap['offline'] = True
        bootstrap['model_available'] = False
        bootstrap['engine'] = '公开只读镜像'
        # CLI diagnostics and deployment details describe a machine, not the product.
        bootstrap['model_diagnostic'] = {'offline':True,'executable_found':False,'login':'不适用','inference':'不适用',
            'status':'公开只读镜像','explanation':'此镜像只展示虚构样例的快照。'}
        self.gets['/api/bootstrap'] = bootstrap
        for r in ('/api/rules','/api/knowledge','/api/projects','/api/operations','/api/evaluations','/api/feedback'):
            self.capture(r)
        data = {'reads':self.gets,'downloads':self.downloads,'models':self.lab_runs,
                'graph':self.graph,'tools':self.tools,'corpora':self.recall}
        body = json.dumps(data, ensure_ascii=False,separators=(',',':'))
        bodies = [body.encode('utf-8')]
        bodies.extend((self.target / filename).read_bytes() for filename in self.downloads.values())
        sensitive = (self.token, str(Path.cwd()), '/workspace/work/', '/Users/', 'sediment://')
        if any(value.encode('utf-8') in payload for value in sensitive for payload in bodies):
            raise RuntimeError('Snapshot contains a local session identifier or filesystem reference')
        (self.target/'preview-data.json').write_text(body,encoding='utf-8')


def assemble(read_model, target):
    sys.path.insert(0,str(read_model))
    from server import create_server, TOKEN
    with create_server(0) as server:
        worker=Thread(target=server.serve_forever,daemon=True)
        worker.start()
        try:
            snapshots=PublicSnapshots('http://127.0.0.1:'+str(server.server_port),target,TOKEN)
            snapshots.historical_risk_views()
            snapshots.schemes()
            snapshots.assign_sample_task()
            snapshots.sample_projects()
            snapshots.supervision()
            snapshots.write()
        finally:
            server.shutdown()
            worker.join()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('target',type=Path)
    parser.add_argument('--worker',action='store_true',help=argparse.SUPPRESS)
    args=parser.parse_args()
    target=args.target.resolve()
    if target.is_relative_to(ROOT) or ROOT.is_relative_to(target):
        parser.error('Use a separate output directory')
    target.mkdir(exist_ok=True,parents=True)
    if args.worker:
        if (os.environ.get('PUBLIC_PREVIEW_BUILD') != str(Path.cwd())
                or Path.cwd() == ROOT or Path('data').exists() or not Path('server.py').is_file()):
            parser.error('The snapshot worker requires a new isolated copy with an empty database.')
        return assemble(Path.cwd(),target)
    with TemporaryDirectory(prefix='public-audit-preview-') as tmp:
        build=Path(tmp)
        for name in ('app','compliance','examples','assets','static'):
            shutil.copytree(ROOT/name,build/name,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
        shutil.copy(ROOT/'server.py',build/'server.py')
        # Never read the user's .env, SQLite files, uploads, model cache or audit records.
        env={k:os.environ[k] for k in ('PATH','LANG','LC_ALL','SYSTEMROOT','TMPDIR') if k in os.environ}
        env.update(PUBLIC_PREVIEW_BUILD=str(build),AUDIT_AI='off',AUDIT_VISUAL_OCR='off',AUDIT_DECISION_PROVIDER='off',PYTHONDONTWRITEBYTECODE='1')
        site = build / 'public-output'
        subprocess.run([sys.executable, __file__, '--worker', str(site)], cwd=build, env=env, check=True)
        # Remove only superseded generated attachments previously listed in our own manifest.
        manifest = target / 'preview-data.json'
        if manifest.is_file():
            old = set(json.loads(manifest.read_text())['downloads'].values())
            new = set(json.loads((site / manifest.name).read_text())['downloads'].values())
            for filename in old - new:
                relative = Path(filename)
                if relative.parent == Path('public-docs') and relative.stem.isalnum():
                    (target / relative).unlink(missing_ok=True)
        shutil.copytree(site, target, dirs_exist_ok=True)
    shutil.copytree(ROOT/'static',target,dirs_exist_ok=True)
    shutil.copy(TOOLS/'adapter.js',target/'preview.js')
    page=target/'index.html'
    html=page.read_text().replace('href="/','href="./').replace('src="/','src="./')
    overlay=('<style>.public-mirror{padding:7px 16px;text-align:center;color:#fff;background:#183652;'
             'font:13px/1.5 system-ui,sans-serif}.public-mirror a{color:#c5eaff;text-decoration:underline}</style>'
             '<script src="./preview-recall.js"></script><script src="./preview.js"></script>'
             '<div class="public-mirror" role="note">公开只读预览 · 虚构样例 · 上传、AI审核及办理请用'
             '<a href="https://github.com/2760932471-ai/ai-finance-compliance-demo/archive/refs/heads/main.zip">本地完整版</a></div>')
    html = html.replace('>产品演示</a>', '>财务审核演示</a>')
    page.write_text(html.replace('<body>', '<body>'+overlay))
    module = target / 'compliance/module.js'
    module.write_text(module.read_text().replace('href="/compliance/style.css"', 'href="./compliance/style.css"'))
    (target/'.nojekyll').touch()


if __name__=='__main__':
    main()
