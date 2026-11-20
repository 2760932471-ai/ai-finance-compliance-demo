import io,json,tempfile,threading,unittest,urllib.request,urllib.error,zipfile
from pathlib import Path
from unittest.mock import patch
from http.server import ThreadingHTTPServer
import server
from app import store,engine,evaluations

class ApiTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.patches=[patch.object(store,'DB',self.root/'audit.sqlite3'),patch.object(store,'DATA',self.root),patch.object(server.POOL,'submit')]
        for p in self.patches:p.start()
        store.init();server.seed();self.http=ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
        self.thread=threading.Thread(target=self.http.serve_forever,daemon=True);self.thread.start()
        self.base=f'http://127.0.0.1:{self.http.server_port}'
    def tearDown(self):
        self.http.shutdown();self.http.server_close();self.thread.join()
        for p in self.patches:p.stop()
        self.temp.cleanup()
    def call(self,path,data=None,headers=None,token=True):
        h={'X-Session-Token':server.TOKEN} if token else {};h.update(headers or {})
        if data is not None and not isinstance(data,bytes):data=json.dumps(data).encode();h['Content-Type']='application/json'
        try:
            with urllib.request.urlopen(urllib.request.Request(self.base+path,data=data,headers=h)) as r:return r.status,json.load(r)
        except urllib.error.HTTPError as e:return e.code,json.load(e)
    def zip(self):
        b=io.BytesIO()
        with zipfile.ZipFile(b,'w') as z:z.writestr('proof.html','<p>费用凭证100</p>');z.writestr('proof_files/resource.html','<p>原始离线资源</p>')
        return b.getvalue()
    def seed_run(self):
        raw=self.zip();_,r=self.call('/api/upload',raw);run=store.get('run',r['id'])
        run.update(status='completed',manifest=[{'status':'业务文件'}],documents=[{'id':'D001','sha256':'test','name':'proof.html','path':'unused'}],evidence=[{'id':'D001-P1','sha256':'test','text':'原识别内容','page':1,'image':'','method':'HTML静态文本'}],results=[{'id':engine.RULES[0]['id'],'name':'测试审核项','group':'T1-01','risk':'P0','principle':'测试','verdict':'不通过','facts':'测试事实','basis':'测试依据','evidence_ids':['D001-P1'],'missing':'','suggestion':'','execution':'completed'}])
        store.put('run',run);return run
    def test_write_requires_session_and_origin(self):
        self.assertEqual(self.call('/api/knowledge',{},token=False)[0],403)
        self.assertEqual(self.call('/api/knowledge',{},headers={'Origin':'https://evil.example'})[0],403)
    def test_invalid_upload_does_not_create_project(self):
        self.assertEqual(self.call('/api/upload',b'not zip')[0],400);self.assertFalse(store.all_items('project'))
    def test_upload_queued_not_passed(self):
        code,r=self.call('/api/upload',self.zip());self.assertEqual(code,201);self.assertEqual(r['status'],'queued');self.assertFalse(r['summary']['complete'])
    def test_correction_preserves_source_and_creates_new_version(self):
        run=self.seed_run();code,r=self.call('/api/runs/'+run['id']+'/correct',{'evidence_id':'D001-P1','text':'已校正内容','reason':'对照原件确认识别错误','actor':'测试校正人'})
        self.assertEqual(code,201);new=store.get('run',r['id']);self.assertEqual(new['parent_run'],run['id']);self.assertEqual(new['corrections'][0]['original_text'],'原识别内容');self.assertEqual(store.get('run',run['id'])['evidence'][0]['text'],'原识别内容')
    def test_supplement_preserves_offline_resources(self):
        run=self.seed_run();code,r=self.call('/api/runs/'+run['id']+'/supplement',b'new file',headers={'X-Filename':'new.pdf'})
        self.assertEqual(code,201)
        with zipfile.ZipFile(store.get('run',r['id'])['source']) as z:self.assertIn('proof_files/resource.html',z.namelist());self.assertEqual(len(z.namelist()),3)
    def test_review_invalid_evidence_and_confirm_incomplete(self):
        run=self.seed_run();path='/api/runs/'+run['id']
        self.assertEqual(self.call(path+'/review',{'rule_id':'T1-01-01','verdict':'通过','actor':'测试','reason':'测试引用不存在的证据','evidence_ids':['FAKE']})[0],400)
        self.assertEqual(self.call(path+'/confirm',{'actor':'测试'})[0],400)
        code,review=self.call(path+'/review',{'rule_id':'T1-01-01','verdict':'待人工判断','actor':'测试复核人','reason':'功能测试：需补充真实核验依据','evidence_ids':['D001-P1']})
        self.assertEqual(code,200);self.assertEqual(review['before'],'不通过')
        _,public=self.call(path)
        self.assertEqual(public['results'][0]['verdict'],'待人工判断');self.assertEqual(public['results'][0]['ai_verdict'],'不通过')
        self.assertEqual(store.get('run',run['id'])['results'][0]['verdict'],'不通过')
    def test_feedback_regression_requires_expectation(self):
        run=self.seed_run();f=store.put('feedback',{'id':'f','run_id':run['id'],'rule_id':'T1-01-01','body':'需验证规则','status':'已核实'})
        self.assertEqual(self.call('/api/feedback/status',{'id':'f','status':'已纳入回归'})[0],400)
        self.assertEqual(self.call('/api/feedback/status',{'id':'f','status':'已纳入回归','expected':'不通过','actor':'测试确认人','reason':'根据原件确认该项不符合标准'})[0],200)
        code,e=self.call('/api/evaluations',{});self.assertEqual(code,200);self.assertEqual(e['matched'],1)
        # A manual override must not inflate the raw-model evaluation score.
        store.put('review',{'id':'v','run_id':run['id'],'rule_id':'T1-01-01','after':'通过','time':'now','evidence_ids':['D001-P1']})
        self.assertEqual(evaluations.evaluate()['matched'],1)
    def test_knowledge_publish_gate(self):
        _,k=self.call('/api/knowledge',{'title':'测试知识','text':'测试金额口径','source':'测试制度'})
        _,found=self.call('/api/knowledge/search?q=%E6%B5%8B%E8%AF%95');self.assertFalse(any(x['id']==k['id'] for x in found))
        self.call('/api/knowledge/status',{'id':k['id'],'status':'published'})
        _,found=self.call('/api/knowledge/search?q=%E6%B5%8B%E8%AF%95');self.assertTrue(any(x['id']==k['id'] for x in found))
        self.assertEqual(self.call('/api/knowledge/status',{'id':'official-t1','status':'disabled'})[0],400)

if __name__=='__main__':unittest.main()
