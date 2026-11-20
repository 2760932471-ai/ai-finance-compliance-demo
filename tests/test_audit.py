import copy,io,json,os,stat,tempfile,unittest,zipfile
from pathlib import Path
from unittest.mock import patch
from app import engine,harness,jev,materials,report,store

class AuditTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.db=patch.object(store,'DB',self.root/'test.sqlite3');self.data=patch.object(store,'DATA',self.root)
        self.db.start();self.data.start();store.init()
    def tearDown(self):self.db.stop();self.data.stop();self.temp.cleanup()
    def zipped(self,entries):
        path=self.root/(store.uid()+'.zip')
        with zipfile.ZipFile(path,'w') as z:
            for name,body in entries:z.writestr(name,body)
        return path
    def evidence(self):return [{'id':'D001-P1','name':'凭证.pdf','page':1,'document_id':'D001','category':'报销主单','image':'','text':'费用为100元','method':'PDF文本','sha256':'test'}]
    def result(self,rid='T1-01-01',**kw):return {'id':rid,'verdict':'通过','facts':'原文显示100元','basis':'对应规则已核对','evidence_ids':['D001-P1'],'missing':'','suggestion':'',**kw}
    def full_run(self):
        rs=[{**self.result(r['id']),**{k:r[k] for k in ('name','group','risk','principle')},'engine':'测试模型','execution':'completed'} for r in engine.RULES]
        store.put('project',{'id':'p','name':'验证项目<script>alert(1)</script>'})
        return {'id':'r','project_id':'p','rules':engine.RULES,'results':rs,'evidence':self.evidence(),'manifest':[]}
    def test_official_rule_coverage(self):
        self.assertEqual(len(engine.RULES),65)
        self.assertEqual([sum(r['group']==g for r in engine.RULES) for g in ['T1-01','T1-02','T1-03','T1-04','T1-05']],[13,15,22,8,7])
        self.assertEqual(len(set(r['id'] for r in engine.RULES)),65)
    def test_zip_traversal_absolute_and_ads(self):
        for name in ['../evil.pdf','C:/evil.pdf','/evil.pdf','a/../../evil.pdf','a\\..\\evil.pdf','a.pdf:evil']:
            with self.subTest(name=name),self.assertRaises(ValueError):materials.unpack(self.zipped([(name,b'x')]),self.root/store.uid())
    def test_zip_symlink_rejected(self):
        i=zipfile.ZipInfo('link.pdf');i.create_system=3;i.external_attr=(stat.S_IFLNK|0o777)<<16
        with self.assertRaises(ValueError):materials.unpack(self.zipped([(i,b'outside')]),self.root/'symlink')
    def test_zip_limits(self):
        z=self.zipped([('a.pdf',b'x'*100)])
        with patch.object(materials,'MAX_TOTAL',99),self.assertRaises(ValueError):materials.unpack(z,self.root/'total')
    def test_dedupe_stable_ids(self):
        a=self.zipped([('folder/甲.pdf',b'b'),('副本.pdf',b'b'),('乙.html',b'a'),('__MACOSX/._a',b'x')])
        b=self.zipped([('乙.html',b'a'),('甲.pdf',b'b')])
        ma,da=materials.unpack(a,self.root/'a');mb,db=materials.unpack(b,self.root/'b')
        self.assertEqual([(d['id'],d['sha256']) for d in da],[(d['id'],d['sha256']) for d in db])
        self.assertEqual(len(da),2);self.assertEqual(sum(m['status']=='重复业务文件' for m in ma),1)
        duplicate=next(m for m in ma if m['status']=='重复业务文件')
        self.assertIn(duplicate['document_id'],[d['id'] for d in da])
    def test_static_html_ignores_scripts(self):
        text=materials.html_text(b'<div>Evidence</div><script>fetch("steal")</script><style>x</style><input type="password" value="secret"><input value="100">')
        self.assertIn('Evidence',text);self.assertIn('100',text);self.assertNotIn('fetch',text);self.assertNotIn('secret',text)
    def test_output_missing_duplicate_and_fabricated_evidence(self):
        rs=engine.RULES[:2];es=self.evidence()
        for output in [[self.result()], [self.result(),self.result()], [self.result(),self.result(rs[1]['id'],evidence_ids=['invented'])]]:
            self.assertTrue(harness.inspect(output,rs,es)[1])
        self.assertTrue(harness.inspect([self.result(evidence_ids=[],missing='缺失')],engine.RULES[:1],es)[1])
    def test_targeted_retry_preserves_accepted(self):
        rules=engine.RULES[:2];calls=[]
        def infer(prompt,*args,**kw):
            calls.append(prompt)
            if len(calls)==1:return {'results':[self.result(),self.result(rules[1]['id'],evidence_ids=['wrong'])]},{'job_id':'one'}
            return {'results':[self.result(rules[1]['id'])]},{'job_id':'two'}
        vals,errors,meta,traces=harness.execute(rules,self.evidence(),[],{},infer=infer)
        self.assertEqual(len(vals),2);self.assertFalse(errors);self.assertEqual(traces[1]['rule_ids'],[rules[1]['id']])
        self.assertEqual(len(calls),2);self.assertEqual(traces[0]['accepted'],1)
    def test_retry_bounded(self):
        calls=[]
        def infer(*args,**kw):calls.append(1);raise RuntimeError('网络故障')
        vals,errors,_,traces=harness.execute(engine.RULES[:1],self.evidence(),[],{},infer=infer)
        self.assertEqual(len(calls),2);self.assertFalse(vals);self.assertTrue(errors)
    def test_incomplete_cannot_pass(self):
        r=self.full_run();r['results'].pop();self.assertEqual(engine.summary(r)['verdict'],'待人工判断')
        self.assertFalse(engine.summary(r)['complete'])
    def test_p0_fail_cannot_be_diluted(self):
        r=self.full_run();p0=next(x for x in r['results'] if x['risk']=='P0');p0['verdict']='不通过'
        self.assertEqual(engine.summary(r)['verdict'],'不通过');self.assertEqual(engine.summary(r)['blockers'],1)
    def test_review_preserves_ai_and_uses_new_refs(self):
        r=self.full_run();store.put('review',{'id':'v','run_id':'r','rule_id':r['results'][0]['id'],'after':'不通过','before':'通过','time':'2026-01-01','actor':'测试复核人','reason':'人工对照原件发现不一致','evidence_ids':[]})
        result=engine.effective_results(r)[0]
        self.assertEqual(result['ai_verdict'],'通过');self.assertEqual(result['verdict'],'不通过');self.assertEqual(r['results'][0]['verdict'],'通过')
        self.assertEqual(result['evidence_ids'],[])
    def test_jev_never_auto_approves(self):
        answer={'type':'choice','choice':'supported','confidence':.95,'probabilities':{'supported':.98,'unsupported':.01,'uncertain':.01}}
        self.assertEqual(jev.apply_gate(self.result(verdict='不通过'),answer)['verdict'],'不通过')
        low={**answer,'confidence':.3}
        self.assertEqual(jev.apply_gate(self.result(),low)['verdict'],'待人工判断')
        self.assertEqual(jev.apply_gate(self.result(verdict='待人工判断'),answer)['verdict'],'待人工判断')
    def test_jev_malformed_probabilities_rejected(self):
        with self.assertRaises(ValueError):jev.validate({'type':'choice','choice':'supported','confidence':float('nan'),'probabilities':{}})
    def test_jev_disabled_is_explicit(self):
        with patch.dict(os.environ,{'AUDIT_JEV':'off'}):self.assertEqual(jev.check([],[])['status'],'未启用')
    def test_offline_report_escape_and_complete(self):
        r=self.full_run();generated=report.generate(r);html=Path(generated['path']).read_text(encoding='utf-8')
        self.assertNotIn('<script>',html);self.assertIn('&lt;script&gt;',html)
        self.assertIn('逐条审核结果（65 / 65）',html);self.assertIn('id="D001-P1"',html)
        self.assertNotIn('src="http',html);self.assertIn('执行检查与版本快照',html)
    def test_persistence(self):
        store.put('review',{'id':'one','why':'保留'});self.assertEqual(store.get('review','one')['why'],'保留')

if __name__=='__main__':unittest.main()
