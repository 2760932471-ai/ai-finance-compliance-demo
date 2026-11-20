import copy
import csv
import http.client
import io
import json
import tempfile
import threading
import unittest
from pathlib import Path

from compliance.domain import build_graph, evaluate, graph_path, impact, validate_config, validate_events
from compliance.reports import csv_report, html_report, safe_cell
from compliance.seed import demo_events, initial_config
from compliance.store import Conflict, Store
from server import create_server, TOKEN


class DomainTests(unittest.TestCase):
    def setUp(self):
        self.config = initial_config()
        self.events = demo_events()

    def test_seed_valid(self):
        validate_config(self.config)
        self.assertEqual(len(validate_events(self.events, self.config['ontology'])), 34)

    def test_three_dimensions_and_score(self):
        r = evaluate(self.events, self.config)[0]
        self.assertEqual(r['event']['id'], 'DEMO-031')
        self.assertEqual(set(r['dimensions']), {'flow', 'stat', 'graph'})
        self.assertEqual(r['score'], 100)
        self.assertEqual(r['level'], '高')

    def test_single_dimension_never_risk_even_high_score(self):
        for rule in self.config['models'][0]['rules']:
            rule['weight'] = 100
        r = next(r for r in evaluate(self.events, self.config) if r['event']['id'] == 'DEMO-032')
        self.assertEqual((r['score'], r['status'], r['level']), (100, 'clue', '待核验'))

    def test_two_flow_hits_are_still_one_dimension(self):
        self.config['models'][0]['rules'] = self.config['models'][0]['rules'][:2]
        r = evaluate(self.events, self.config)[0]
        self.assertEqual(r['dimensions'], ['flow'])
        self.assertEqual(r['status'], 'clue')

    def test_threshold_change_affects_real_result(self):
        before = evaluate(self.events, self.config)
        self.config['models'][0]['rules'][0]['threshold'] = 10
        self.config['models'][0]['rules'][3]['threshold'] = 100
        after = evaluate(self.events, self.config)
        self.assertGreater(sum(r['status']=='cross' for r in before), sum(r['status']=='cross' for r in after))

    def test_insufficient_samples_do_not_report_clear(self):
        r = evaluate(self.events[:3], self.config)[0]
        self.assertEqual(r['status'], 'incomplete')
        self.assertFalse(next(c for c in r['checks'] if c['kind']=='amount_outlier')['evaluated'])

    def test_baseline_excludes_current_event(self):
        r = evaluate(self.events, self.config)[0]
        c = next(c for c in r['checks'] if c['kind']=='amount_outlier')
        self.assertEqual(c['evidence']['peer_count'],33)

    def test_disabling_models_and_rules(self):
        self.config['models'][0]['enabled'] = False
        self.assertEqual(evaluate(self.events,self.config),[])
        self.config['models'][0]['enabled'] = True
        for rule in self.config['models'][0]['rules']:
            rule['enabled'] = False
        self.assertTrue(all(r['status']=='incomplete' for r in evaluate(self.events,self.config)))

    def test_graph_path_follows_real_edges(self):
        graph = build_graph(self.events)
        path = graph_path(graph,'supplier:示例远山设备','supplier:示例青禾材料')
        self.assertEqual(path,['supplier:示例远山设备','bank:DEMO-SHARED-01','supplier:示例青禾材料'])
        self.assertEqual(graph_path(graph,path[0],path[0]),[path[0]])

    def test_unknown_graph_node_rejected(self):
        with self.assertRaises(ValueError): graph_path(build_graph(self.events),'not-real','also-not')

    def test_duplicate_entity_and_dangling_relation_rejected(self):
        bad=copy.deepcopy(self.config)
        bad['ontology']['entities'].append(bad['ontology']['entities'][0])
        with self.assertRaises(ValueError): validate_config(bad)
        bad=copy.deepcopy(self.config)
        bad['ontology']['relations'][0]['target']='not-real'
        with self.assertRaises(ValueError): validate_config(bad)

    def test_arbitrary_expression_rejected(self):
        self.config['models'][0]['rules'][0]['kind']='eval'
        with self.assertRaises(ValueError): validate_config(self.config)

    def test_weight_nan_and_infinite_rejected(self):
        for value in (float('nan'),float('inf'),-1,True):
            self.config['models'][0]['rules'][0]['weight']=value
            with self.assertRaises(ValueError): validate_config(self.config)

    def test_mapping_applies_and_conflicts_fail(self):
        row=copy.deepcopy(self.events[0]);row['payment_total']=row.pop('amount')
        self.assertEqual(validate_events([row],self.config['ontology'])[0]['amount'],self.events[0]['amount'])
        row['amount']=99999
        with self.assertRaises(ValueError):validate_events([row],self.config['ontology'])

    def test_events_date_type_and_uniqueness(self):
        for field,value in [('payment_date','2026-02-30'),('amount',-3),('accepted','yes'),('business_type','未知')]:
            row=copy.deepcopy(self.events[0]);row[field]=value
            with self.assertRaises(ValueError):validate_events([row],self.config['ontology'])
        with self.assertRaises(ValueError):validate_events([self.events[0]]*2,self.config['ontology'])

    def test_compatibility_detects_type_and_required_changes(self):
        changed=copy.deepcopy(self.config)
        changed['ontology']['entities'][0]['properties'][0]['type']='string'
        self.assertFalse(impact(self.config,changed)['compatible'])
        changed=copy.deepcopy(self.config)
        changed['ontology']['entities'][0]['properties'].append({'code':'new','name':'新字段','type':'string','required':True})
        self.assertFalse(impact(self.config,changed)['compatible'])
        changed['ontology']['entities'][0]['properties'][-1]['required']=False
        self.assertTrue(impact(self.config,changed)['compatible'])

    def test_mapping_type_must_match(self):
        self.config['ontology']['mappings'][0]['property']='date'
        with self.assertRaises(ValueError):validate_config(self.config)


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.store=Store(Path(self.temp.name)/'demo.db')

    def tearDown(self): self.temp.cleanup()

    def test_restart_persists_run_and_review(self):
        run=self.store.run()
        self.store.review(run['id'],run['results'][0]['id'],'confirmed','演示审核员','已核对虚构合同与关联账户')
        again=Store(self.store.path).get_run(run['id'])
        self.assertEqual(len(again['reviews']),1)
        self.assertEqual(again['results'],run['results'])

    def test_snapshots_immutable_after_new_configuration(self):
        run=self.store.run();s=self.store.state();c=s['draft']['config'];c['models'][0]['rules'][0]['threshold']=10
        self.store.save_config(c,s['draft']['revision'])
        self.store.publish(self.store.state()['draft']['revision'],'调整超付阈值')
        new_run=self.store.run()
        self.assertEqual(new_run['version'],2)
        self.assertEqual(self.store.get_run(run['id'])['config']['models'][0]['rules'][0]['threshold'],.05)

    def test_revision_conflict(self):
        state=self.store.state();self.store.save_config(state['draft']['config'],1)
        with self.assertRaises(Conflict):self.store.save_config(state['draft']['config'],1)
        with self.assertRaises(Conflict):self.store.publish(1,'过期修订')

    def test_breaking_publication_blocked_atomically(self):
        s=self.store.state();c=s['draft']['config'];c['ontology']['relations'].pop()
        self.store.save_config(c,1)
        with self.assertRaises(ValueError):self.store.publish(2,'破坏性删除')
        self.assertEqual(self.store.state()['active_version'],1)

    def test_import_does_not_mutate_run(self):
        run=self.store.run();rows=demo_events();rows[0]['amount']=11111
        self.store.import_events(rows,'测试导入',1)
        self.assertEqual(self.store.get_run(run['id'])['dataset']['rows'][0]['amount'],9250)
        self.assertEqual(self.store.state()['dataset']['rows'][0]['amount'],11111)

    def test_review_state_constraints(self):
        r=self.store.run();clue=next(x for x in r['results'] if x['status']=='clue');cross=r['results'][0]
        with self.assertRaises(ValueError):self.store.review(r['id'],clue['id'],'confirmed','审核员','只有单维')
        with self.assertRaises(ValueError):self.store.review(r['id'],cross['id'],'resolved','审核员','未确认')
        with self.assertRaises(ValueError):self.store.review(r['id'],cross['id'],'confirmed','审核员','')
        self.store.review(r['id'],cross['id'],'confirmed','审核员','已核对')
        final=self.store.review(r['id'],cross['id'],'resolved','审核员','整改凭证已核对')
        self.assertEqual([x['status'] for x in final['reviews']],['confirmed','resolved'])

    def test_csv_formula_and_html_injection(self):
        r=self.store.run();r['results'][0]['event']['name']='=HYPERLINK("evil")<script>alert(1)</script>'
        csv_text=csv_report(r).decode('utf-8-sig');rows=list(csv.reader(io.StringIO(csv_text)))
        self.assertTrue(rows[1][1].startswith("'="))
        self.assertNotIn('<script>',html_report(r).decode())
        self.assertTrue(safe_cell('  +SUM(1)').startswith("'"))


class HttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();cls.httpd=create_server(0,Path(cls.temp.name)/'http.db')
        cls.port=cls.httpd.server_address[1];cls.thread=threading.Thread(target=cls.httpd.serve_forever,daemon=True);cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown();cls.httpd.server_close();cls.thread.join();cls.temp.cleanup()

    def request(self,path,body=None,headers=None):
        conn=http.client.HTTPConnection('127.0.0.1',self.port)
        defaults={'Content-Type':'application/json','X-Session-Token':TOKEN}
        if headers: defaults.update(headers)
        conn.request('POST' if body is not None else 'GET',path,body,defaults)
        r=conn.getresponse();result=(r.status,dict(r.getheaders()),r.read());conn.close();return result

    def test_health_ui_and_tools(self):
        self.assertEqual(self.request('/health')[0],200)
        self.assertIn('AI财务审核',self.request('/')[2].decode())
        response=self.request('/api/compliance/tools/call',json.dumps({'name':'metrics'}))
        self.assertEqual(response[0],200);self.assertEqual(json.loads(response[2])['count'],34)

    def test_host_and_origin_rejected(self):
        self.assertEqual(self.request('/api/compliance/state',headers={'Host':'attacker.example'})[0],403)
        self.assertEqual(self.request('/api/compliance/run','{}',{'Origin':'https://evil.example'})[0],403)

    def test_form_csrf_missing_header_and_invalid_json(self):
        self.assertEqual(self.request('/api/compliance/run','{}',{'X-Session-Token':''})[0],403)
        self.assertEqual(self.request('/api/compliance/run','{}',{'Content-Type':'text/plain'})[0],400)
        self.assertEqual(self.request('/api/compliance/run','{invalid}')[0],400)
        self.assertEqual(self.request('/api/compliance/run','{"x":NaN}')[0],400)

    def test_path_traversal_and_unknown_endpoint(self):
        self.assertEqual(self.request('/../server.py')[0],404)
        self.assertEqual(self.request('/%2e%2e/server.py')[0],404)
        self.assertEqual(self.request('/api/compliance/not-real','{}')[0],404)

    def test_missing_run_and_invalid_payload(self):
        self.assertEqual(self.request('/api/compliance/run?id=missing')[0],404)
        self.assertEqual(self.request('/api/compliance/config','{}')[0],400)
        self.assertEqual(self.request('/api/compliance/config','[]')[0],400)

    def test_security_headers(self):
        headers=self.request('/')[1]
        self.assertIn("frame-ancestors 'none'",headers['Content-Security-Policy'])
        self.assertEqual(headers['X-Content-Type-Options'],'nosniff')

    def test_http_end_to_end_export(self):
        status,_,body=self.request('/api/compliance/run','{}');self.assertEqual(status,200)
        run=json.loads(body)
        self.assertEqual(self.request('/api/compliance/export/csv?run='+run['id'])[0],200)
        self.assertEqual(self.request('/api/compliance/export/html?run='+run['id'])[0],200)
        self.assertEqual(self.request('/api/compliance/export/json?run='+run['id'])[0],200)


if __name__=='__main__':unittest.main()
