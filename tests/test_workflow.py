"""HTTP workflow with real governance/storage/Harness and isolated model fixtures.

These tests validate application behavior, never financial accuracy or exam answers.
"""
import copy, os, urllib.request, unittest
from pathlib import Path
from unittest.mock import patch
import test_api
from app import engine, model, store
import server


class WorkflowTests(unittest.TestCase):
    setUp = test_api.ApiTests.setUp
    tearDown = test_api.ApiTests.tearDown
    call = test_api.ApiTests.call
    zip = test_api.ApiTests.zip

    def infer(self, *args, **kwargs):
        if args:self.last_prompt = args[0]
        return {'results': [
            {'id': r['id'], 'verdict': '待人工判断',
             'facts': '测试材料包含费用凭证100，其他条件尚缺依据。',
             'basis': '测试桩：需人工核实该规则要求。',
             'evidence_ids': ['D001-P1'], 'missing': '缺少完整业务依据',
             'suggestion': '补充规则要求的材料。'} for r in engine.RULES
        ]}, {'job_id': 'isolated-test-fixture', 'cached': False, 'usage': {}}

    def test_upload_audit_review_confirm_report_and_retry(self):
        with patch.dict(os.environ, {'AUDIT_DECISION_PROVIDER': 'off', 'AUDIT_VISUAL_OCR': 'off'}), patch.object(model, 'infer', side_effect=self.infer):
            _, queued = self.call('/api/upload', self.zip())
            engine.process(queued['id'])
            path = '/api/runs/' + queued['id']
            _, current = self.call(path)
            self.assertEqual(current['status'], 'completed')
            self.assertEqual(current['quality']['valid'], 65)
            self.assertTrue(current['summary']['complete'])
            self.assertIn('technical_checks', current)
            self.assertIn('_local_checks', self.last_prompt)
            self.assertIn('photo_metadata', self.last_prompt)
            self.assertEqual(len(current['documents']), 1)
            self.assertIn('费用凭证100', current['evidence'][0]['text'])
            self.assertNotIn('path', current['documents'][0])
            self.assertNotIn('source', current)
            original = copy.deepcopy(store.get('run', queued['id'])['results'])
            p0 = next(r['id'] for r in engine.RULES if r['risk'] == 'P0')
            code, review = self.call(path + '/review', {
                'rule_id': p0, 'verdict': '不通过', 'actor': '隔离测试人员',
                'reason': '仅用于测试人工复核留痕，不是考试判断。', 'evidence_ids': ['D001-P1']})
            self.assertEqual(code, 200)
            _, confirmed = self.call(path + '/confirm', {'actor': '隔离测试人员'})
            self.assertTrue(confirmed['confirmed_at'])
            self.assertEqual(confirmed['summary']['verdict'], '不通过')
            self.assertEqual(confirmed['summary']['blockers'], 1)
            _, generated = self.call(path + '/report', {})
            with urllib.request.urlopen(self.base + '/api/reports/' + generated['id']+'?format=html') as response:
                report = response.read().decode('utf-8')
            self.assertIn('AI财务审核', report)
            self.assertIn('逐条审核结果（65 / 65）', report)
            self.assertIn('隔离测试人员', report)
            self.assertIn('id="D001-P1"', report)
            self.assertIn('日期星期与原图 EXIF 本地核验', report)
            self.assertNotIn('<script', report)
            report_path = Path(store.get('report', generated['id'])['path'])
            snapshot = report_path.read_bytes()
            _, retried = self.call(path + '/retry', {})
            engine.process(retried['id'])
            _, new = self.call('/api/runs/' + retried['id'])
            self.assertEqual(new['parent_run'], queued['id'])
            self.assertEqual(new['source_hash'], current['source_hash'])
            self.assertFalse(new['reviews'])
            self.assertFalse(new.get('confirmed_at'))
            self.assertEqual(store.get('run', queued['id'])['results'], original)
            self.assertEqual(report_path.read_bytes(), snapshot)

    def test_model_failure_is_visible_then_retry_recovers(self):
        with patch.dict(os.environ, {'AUDIT_DECISION_PROVIDER': 'off', 'AUDIT_VISUAL_OCR': 'off'}):
            _, queued = self.call('/api/upload', self.zip())
            with patch.object(model, 'infer', side_effect=RuntimeError('测试：模型不可达')) as failed:
                engine.process(queued['id'])
            self.assertEqual(failed.call_count, 2)
            path = '/api/runs/' + queued['id']
            _, current = self.call(path)
            self.assertEqual(current['status'], 'needs_attention')
            self.assertFalse(current['summary']['complete'])
            self.assertEqual(current['summary']['counts']['通过'], 0)
            self.assertEqual(self.call(path + '/confirm', {'actor': '测试人员'})[0], 400)
            _, retried = self.call(path + '/retry', {})
            with patch.object(model, 'infer', side_effect=self.infer):
                engine.process(retried['id'])
            self.assertEqual(store.get('run', retried['id'])['status'], 'completed')
            self.assertEqual(store.get('run', queued['id'])['status'], 'needs_attention')

    def test_restart_marks_inflight_and_retains_source(self):
        _, queued = self.call('/api/upload', self.zip())
        before = store.get('run', queued['id'])
        source = Path(before['source']).read_bytes()
        server.seed()
        after = store.get('run', queued['id'])
        self.assertEqual(after['status'], 'interrupted')
        self.assertEqual(Path(after['source']).read_bytes(), source)
        self.assertEqual(after['source_hash'], before['source_hash'])

    def test_numeric_variants_and_untrusted_ocr(self):
        def audit(a, b, confirmed=True):
            return engine.deterministic({'evidence': [], 'facts': [
                {'label': '主表填报金额', 'value': a, 'evidence_id': 'D001-P1', 'confirmed': confirmed},
                {'label': '主表核报金额', 'value': b, 'evidence_id': 'D001-P1', 'confirmed': confirmed}]})
        self.assertEqual(audit('1,200.00', '1,201.00')['T1-02-02']['verdict'], '不通过')
        self.assertFalse(audit('1,200.00', '1200.00'))  # Equal numerals don't prove uppercase consistency.
        self.assertFalse(audit('100', '900', False))  # OCR candidates cannot deterministically reject.
        self.assertEqual(audit('1..20', '120')['T1-02-02']['verdict'], '待人工判断')

    def test_weekday_conflict_remains_manual_candidate(self):
        out = engine.deterministic({'facts': [], 'evidence': [
            {'id': 'D001-P1', 'category': '现场照片', 'method': 'Windows OCR',
             'text': '2026年04月18日 星期一'}]})
        self.assertEqual(out['T1-03-01']['verdict'], '待人工判断')

    def test_uncertain_amount_cannot_be_approved_by_model(self):
        values, meta = self.infer()
        for result in values['results']:
            result.update(verdict='通过', missing='')
        facts = [{'label': label, 'value': value, 'evidence_id': 'D001-P1', 'confirmed': True}
                 for label, value in [('主表填报金额', '1..20'), ('主表核报金额', '120')]]
        with patch.dict(os.environ, {'AUDIT_DECISION_PROVIDER': 'off', 'AUDIT_VISUAL_OCR': 'off'}), patch.object(model, 'infer', return_value=(values, meta)), patch.object(engine, 'facts_from', return_value=facts):
            _, queued = self.call('/api/upload', self.zip())
            engine.process(queued['id'])
        current = store.get('run', queued['id'])
        self.assertEqual(current['status'], 'completed')
        amount = next(r for r in current['results'] if r['id'] == 'T1-02-02')
        self.assertEqual(amount['verdict'], '待人工判断')
        self.assertEqual(engine.summary(current)['verdict'], '待人工判断')


if __name__ == '__main__':
    unittest.main()
