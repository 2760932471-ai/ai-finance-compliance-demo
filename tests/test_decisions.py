import copy,json,os,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from app import decisions

class DecisionTests(unittest.TestCase):
    def answer(self,options,choice):
        return {'type':'choice','choice':choice,'confidence':.98,'probabilities':{k:.99 if k==choice else .005 for k in options}}
    def response(self):
        return {'model':'StartLux-Decision-4B','answers':{'support':self.answer(decisions.SUPPORT,'supported'),'verdict':self.answer(decisions.VERDICTS,'meets')},'usage':{}}
    def cfg(self,**kw):
        return {'provider':'startlux','model':'StartLux-Decision-4B','endpoint':'http://127.0.0.1:8090/v1/systemone','key':'','mode':'shadow','threshold':.8,'images':False,'error':'',**kw}
    def result(self,**kw):return {'id':'T1-01-01','principle':'金额必须100元','facts':'单据金额100元','verdict':'通过','basis':'原件可见','evidence_ids':['D001-P1'],'execution':'completed',**kw}
    def evidence(self):return [{'id':'D001-P1','text':'金额100元','method':'PDF文本','page':1,'sha256':'x','image':''}]
    def test_default_off_never_calls(self):
        with patch.dict(os.environ,{'AUDIT_DECISION_PROVIDER':'off'}),patch.object(decisions,'transport') as call:
            self.assertEqual(decisions.check([self.result()],self.evidence())['status'],'未启用');call.assert_not_called()
    def test_p0_scope_is_explicit_and_does_not_drop_primary_results(self):
        rs=[self.result(id='a',risk='P0'),self.result(id='b',risk='未指定')]
        with patch.object(decisions,'config',return_value=self.cfg(scope='p0')),patch.object(decisions,'request',return_value=(self.response(),{})):
            report=decisions.check(rs,self.evidence())
        self.assertEqual((report['count'],report['target_count'],report['total_rules']),(1,1,2))
        self.assertIn('decision',rs[0]);self.assertNotIn('decision',rs[1]);self.assertEqual(len(rs),2)
    def test_runtime_health_is_not_inferred_from_configuration(self):
        with patch.object(decisions,'config',return_value=self.cfg()),patch.object(decisions,'health',return_value={'reachable':False,'reason':'服务不可达'}):
            self.assertEqual(decisions.status()['status'],'服务不可达')
        with patch.object(decisions,'config',return_value=self.cfg()),patch.object(decisions,'health',return_value={'reachable':True}):
            self.assertEqual(decisions.status()['status'],'服务已连接')
    def test_timeout_configuration_is_bounded(self):
        with patch.dict(os.environ,{'AUDIT_DECISION_PROVIDER':'startlux','STARTLUX_ENDPOINT':'http://127.0.0.1:8090/v1/systemone','DECISION_TIMEOUT':'0'}):
            self.assertTrue(decisions.config()['error'])
    def test_unknown_provider_and_insecure_endpoint_are_explicit(self):
        with patch.dict(os.environ,{'AUDIT_DECISION_PROVIDER':'wrong'}):self.assertTrue(decisions.config()['error'])
        with patch.dict(os.environ,{'AUDIT_DECISION_PROVIDER':'startlux','STARTLUX_ENDPOINT':'http://remote.test/v1/systemone'}):self.assertTrue(decisions.config()['error'])
    def test_shadow_keeps_verdict_on_disagreement(self):
        r=self.result();a=self.response();a['answers']['verdict']=self.answer(decisions.VERDICTS,'violates')
        decisions.apply(r,a,self.cfg(),{},{});self.assertEqual(r['verdict'],'通过');self.assertTrue(r['decision']['needs_review'])
    def test_gate_never_approves_rejection_and_low_confidence_goes_manual(self):
        r=self.result(verdict='不通过',risk='P0');decisions.apply(r,self.response(),self.cfg(mode='gate'),{},{});self.assertEqual(r['verdict'],'不通过')
        r=self.result();a=self.response();a['answers']['support']['confidence']=.3
        decisions.apply(r,a,self.cfg(mode='gate'),{},{});self.assertEqual(r['verdict'],'待人工判断');self.assertEqual(r['pre_decision_verdict'],'通过')
    def test_truncation_forces_gate_review(self):
        r=self.result();decisions.apply(r,self.response(),self.cfg(mode='gate'),{}, {'truncated':True});self.assertEqual(r['verdict'],'待人工判断')
    def test_response_validation(self):
        for mutation in ['nan','missing','wrong_model','bad_choice']:
            a=self.response()
            if mutation=='nan':a['answers']['support']['confidence']=float('nan')
            if mutation=='missing':a['answers'].pop('verdict')
            if mutation=='wrong_model':a['model']='unexpected'
            if mutation=='bad_choice':a['answers']['support']['choice']='invented'
            with self.subTest(mutation=mutation),self.assertRaises(ValueError):decisions.validate(a,self.cfg())
    def test_images_opt_in_and_evidence_mapping(self):
        with tempfile.TemporaryDirectory() as t:
            p=Path(t)/'image.jpg';p.write_bytes(b'unit-test-image');e=self.evidence();e[0]['image']=str(p)
            for cfg,expected in [(self.cfg(provider='jev',images=True),False),(self.cfg(images=False),False),(self.cfg(images=True),True)]:
                body,meta=decisions.payload(self.result(),e,cfg)
                self.assertEqual('images' in body,expected)
                self.assertEqual(body['state']['evidence'][0]['image_number'],1 if expected else None)
                self.assertNotIn(str(p),json.dumps(body))
    def test_cache_bound_to_provider_and_content(self):
        with tempfile.TemporaryDirectory() as t,patch.object(decisions.store,'DATA',Path(t)):
            calls=[]
            def invoke(*args):calls.append(1);return self.response(),{'request_id':'test'}
            with patch.object(decisions,'transport',side_effect=invoke):
                cfg=self.cfg();b,m=decisions.payload(self.result(),self.evidence(),cfg)
                decisions.request(cfg,b);_,hit=decisions.request(cfg,b);self.assertTrue(hit['cached'])
                altered=copy.deepcopy(b);altered['state']['evidence'][0]['text']='金额200元';decisions.request(cfg,altered)
                decisions.request(self.cfg(endpoint='http://127.0.0.1:8091/v1/systemone'),b)
                decisions.request(self.cfg(revision='new-weights'),b)
            self.assertEqual(len(calls),4)
    def test_service_failure_records_and_gates_all_remaining_passes(self):
        rs=[self.result(id='a'),self.result(id='b')]
        with patch.object(decisions,'config',return_value=self.cfg(mode='gate')),patch.object(decisions,'request',side_effect=RuntimeError('offline')):
            report=decisions.check(rs,self.evidence())
        self.assertEqual(report['status'],'部分失败');self.assertTrue(all(r['verdict']=='待人工判断' for r in rs))

if __name__=='__main__':unittest.main()
