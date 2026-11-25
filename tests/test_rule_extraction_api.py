"""Exercise document upload, model interpretation and draft adoption through HTTP."""
import time
import unittest
from unittest.mock import patch
from urllib.parse import quote

from app import extraction_jobs, model, store
import test_platform_workflow as fixtures


class RuleExtractionAPI(unittest.TestCase):
    setUp = fixtures.PlatformWorkflow.setUp
    start_service = fixtures.PlatformWorkflow.start_service
    address = staticmethod(fixtures.PlatformWorkflow.address)
    request = fixtures.PlatformWorkflow.request
    base = '/api/schemes/contract/extractions'

    def test_scoped_job_to_draft_to_published_version(self):
        source = '采购付款必须有签章合同。'
        rule_name = '付款签章合同核验'
        generated = {'name':rule_name, 'principle':'复核付款对应的合同签章。',
                     'pass_condition':'已提供签章合同。','fail_condition':'经查合同未经签章。',
                     'manual_condition':'签章无法辨认时转人工。','questions':[],
                     'sources':[{'segment_id':'S0001','quote':source}]}
        verdict = {'id':'','verdict':'待人工判断','facts':'付款样本没有签章信息','basis':'缺少签章依据',
                   'evidence_ids':['D001-P1'],'missing':'签章材料','suggestion':'补充签章合同'}
        def infer(prompt,schema,*args,**kwargs):
            if 'candidates' in schema['properties']:
                self.assertIn(source,prompt)
                output={'candidates':[generated], 'ambiguities':[]}
            else:
                self.assertIn(rule_name,prompt)
                self.assertNotIn('"provenance"',prompt)
                output={'results':[verdict]}
            return output, {'engine':'固定测试响应','seconds':0}
        self.stack.enter_context(patch.object(model,'available',return_value=True))
        self.stack.enter_context(patch.object(model,'infer',side_effect=infer))
        original_revision = self.request('/api/schemes/contract')[1]['revision']
        code,job = self.request(self.base,source.encode(),{'X-Filename':quote('采购制度.txt')})
        self.assertEqual(code,202)
        url = self.base+'/'+job['id']
        for _ in range(100):
            _,job = self.request(url)
            if job['status'] in ('failed','completed'): break
            time.sleep(.03)
        self.assertEqual(job['status'],'completed',job)
        self.assertEqual(self.request(url+'/original'),(200,source.encode()))
        self.assertEqual(self.request(url.replace('contract','carrier'))[0],404)
        self.assertEqual(self.request(url.replace('contract','carrier')+'/original')[0],404)
        candidate = job['candidates'][0]
        self.assertEqual(candidate['sources'][0]['quote'],source)
        _, before = self.request('/api/schemes/contract')
        self.assertEqual(before['revision'],original_revision)
        code,detail = self.request(url+'/adopt',{'revision':before['revision'],'candidate_ids':[candidate['id']]})
        self.assertEqual(code,200,detail)
        adopted = detail['draft']['rules'][-1]
        self.assertEqual(adopted['provenance']['sha256'],job['sha256'])
        self.assertNotIn(candidate['id'],[r['id'] for r in detail['published']['rules']])
        verdict['id'] = adopted['id']
        sample = {'name':'缺签章','pages':[{'category':'待审合同','text':'付款附件未附签章'}],
                  'expected':{adopted['id']:'待人工判断'}}
        code,trial = self.request('/api/schemes/contract/trial',{'revision':detail['revision'],'samples':[sample]})
        self.assertEqual(code,200,trial)
        self.assertTrue(trial['trial']['passed'],trial['trial'])
        code,published = self.request('/api/schemes/contract/publish',{'revision':trial['revision'],'note':'启用签章核验'})
        self.assertEqual(code,200,published)
        _, run = self.request('/api/upload',b'contract example',{'X-Filename':'one.txt','X-Scheme-Id':'contract'})
        self.assertEqual(store.get('run',run['id'])['scheme_snapshot']['rules'][-1]['id'],adopted['id'])

    def test_offline_and_restart_report_failure_without_modifying_scheme(self):
        before=self.request('/api/schemes/contract')[1]
        self.assertEqual(self.request(self.base,b'example',{'X-Filename':'one.txt'})[0],503)
        self.assertEqual(self.request(self.base)[1],[])
        store.put('rule_extraction',{'id':'a'*16,'scheme_id':'contract','status':'processing'})
        extraction_jobs.recover()
        job=self.request(self.base+'/'+'a'*16)[1]
        self.assertEqual(job['status'],'failed')
        self.assertEqual(self.request('/api/schemes/contract')[1]['revision'],before['revision'])
