"""Original audit → confirmed facts → risk snapshot → original evidence contract."""
import copy
import json
import unittest
from app import store
import test_api


class IntegrationTests(unittest.TestCase):
    setUp = test_api.ApiTests.setUp
    tearDown = test_api.ApiTests.tearDown
    call = test_api.ApiTests.call
    zip = test_api.ApiTests.zip
    seed_run = test_api.ApiTests.seed_run

    def capture_payload(self):
        source = self.seed_run()
        _, state = self.call('/api/compliance/state')
        row = copy.deepcopy(state['dataset']['rows'][30])
        row['id'] = 'SOURCE-001'
        return {'row': row, 'source_run': source['id'], 'evidence_ids': ['D001-P1'],
                'actor': '测试财务', 'note': '对照合同和支付凭证逐项核实',
                'revision': state['dataset']['revision'], 'replace_demo': True}, source

    def test_original_evidence_to_risk_and_report_snapshot(self):
        payload, original = self.capture_payload()
        before = copy.deepcopy(store.get('run', original['id']))
        code, state = self.call('/api/compliance/capture', payload)
        self.assertEqual(code, 200)
        self.assertEqual(len(state['dataset']['rows']), 1)
        row = state['dataset']['rows'][0]
        self.assertEqual(row['provenance']['run_id'], original['id'])
        self.assertEqual(len(row['provenance']['evidence_sha256']), 64)
        code, risk = self.call('/api/compliance/run', {})
        self.assertEqual(code, 200)
        result = risk['results'][0]
        # Multiple flow checks remain one dimension; too few peers cannot invent statistics.
        self.assertEqual(result['status'], 'clue')
        self.assertEqual(result['event']['provenance'], row['provenance'])
        self.assertTrue(any(not c['evaluated'] for c in result['checks']))
        self.assertEqual(self.call('/api/compliance/review', {'run_id': risk['id'], 'result_id': result['id'],
            'status': 'confirmed', 'actor': '测试财务', 'note': '测试不能单维定性'})[0], 400)
        self.assertEqual(self.call('/api/compliance/review', {'run_id': risk['id'], 'result_id': result['id'],
            'status': 'pending', 'actor': '测试财务', 'note': '等待补充交叉证据'})[0], 200)
        _, snapshot = self.call('/api/compliance/export/json?run=' + risk['id'])
        self.assertEqual(snapshot['dataset']['rows'][0]['provenance'], row['provenance'])
        self.assertEqual(store.get('run', original['id']), before)
        code, source = self.call('/api/runs/' + original['id'])
        self.assertEqual(code, 200)
        self.assertEqual(source['evidence'][0]['id'], 'D001-P1')

    def test_requires_explicit_demo_replacement(self):
        payload, _ = self.capture_payload()
        payload['replace_demo'] = False
        self.assertEqual(self.call('/api/compliance/capture', payload)[0], 400)
        _, state = self.call('/api/compliance/state')
        self.assertEqual(len(state['dataset']['rows']), 34)

    def test_missing_fields_and_fabricated_reference_never_persist(self):
        payload, _ = self.capture_payload()
        missing = copy.deepcopy(payload)
        del missing['row']['amount']
        self.assertEqual(self.call('/api/compliance/capture', missing)[0], 400)
        payload['evidence_ids'] = ['D999-P1']
        self.assertEqual(self.call('/api/compliance/capture', payload)[0], 400)
        _, state = self.call('/api/compliance/state')
        self.assertEqual(state['dataset']['revision'], 1)

    def test_import_rejects_foreign_or_changed_evidence(self):
        payload, original = self.capture_payload()
        _, state = self.call('/api/compliance/capture', payload)
        row = state['dataset']['rows'][0]
        original['evidence'][0]['text'] = '人为更改的原件文字'
        store.put('run', original)
        self.assertEqual(self.call('/api/compliance/import', {'rows': [row], 'source': '回导',
            'revision': state['dataset']['revision']})[0], 400)
        row['provenance']['run_id'] = 'f' * 16
        self.assertEqual(self.call('/api/compliance/import', {'rows': [row], 'source': '回导',
            'revision': state['dataset']['revision']})[0], 400)

    def test_original_session_protects_compliance_writes(self):
        self.assertEqual(self.call('/api/compliance/run', {}, token=False)[0], 403)
        self.assertEqual(self.call('/api/compliance/run', {}, headers={'Origin': 'https://evil.example'})[0], 403)
        self.assertEqual(self.call('/api/compliance/state', headers={'Host': 'evil.example'})[0], 403)

    def test_configuration_publish_does_not_rewrite_captured_run(self):
        payload, _ = self.capture_payload()
        _, state = self.call('/api/compliance/capture', payload)
        _, old = self.call('/api/compliance/run', {})
        config = state['draft']['config']
        config['models'][0]['high'] = 85
        _, saved = self.call('/api/compliance/config', {'config': config, 'revision': state['draft']['revision']})
        code, published = self.call('/api/compliance/publish', {'revision': saved['draft']['revision'], 'note': '提高高风险分值起点'})
        self.assertEqual(code, 200)
        self.assertEqual(published['active_version'], 2)
        _, preserved = self.call('/api/compliance/run?id=' + old['id'])
        self.assertEqual(preserved['version'], 1)
        self.assertEqual(preserved['config'], old['config'])
        self.assertEqual(preserved['dataset'], old['dataset'])

    def test_statistical_lab_http_and_guards(self):
        self.assertEqual(self.call('/api/compliance/lab/run', {}, token=False)[0],403)
        code,s=self.call('/api/compliance/lab/state');self.assertEqual(code,200)
        self.assertEqual(len(s['dataset']['rows']),1440)
        self.assertEqual(self.call('/api/compliance/lab/run', {'model_id':'price','start':'invalid','end':'2026-10'})[0],400)
        code,run=self.call('/api/compliance/lab/run', {'model_id':'price','start':'2026-10','end':'2026-10'})
        self.assertEqual(code,200);self.assertEqual(run['result']['metrics']['tp'],2)
        self.assertEqual(self.call('/api/compliance/lab/export?id='+run['id'])[1]['dataset_sha256'],run['dataset_sha256'])
        self.assertEqual(self.call('/api/compliance/lab/run?id=unknown')[0],404)
        self.assertEqual(self.call('/api/compliance/lab/save', {'revision':0,'models':s['draft']})[0],409)


if __name__ == '__main__':
    unittest.main()
