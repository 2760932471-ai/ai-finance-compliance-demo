"""SDD-04 acceptance tests. Synthetic materials, no external inference."""
import copy
import hashlib
import io
import json
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

from pypdf import PdfReader
import test_api
import test_audit
from app import engine, harness, report, store


class ReportContractTests(unittest.TestCase):
    setUp = test_audit.AuditTests.setUp
    tearDown = test_audit.AuditTests.tearDown
    full_run = test_audit.AuditTests.full_run
    evidence = test_audit.AuditTests.evidence
    result = test_audit.AuditTests.result

    def test_pdf_snapshot_and_html_share_all_rules(self):
        run = self.full_run()
        item = report.generate(run)
        self.assertEqual(set(item['formats']), {'pdf', 'html', 'json'})
        pdf = Path(item['formats']['pdf']['path']).read_bytes()
        self.assertTrue(pdf.startswith(b'%PDF-'))
        reader = PdfReader(io.BytesIO(pdf))
        text = '\n'.join(page.extract_text() for page in reader.pages)
        for rule in engine.RULES:
            self.assertIn(rule['id'], text)
        self.assertIn('待人工确认', text)
        self.assertIn('D001-P1', text)
        snapshot = json.loads(Path(item['formats']['json']['path']).read_text(encoding='utf-8'))
        self.assertEqual(snapshot['schema_version'], 'audit-report-v1')
        self.assertEqual(len(snapshot['results']), 65)
        self.assertNotIn(str(self.root), json.dumps(snapshot, ensure_ascii=False))
        for variant in item['formats'].values():
            self.assertEqual(hashlib.sha256(Path(variant['path']).read_bytes()).hexdigest(), variant['sha256'])

    def test_report_keeps_original_after_later_review(self):
        run = self.full_run()
        item = report.generate(run)
        before = {fmt: Path(v['path']).read_bytes() for fmt, v in item['formats'].items()}
        store.put('review', {'id':'new','run_id':'r','rule_id':'T1-01-01',
                            'before':'通过','after':'不通过','actor':'测试财务',
                            'reason':'对照原件发现事实不一致','time':'2026-10-14T10:00:00',
                            'evidence_ids':['D001-P1']})
        new = report.generate(run)
        snap = json.loads(Path(new['formats']['json']['path']).read_text(encoding='utf-8'))
        self.assertEqual(snap['results'][0]['verdict'], '不通过')
        self.assertEqual(snap['ai_results'][0]['verdict'], '通过')
        self.assertEqual(snap['results'][0]['ai_result']['evidence_ids'], ['D001-P1'])
        for fmt, v in item['formats'].items(): self.assertEqual(before[fmt], Path(v['path']).read_bytes())

    def test_pdf_failure_leaves_no_success_record(self):
        from app import report_pdf
        with patch.object(report_pdf, 'render', side_effect=RuntimeError('synthetic renderer failure')):
            with self.assertRaises(RuntimeError): report.generate(self.full_run())
        self.assertFalse(store.all_items('report'))

    def test_same_second_review_last_write_wins(self):
        run = self.full_run()
        for ident, verdict in [('first', '不通过'), ('last', '待人工判断')]:
            store.put('review', {'id':ident,'run_id':'r','rule_id':'T1-01-01',
                                'before':'通过','after':verdict,'time':'2026-10-14T10:00:00',
                                'evidence_ids':['D001-P1'],'actor':'测试','reason':'同秒连续复核测试'})
        effective = engine.effective_results(run)[0]
        self.assertEqual(effective['verdict'], '待人工判断')
        self.assertEqual(run['results'][0]['verdict'], '通过')

    def test_harness_handles_unhashable_id_and_empty_missing(self):
        value = self.result()
        accepted, errors, extra = harness.inspect([dict(value, id=[])], engine.RULES[:1], self.evidence())
        self.assertFalse(accepted)
        value.update(verdict='待人工判断', evidence_ids=[], missing=None)
        accepted, errors, extra = harness.inspect([value], engine.RULES[:1], self.evidence())
        self.assertFalse(accepted)

    def test_long_source_is_explicit_excerpt_not_silent_loss(self):
        from app.report_pdf import excerpt
        self.assertLessEqual(len(excerpt('\n'.join(map(str,range(20))),120,2).splitlines()),3)
        run=self.full_run()
        run['evidence'][0]['text']='长正文每行都应保留在完整快照中。\n'*3000+'原文尾部校验标记'
        item=report.generate(run)
        reader=PdfReader(item['formats']['pdf']['path'])
        text='\n'.join(p.extract_text() for p in reader.pages)
        self.assertLess(len(reader.pages),50)
        self.assertIn('摘录',text)
        snapshot=json.loads(Path(item['formats']['json']['path']).read_text(encoding='utf-8'))
        self.assertTrue(snapshot['evidence'][0]['text'].endswith('原文尾部校验标记'))


class SecurityContractTests(unittest.TestCase):
    setUp = test_api.ApiTests.setUp
    tearDown = test_api.ApiTests.tearDown
    call = test_api.ApiTests.call
    zip = test_api.ApiTests.zip
    seed_run = test_api.ApiTests.seed_run

    def test_cross_site_and_wrong_host_port_rejected(self):
        self.assertEqual(self.call('/api/bootstrap', headers={'Sec-Fetch-Site':'cross-site'})[0], 403)
        self.assertEqual(self.call('/api/bootstrap', headers={'Host':'127.0.0.1:1'})[0], 403)

    def test_json_must_be_object_and_expected_errors_are_safe(self):
        code, data = self.call('/api/knowledge', [])
        self.assertEqual(code, 400)
        self.assertNotIn('attribute', data['error'])
        self.assertIn('对象', data['error'])

    def test_report_route_returns_pdf_and_detects_corruption(self):
        run = self.seed_run()
        run['evidence'][0].update(name='测试凭证.html',category='辅助凭证',document_id='D001')
        store.put('run', run)
        code, item = self.call('/api/runs/'+run['id']+'/report', {})
        self.assertEqual(code, 200)
        self.assertNotIn(str(self.root), json.dumps(item))
        with urllib.request.urlopen(self.base+'/api/reports/'+item['id']) as response:
            self.assertEqual(response.headers.get_content_type(), 'application/pdf')
            self.assertTrue(response.read().startswith(b'%PDF-'))
        internal = store.get('report', item['id'])
        Path(internal['formats']['pdf']['path']).write_bytes(b'corrupted')
        self.assertEqual(self.call('/api/reports/'+item['id'])[0], 409)

    def test_document_path_cannot_escape_run_directory(self):
        run = self.seed_run()
        secret = self.root/'not-a-material.txt'; secret.write_text('do not disclose', encoding='utf-8')
        run['documents'][0]['path'] = str(secret)
        store.put('run', run)
        code, data = self.call('/api/runs/'+run['id']+'/documents/D001/original')
        self.assertEqual(code, 403)
        self.assertNotIn('do not disclose', str(data))

    def test_safety_events_do_not_store_body_or_token(self):
        self.call('/api/knowledge', {'title':'记录测试','text':'正文不能出现在安全日志'})
        events = store.all_items('security_event')
        self.assertTrue(events)
        data = json.dumps(events, ensure_ascii=False)
        import server
        self.assertNotIn(server.TOKEN, data)
        self.assertNotIn('正文不能出现在安全日志', data)

    def test_wrong_content_type_and_transfer_encoding_rejected(self):
        self.assertEqual(self.call('/api/knowledge', b'{}', headers={'Content-Type':'text/plain'})[0],400)
        self.assertEqual(self.call('/api/knowledge', b'{}', headers={'Content-Type':'application/json','Transfer-Encoding':'chunked'})[0],400)
        self.assertEqual(self.call('/api/knowledge', b'{}', headers={'Content-Type':'application/json','Content-Length':str(3*1024*1024)})[0],400)

    def test_unexpected_failure_does_not_disclose_internal_path(self):
        run=self.seed_run()
        for error,status in ((RuntimeError,500),(PermissionError,403)):
            with self.subTest(error=error),patch.object(report,'generate',side_effect=error('C:/private/credentials secret-value')):
                code,data=self.call('/api/runs/'+run['id']+'/report',{})
                self.assertEqual(code,status)
                self.assertNotIn('credentials',str(data))
                self.assertNotIn('secret-value',str(data))

    def test_legacy_html_report_still_downloads(self):
        folder=self.root/'reports';folder.mkdir()
        page=folder/'old.html';page.write_text('<p>Historical report</p>',encoding='utf-8')
        store.put('report',{'id':'abcd','run_id':'aabb','path':str(page)})
        with urllib.request.urlopen(self.base+'/api/reports/abcd') as response:
            self.assertEqual(response.headers.get_content_type(),'text/html')
            self.assertIn(b'Historical report',response.read())

    def test_security_headers_and_unknown_report_format(self):
        with urllib.request.urlopen(self.base+'/api/bootstrap') as response:
            self.assertEqual(response.headers['X-Frame-Options'],'DENY')
            self.assertEqual(response.headers['Referrer-Policy'],'no-referrer')
        run=self.seed_run()
        self.assertEqual(self.call('/api/runs/'+run['id']+'/report',{'format':'docx'})[0],400)


if __name__ == '__main__': unittest.main()
