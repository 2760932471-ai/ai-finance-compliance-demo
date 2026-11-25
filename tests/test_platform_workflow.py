"""API regressions at the boundary between audit runs and supervisory events."""
import io
import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import zipfile
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch
from urllib.parse import quote

import server
from app import casework, engine, materials, model, store


class PlatformWorkflow(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        for module in (store, materials, model):
            self.stack.enter_context(patch.object(module, 'DATA', self.root))
        self.stack.enter_context(patch.object(store, 'DB', self.root/'audit.sqlite3'))
        self.stack.enter_context(patch.dict(os.environ, {'AUDIT_AI':'off', 'AUDIT_DECISION_PROVIDER':'off'}))
        self.stack.enter_context(patch.object(server.POOL, 'submit'))
        store.init()
        self.http = self.start_service()
        self.url = self.address(self.http)

    def start_service(self, summary_only=False):
        service = server.create_server(0, summary_only=summary_only)
        thread = threading.Thread(target=service.serve_forever, daemon=True)
        thread.start()
        self.stack.callback(thread.join)
        self.stack.callback(service.server_close)
        self.stack.callback(service.shutdown)
        return service

    @staticmethod
    def address(service):
        return 'http://127.0.0.1:'+str(service.server_port)

    def request(self, path, data=None, headers=None, base=None):
        headers = {'X-Session-Token':server.TOKEN, **(headers or {})}
        if isinstance(data, dict):
            headers['Content-Type'] = 'application/json'
            data = json.dumps(data).encode()
        req = urllib.request.Request((base or self.url)+path, data=data, headers=headers)
        try:
            resp = urllib.request.urlopen(req)
        except urllib.error.HTTPError as err:
            resp = err
        with resp:
            payload = resp.read()
            return resp.status, json.loads(payload) if 'application/json' in resp.headers['Content-Type'] else payload

    def test_directed_audit_supplement_and_task_feedback(self):
        assignment = {'event_id':'PAY-002', 'actor':'监管员', 'owner':'合同岗',
                      'due':'2026-12-01','question':'补充协议在这笔付款发生时是否生效？'}
        _, view = self.request('/api/supervision/assign', assignment)
        task_id = view['result']['task_id']
        sample = (server.ROOT/'examples/contract-demo.txt').read_bytes()
        headers = {'X-Project-Name':'Directed audit', 'X-Filename':'contract.txt', 'X-Business-Id':'CON-DEMO-02', 'X-Scheme-Id':'contract',
                   'X-Supervision-Task':task_id}
        self.assertEqual(self.request('/api/upload',sample,{**headers,'X-Supervision-Task':'HCD-9999'})[0],400)
        self.assertEqual(self.request('/api/upload',sample,{**headers,'X-Scheme-Id':'carrier'})[0],400)
        code, run = self.request('/api/upload',sample,headers)
        self.assertEqual(code,201)
        self.assertEqual(run['supervision_task']['question'],assignment['question'])
        self.assertEqual(run['supervision_task']['row_id'],'PAY-002')
        engine.process(run['id'])
        row = store.get('run',run['id'])
        self.assertEqual([(r['id'],r['verdict']) for r in row['results']][:2],
                         [('CON-01','不通过'),('CON-02','通过')])
        eid = row['evidence'][0]['id']
        source = row['documents'][0]['id']
        self.assertEqual(self.request(f"/api/runs/{run['id']}/documents/{source}/original"),(200,sample))
        self.assertEqual(self.request('/api/schemes/contract?version=1')[1]['version'],1)
        self.assertEqual(self.request('/api/upload',sample,{**headers,'X-Project-Id':run['project_id']})[0],400)
        code,new = self.request(f"/api/runs/{run['id']}/supplement",'无追溯效力'.encode(),{'X-Filename':quote('补充说明.txt')})
        self.assertEqual(code,201)
        self.assertEqual([new[k] for k in ('project_id','business_id','scheme_version','supervision_task')],
                         [run[k] for k in ('project_id','business_id','scheme_version','supervision_task')])
        self.assertEqual(new['parent_run'],run['id'])
        code,feedback = self.request('/api/supervision/transition',{
            'task_id':task_id,'actor':'合同岗','action':'feedback','note':'核实协议适用时间',
            'evidence':'合同证据原文','run_id':run['id'],'evidence_ids':[eid]})
        self.assertEqual(code,200)
        self.assertEqual(feedback['metrics']['total'],3)
        ref = feedback['tasks'][0]['history'][-1]['audit_evidence'][0]
        self.assertIn('字符',ref['locator'])
        self.assertNotIn('页',ref['locator'])
        self.assertEqual(ref['run_id'],run['id'])
        self.assertEqual(self.request('/api/upload',sample,headers)[0],400)
        self.assertEqual(len(store.all_items('project')),1)

    def test_summary_service_never_exposes_details_or_interrupts_a_run(self):
        _,run = self.request('/api/upload',b'test',{'X-Filename':'one.txt'})
        base = self.address(self.start_service(summary_only=True))
        self.assertEqual(store.get('run',run['id'])['status'],'queued')
        with self.subTest('aggregate only'):
            code,state = self.request('/api/supervision/summary',base=base)
            self.assertEqual(code,200)
            self.assertEqual(state['metrics']['excess'],15000)
            self.assertNotIn('rows',state)
            self.assertNotIn('PAY-002',str(state))
        for path in ('/api/supervision/state','/api/projects','/api/bootstrap','/api/supervision/evidence?id=CONTRACT-1',
                     f"/api/runs/{run['id']}/documents/D001/original",'/api/reports/1234','/demo.html'):
            self.assertEqual(self.request(path,base=base)[0],403,path)
        self.assertEqual(self.request('/api/upload',b'no',base=base)[0],403)
        code,html = self.request('/',base=base)
        self.assertEqual(code,200)
        self.assertIn('汇总权限',html.decode())

    def test_old_records_keep_one_business_per_lineage_and_65_t1_items(self):
        store.put('project',{'id':'old','name':'旧财务','created_at':'2026-05-04'})
        for rid,parent in [('ab01',''),('ab02','ab01')]:
            store.put('run',{'id':rid,'project_id':'old','parent_run':parent,
                             'status':'completed','created_at':'2026-05-04'})
        _,projects = self.request('/api/projects')
        for r in projects[0]['runs']:
            self.assertEqual((r['business_id'],r['scheme_id'],r['summary']['expected']),('BUS-ab01','finance-t1',65))
        code,r = self.request('/api/upload',b'fresh',{'X-Filename':'file.txt','X-Project-Id':'old'})
        self.assertEqual(code,201)
        self.assertEqual(len(store.all_items('project')),1)
        self.assertNotEqual(r['business_id'],'BUS-ab01')
        _, stats = self.request('/api/operations')
        self.assertEqual((stats['projects'],stats['businesses'],stats['runs']),(1,2,3))
        _,again = self.request('/api/upload',b'followup',{'X-Filename':'file.txt'})
        self.assertEqual(again['project_id'],'old')

    def test_docx_evidence_locations(self):
        source = io.BytesIO()
        xml = """<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
        <w:body><w:p><w:r><w:t>待审合同</w:t></w:r></w:p><w:tbl><w:tr>
        <w:tc><w:p><w:r><w:t>签约单位</w:t></w:r></w:p></w:tc></w:tr></w:tbl></w:body></w:document>"""
        with zipfile.ZipFile(source,'w') as archive:
            archive.writestr('word/document.xml',xml)
        code,run = self.request('/api/upload',source.getvalue(),{'X-Filename':'agreement.docx','X-Scheme-Id':'contract'})
        self.assertEqual(code,201)
        engine.process(run['id'])
        _,result = self.request('/api/runs/'+run['id'])
        self.assertEqual([e['locator']['type'] for e in result['evidence']],['clause','row'])
        self.assertEqual(result['evidence'][1]['locator']['row'],1)

    def test_inference_timing_keeps_cached_and_program_runs_distinct(self):
        runs = [{'duration':2,'usage':[]},
                {'duration':3,'usage':[{'job_id':'new'}]},
                {'duration':1,'usage':[{'cached':True}]},
                {'duration':4,'usage':[{'cached':True},{'job_id':'new'}]},
                {'duration':5,'traces':[{'status':'failed','errors':{'CON-03':'模型网络连接失败'}}]}]
        metrics = casework.timing_metrics(runs)
        self.assertEqual({k:v['avg_seconds'] for k,v in metrics.items()},
                         {'cold':3,'cached':1,'mixed':4,'no_model':2,'failed':5})
