import copy,os,tempfile,unittest
from pathlib import Path
from unittest.mock import patch,Mock
from app import knowledge,model
from compliance import graphdata
from compliance.domain import build_graph
from compliance.store import Store

class KnowledgeTests(unittest.TestCase):
    def doc(self,text,status='published'):
        return {'id':'k','title':'采购制度','source':'演示制度','version':'v1','status':status,'text':text}
    def test_chunk_offsets_cover_text_with_overlap(self):
        doc=self.doc(('付款前应完成验收。\n'*70));chunks=knowledge.chunks(doc,{'size':100,'overlap':20,'mode':'paragraph'})
        self.assertGreater(len(chunks),1)
        for c in chunks:self.assertEqual(c['text'],doc['text'][c['start']:c['end']])
        self.assertEqual(chunks[-1]['end'],len(doc['text']))
        self.assertEqual(chunks[1]['start'],chunks[0]['end']-20)
    def test_published_only_and_no_fabricated_hits(self):
        docs=[self.doc('付款之前必须验收，付款材料应包含验收记录。'),dict(self.doc('秘密口令','draft'),id='secret')]
        for method in ('bm25','tfidf','hybrid'):
            r=knowledge.retrieve('付款验收',3,method,docs);self.assertEqual(r['results'][0]['id'],'k');self.assertGreater(r['results'][0]['score'],0)
            self.assertEqual(knowledge.retrieve('秘密口令',3,method,docs)['results'],[])
    def test_chunk_version_and_limits(self):
        d=self.doc('采购制度');x=knowledge.chunks(d);d['version']='v2';self.assertNotEqual(x[0]['chunk_id'],knowledge.chunks(d)[0]['chunk_id'])
        with self.assertRaises(ValueError):knowledge.config({'size':100,'overlap':50})
        with self.assertRaises(ValueError):knowledge.retrieve('采购',top_k=30,docs=[d])

class DiagnosticTests(unittest.TestCase):
    def test_offline_is_not_missing_model(self):
        with patch.dict(os.environ,{'AUDIT_AI':'off'}),patch.object(model,'executable',return_value='/bin/sh'):
            d=model.diagnostic();self.assertTrue(d['executable_found']);self.assertIn('离线演示',d['status']);self.assertEqual(d['inference'],'尚未验证')
    def test_login_never_leaks_output(self):
        with patch.object(model,'executable',return_value='/bin/sh'),patch.object(model.subprocess,'run',return_value=Mock(returncode=0,stdout='SECRET')):
            d=model.diagnostic(True);self.assertEqual(d['login'],'已登录');self.assertNotIn('SECRET',str(d))

class GraphSourceTests(unittest.TestCase):
    def test_sources_attached_to_edges_and_missing_person_not_invented(self):
        r=graphdata.scenario()[0];r['person']='';r['source_kind']='source';g=build_graph([r])
        self.assertFalse(any(n['type']=='person' for n in g['nodes']))
        self.assertTrue(all(e['source_kinds']==['source'] and e['record_ids']==[r['id']] for e in g['edges']))
    def test_separate_from_audit_data(self):
        with tempfile.TemporaryDirectory() as folder:
            s=Store(Path(folder)/'db.sqlite3');before=s.state()['dataset'];graphdata.save(s,{'revision':0,'source':'测试','rows':graphdata.scenario()})
            self.assertEqual(before,s.state()['dataset']);self.assertEqual(graphdata.state(s)['revision'],1)
    def test_source_kind_required(self):
        rows=graphdata.scenario();del rows[0]['source_kind']
        with self.assertRaises(ValueError):graphdata.validate(rows)
