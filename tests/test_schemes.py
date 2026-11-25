"""Scheme publication and evidence-bound checks using a temporary local DB."""
import copy
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app import engine, model, schemes, store


class SchemeTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        db=patch.object(store,'DB',Path(temp.name)/'test.sqlite3')
        db.start()
        self.addCleanup(db.stop)
        store.init()

    def test_builtin_trials_and_immutable_versions(self):
        for scheme_id in ('finance-t1','contract','carrier'):
            result=schemes.trial(scheme_id,{'revision':0})['trial']
            self.assertTrue(result['passed'],result)
        draft=schemes.detail('contract')['draft']
        draft['report']['title']='合同审阅备忘录'
        updated=schemes.save('contract',{'revision':0,'draft':draft})
        with self.assertRaises(ValueError):schemes.publish('contract',{'revision':1,'note':'跳过试跑'})
        trial=schemes.trial('contract',{'revision':updated['revision']})
        self.assertTrue(trial['trial']['passed'])
        result=schemes.publish('contract',{'revision':1,'note':'更新报告名称'})
        self.assertEqual(result['published']['version'],2)
        self.assertEqual(schemes.published('contract',1)['report']['title'],'合同条款审核报告')
        with self.assertRaises(ValueError):schemes.save('contract',{'revision':1,'draft':draft})

    def test_mismatch_or_untested_rule_prevents_publication(self):
        detail=schemes.detail('carrier')
        rule=copy.deepcopy(detail['draft']['rules'][0])
        rule.update(id='CAR-04',name='新标识核验')
        detail['draft']['rules'].append(rule)
        schemes.save('carrier',{'revision':0,'draft':detail['draft']})
        trial=schemes.trial('carrier',{'revision':1})
        self.assertFalse(trial['trial']['passed'])
        self.assertIn('CAR-04',trial['trial']['uncovered_changes'])
        samples=detail['sample_examples']
        for sample in samples:sample['expected']['CAR-04']=sample['expected']['CAR-01']
        samples[1]['expected']['CAR-01']='通过'
        trial=schemes.trial('carrier',{'revision':1,'samples':samples})
        self.assertFalse(trial['trial']['passed'])
        self.assertLess(trial['trial']['matched'],trial['trial']['total'])

    def test_custom_draft_discovery_and_applicability(self):
        draft=copy.deepcopy(schemes.BASE['contract'])
        draft.update(id='sample')
        draft['rules']=draft['rules'][:2]
        draft['rules'][0]['applies_to']=['标准模板']
        schemes.save('sample',{'revision':0,'draft':draft})
        self.assertIn('sample',[x['id'] for x in schemes.manage_list()])
        self.assertNotIn('sample',[x['id'] for x in schemes.list_schemes()])
        sample={'name':'合同样本','pages':[{'category':'待审合同','text':'甲方名称：甲\n乙方名称：乙'}],
                'expected':{'CON-01':'不适用','CON-02':'通过'}}
        trial=schemes.trial('sample',{'revision':1,'samples':[sample]})
        self.assertFalse(trial['trial']['passed'])
        self.assertEqual(trial['trial']['cases'][0]['actual'],'不适用')
        samples=[sample,{'name':'模板与合同','pages':[{'category':'标准模板','text':'模板付款起算条件：30天'},
                                                 {'category':'待审合同','text':'合同付款起算条件：30天'}],
                         'expected':{'CON-01':'通过'}}]
        self.assertTrue(schemes.trial('sample',{'revision':1,'samples':samples})['trial']['passed'])
        self.assertEqual(schemes.publish('sample',{'revision':1,'note':'发布'})['published']['version'],1)

    def test_scope_and_integrity_of_inference(self):
        docs=[{'id':'legacy'}, {'id':'new','scheme_id':'carrier'}, {'id':'in','scheme_id':'contract'},
              {'id':'out','scheme_id':'contract'}, {'id':'demo','scheme_id':'contract','demo':True}]
        self.assertEqual([x['id'] for x in schemes.knowledge_docs(schemes.BASE['finance-t1'],docs)],['legacy'])
        self.assertEqual([x['id'] for x in schemes.knowledge_docs(dict(schemes.BASE['contract'],knowledge_ids=['in','new']),docs)],['in'])
        rules=schemes.BASE['carrier']['rules'][:2]
        complete=[{'id':r['id'],'verdict':'通过','execution':'completed'} for r in rules]
        self.assertTrue(engine.summarize_results(complete,rules)['complete'])
        self.assertFalse(engine.summarize_results([complete[0]]*2,rules)['complete'])
        self.assertFalse(engine.summarize_results([],[])['complete'])
        self.assertFalse(engine.summary({'id':'run','rules':rules,'results':complete+[complete[0]]})['complete'])
        self.assertNotIn('经销商',model.system_for(schemes.BASE['carrier']))
        self.assertIn('载具审核',model.system_for(schemes.BASE['carrier']))

    def test_uncertain_and_conflicting_fields_stay_manual(self):
        rule=schemes.BASE['carrier']['rules'][0]
        pages=[{'id':'D001-P1','text':'登记载具编号：A123\n现场载具编号：A123'}]
        self.assertEqual(schemes.field_result(rule,pages)['verdict'],'通过')
        self.assertEqual(schemes.field_result(rule,[*pages,dict(id='D002-P1',text='现场载具编号：B123')])['verdict'],'待人工判断')
        self.assertEqual(schemes.field_result(rule,[dict(pages[0],recognition={'status':'partial'})])['verdict'],'待人工判断')
        self.assertEqual(schemes.field_result(rule,[dict(pages[0],recognition={'status':'partial'},confirmed=True)])['verdict'],'通过')

    def test_directed_question_flows_into_model_and_chat_without_deciding_result(self):
        from app import harness
        task={'id':'task-1','question':'为何登记编号重复？','row_id':'row-a','org_id':'org-1'}
        rule=schemes.BASE['carrier']['rules'][2]
        seen=[]
        def infer(prompt,schema,images,**kw):
            seen.append(prompt)
            if schema==model.CHAT_SCHEMA:
                return {'answer':'证据不足','rule_ids':[rule['id']],'evidence_ids':[]},{}
            return {'results':[{'id':rule['id'],'facts':'现场原件未提供','basis':'需补拍',
                    'verdict':'待人工判断','evidence_ids':[],'missing':'照片','suggestion':'补证'}]},{}
        vals,_,_,traces=harness.execute([rule],[],[],{},infer=infer,scheme=schemes.BASE['carrier'],task=task)
        _,_,_,regular=harness.execute([rule],[],[],{},infer=infer,scheme=schemes.BASE['carrier'])
        self.assertIn(task['question'],seen[0]);self.assertNotIn(task['question'],seen[1])
        self.assertNotEqual(traces[0]['input_hash'],regular[0]['input_hash'])
        self.assertEqual(vals[0]['verdict'],'待人工判断')
        with patch.object(model,'infer',side_effect=infer):
            engine.chat({'id':'r','rules':[rule],'results':vals,'scheme_snapshot':schemes.BASE['carrier'],
                         'supervision_task':task},'说明原因')
        self.assertIn(task['question'],seen[-1])

    def test_field_values_carry_word_locator(self):
        p={'id':'D001-P1','text':'登记载具编号 | ZJ-001','category':'登记记录','page':1,
           'locator':{'table':'2','row':3}}
        facts=schemes.extracted_facts(schemes.BASE['carrier'],[p])
        self.assertEqual(facts[0]['locator'],{'table':'2','row':3,'field':'登记载具编号'})
        self.assertFalse(facts[0]['confirmed'])

    def test_nonfinancial_engine_runs_field_and_model_checks_in_one_case(self):
        import io, zipfile
        from app import materials
        base=Path(store.DB).parent
        body=io.BytesIO()
        with zipfile.ZipFile(body,'w') as out:
            out.writestr('待审合同.html','<p>甲方名称：虚构甲方</p><p>乙方名称：虚构乙方</p><p>合同付款起算条件：交付后30日</p>')
            out.writestr('标准模板.html','<p>模板付款起算条件：验收后30日</p>')
        source=base/'contract.zip'
        source.write_bytes(body.getvalue())
        scheme=schemes.published('contract')
        for id,owner in [('contract-policy','contract'),('foreign-policy','finance-t1')]:
            store.put('knowledge',dict(id=id,version='v1',scheme_id=owner,title='合同',status='published',text='合同付款起算条件、例外批准材料'))
        store.put('run',{'id':'1234','project_id':'p','scheme_snapshot':scheme,'source':str(source)})
        seen=[]
        def check(prompt,schema,images,**kwargs):
            seen.append(prompt)
            return {'results':[{'id':'CON-03','facts':'未见例外审批','basis':'需核实适用例外',
                                'evidence_ids':[],'missing':'审批文件','verdict':'待人工判断','suggestion':'补证'}]},{}
        with patch.object(store,'DATA',base),patch.object(materials,'DATA',base),patch.dict(os.environ,{'AUDIT_DECISION_PROVIDER':'off'}),patch.object(model,'infer',side_effect=check):
            engine.process('1234')
        done=store.get('run','1234')
        self.assertEqual(done['status'],'completed',done.get('error'))
        self.assertEqual([r['verdict'] for r in done['results']],['不通过','通过','待人工判断'])
        self.assertEqual(engine.summary(done)['expected'],3)
        self.assertEqual([x['id'] for x in done['knowledge_snapshot']],['contract-policy'])
        self.assertTrue(done['facts'][0]['evidence_id'])
        self.assertIn('合同审核（示例）',seen[0])
        self.assertNotIn('经销商',seen[0])

    def test_finance_override_cannot_silently_override_changed_principle(self):
        rule=next(r for r in engine.RULES if r['id']=='T1-02-13')
        run={'evidence':[{'category':'报销主单','method':'PDF文本','id':'D001-P1','text':'费用明细表 0.00'}],'facts':[]}
        self.assertIn(rule['id'],engine.finance_checks(run,[rule]))
        self.assertNotIn(rule['id'],engine.finance_checks(run,[dict(rule,principle='核对经批准例外后再决定')]))
