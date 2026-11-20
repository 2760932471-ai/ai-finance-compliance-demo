import copy
import tempfile
import unittest
from pathlib import Path
from compliance import analytics as a
from compliance.risklab import RiskLab
from compliance.store import Store, Conflict

class AnalyticsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.rows=a.demo_rows()
    def test_no_future_or_target_leakage(self):
        m=a.templates()[0];base=a.evaluate_period(self.rows,m,'2026-07')
        rows=copy.deepcopy(self.rows)
        for r in rows:
            if r['date']>='2026-07':r['unit_price']*=1000
        changed=a.evaluate_period(rows,m,'2026-07')
        first={r['id']:r for r in base['results']}
        for r in changed['results']:
            self.assertEqual(r['baseline'],first[r['id']]['baseline'])
            self.assertEqual(r['details'][0]['median'],first[r['id']]['details'][0]['median'])
    def test_robust_candidates_and_groups(self):
        result=a.evaluate_period(self.rows,a.templates()[0],'2026-10')
        self.assertEqual(result['metrics']['tp'],2)
        for r in result['results']:
            peers={x['id']:x for x in self.rows}
            self.assertTrue(all(peers[i]['unit']==r['row']['unit'] for i in r['baseline']['sample_ids']))
    def test_insufficient_and_unknown_labels(self):
        m=a.templates()[0];m['seasonal']=True
        result=a.evaluate_period(self.rows,m,'2026-10')
        self.assertEqual(result['metrics']['evaluated'],0)
        m['seasonal']=False
        rows=copy.deepcopy(self.rows)
        for r in rows:r['label']=None
        metrics=a.evaluate_period(rows,m,'2026-10')['metrics']
        self.assertIsNone(metrics['recall']);self.assertIsNone(metrics['precision']);self.assertEqual(metrics['labeled'],0)
    def test_actual_forest_deterministic_and_label_independent(self):
        m=a.templates()[2];x=a.evaluate_period(self.rows,m,'2026-10')
        rows=copy.deepcopy(self.rows)
        for r in rows:r['label']=not r['label']
        y=a.evaluate_period(rows,m,'2026-10')
        self.assertEqual([(r['id'],r['score']) for r in x['results']],[(r['id'],r['score']) for r in y['results']])
        self.assertTrue(x['results'][0]['algorithm_version'].startswith('scikit-learn'))
        self.assertGreater(x['metrics']['tp'],0)
    def test_monthly_and_backtest(self):
        r=a.backtest(self.rows,a.templates()[1],'2026-07','2026-10')
        self.assertEqual(len(r['periods']),4);self.assertEqual(r['metrics']['total'],16)
        sample=r['periods'][-1]['results'][0]
        self.assertEqual(sample['baseline']['count'],12);self.assertIsNotNone(sample['comparisons']['yoy']['change'])
        with self.assertRaises(ValueError):a.backtest(self.rows,a.templates()[0],'2024-01','2026-10')
    def test_validate_bad_numbers_and_dates(self):
        for key,value in [('quantity',0),('amount',float('nan')),('date','2026-02-31')]:
            rows=copy.deepcopy(self.rows[:1]);rows[0][key]=value
            with self.assertRaises(ValueError):a.validate_rows(rows)
        models=a.templates();models[2]['metrics']=['amount']
        with self.assertRaises(ValueError):a.validate_models(models)
    def test_quantile_interval(self):
        m=a.templates()[0];m['interval']='quantile'
        self.assertGreaterEqual(a.evaluate_period(self.rows,m,'2026-10')['metrics']['tp'],2)

class RiskLabTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.lab=RiskLab(Store(Path(self.tmp.name)/'test.db'))
    def tearDown(self):self.tmp.cleanup()
    def test_publish_snapshot_and_labels(self):
        s=self.lab.state();models=copy.deepcopy(s['draft']);models[0]['sensitivity']=5
        draft=self.lab.mutate('save',{'revision':s['revision'],'models':models})
        old=self.lab.run({'model_id':'price','start':'2026-10','end':'2026-10'})
        self.assertEqual(old['model']['sensitivity'],3.5)
        published=self.lab.mutate('publish',{'revision':draft['revision'],'note':'测试版本'})
        self.assertEqual(published['version'],2)
        with self.assertRaises(Conflict):self.lab.mutate('save',{'revision':1,'models':models})
        result=old['result']['periods'][0]['results'][0]
        reviewed=self.lab.label({'run_id':old['id'],'result_id':result['id'],'label':False,'actor':'测试','note':'核实正常'})
        self.assertEqual(reviewed['result'],old['result']);self.assertEqual(len(reviewed['label_history']),1)
        self.assertGreater(reviewed['reviewed_metrics']['fp'],old['result']['metrics']['fp'])
        self.lab.mutate('import',{'revision':published['revision'],'source':'导入测试','rows':a.demo_rows()[:20]})
        self.assertEqual(len(self.lab.get_run(old['id'])['dataset']['rows']),1440)
    def test_invalid_run_and_label(self):
        with self.assertRaises(ValueError):self.lab.run({'model_id':'unknown','start':'2026-10','end':'2026-10'})
        with self.assertRaises(ValueError):self.lab.label({'label':'false'})
