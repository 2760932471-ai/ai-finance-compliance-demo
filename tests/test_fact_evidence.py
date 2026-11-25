"""Trace a single business field or graph edge to the supplying run and location."""
import copy
import unittest

import test_integration
from app import store
from compliance.domain import FACT_FIELDS, RELATIONS, build_graph, evaluate
from compliance.seed import initial_config


class FactEvidenceTests(unittest.TestCase):
    setUp = test_integration.IntegrationTests.setUp
    tearDown = test_integration.IntegrationTests.tearDown
    call = test_integration.IntegrationTests.call
    zip = test_integration.IntegrationTests.zip
    seed_run = test_integration.IntegrationTests.seed_run
    capture_payload = test_integration.IntegrationTests.capture_payload

    def payload(self):
        payload, run = self.capture_payload()
        p1 = run['evidence'][0]
        p1.update(name='合同.docx',document_id='D001',locator={'type':'clause','clause':'第七条'})
        p2 = {**p1, 'id':'D002-P1', 'name':'登记表.docx', 'document_id':'D002',
              'locator':{'type':'row','table':'2','row':7}}
        run['evidence'].append(p2)
        store.put('run',run)
        payload.update(proof_version=2,evidence_ids=[p1['id'],p2['id']],
                       field_evidence={k:[p1['id']] for k in FACT_FIELDS},
                       relation_evidence={k:[p2['id']] for k in RELATIONS})
        return payload, run

    def test_field_relation_snapshot_and_unknown_acceptance(self):
        payload, run = self.payload()
        payload['row']['accepted'] = None
        del payload['field_evidence']['accepted']
        code, state = self.call('/api/compliance/capture',payload)
        self.assertEqual(code,200,state)
        row=state['dataset']['rows'][0]
        p=row['provenance']
        self.assertEqual(p['field_evidence']['amount'],['D001-P1'])
        self.assertEqual(p['evidence_index']['D002-P1']['locator'],{'type':'row','table':'2','row':7})
        self.assertEqual(len(p['facts_sha256']),64)
        graph=build_graph([row])
        for edge in graph['edges']:
            self.assertEqual(edge['evidence'],[{'event_id':row['id'],'run_id':run['id'],'scope':'relation','evidence_ids':['D002-P1']}])
        rule=next(c for c in evaluate([row],initial_config())[0]['checks'] if c['kind']=='missing_acceptance')
        self.assertFalse(rule['evaluated'])
        self.assertFalse(rule['hit'])
        for value, hit in [(False,True),(True,False)]:
            local=copy.deepcopy(row)
            local['accepted']=value
            local['provenance']['field_evidence']['accepted']=['D001-P1']
            rule=next(c for c in evaluate([local],initial_config())[0]['checks'] if c['kind']=='missing_acceptance')
            self.assertEqual(rule['hit'],hit)
            self.assertTrue(rule['evaluated'])
        row['provenance']['field_evidence']['amount'] = ['D002-P1']
        self.assertEqual(self.call('/api/compliance/import',{'rows':[row],'source':'回导','revision':state['dataset']['revision']})[0],400)

    def test_foreign_reference_or_unbound_relationship_rejected(self):
        payload, _ = self.payload()
        for mutation in [
            lambda p: p['field_evidence'].update(amount=['D999-P1']),
            lambda p: p['relation_evidence'].update(event_supplier=['D999-P1']),
            lambda p: p['relation_evidence'].pop('event_person'),
            lambda p: p['field_evidence'].pop('supplier'),
        ]:
            bad=copy.deepcopy(payload)
            mutation(bad)
            self.assertEqual(self.call('/api/compliance/capture',bad)[0],400)
        self.assertEqual(self.call('/api/compliance/state')[1]['dataset']['revision'],1)

    def test_legacy_group_provenance_remains_grouped(self):
        payload, run = self.capture_payload()
        payload['row']['accepted'] = False
        code, state = self.call('/api/compliance/capture',payload)
        self.assertEqual(code,200)
        row=state['dataset']['rows'][0]
        self.assertNotIn('field_evidence',row['provenance'])
        for edge in build_graph([row])['edges']:
            self.assertEqual(edge['evidence'][0]['scope'],'record')
        rule=next(c for c in evaluate([row],initial_config())[0]['checks'] if c['kind']=='missing_acceptance')
        self.assertFalse(rule['evaluated'])
        self.assertFalse(rule['hit'])


if __name__=='__main__':
    unittest.main()
