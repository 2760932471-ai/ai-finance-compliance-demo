"""Cross-scheme snapshots, navigable evidence, and immutable exports."""
import copy
import io
import json
import unittest
from html.parser import HTMLParser

from pypdf import PdfReader

import test_audit
from app import report, report_snapshot, schemes, store


class Anchors(HTMLParser):
    def __init__(self):
        super().__init__()
        self.targets = []
        self.links = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if 'id' in attrs:
            self.targets.append(attrs['id'])
        if attrs.get('href', '').startswith('#'):
            self.links.append(attrs['href'][1:])


class SchemeReportTests(unittest.TestCase):
    setUp = test_audit.AuditTests.setUp
    tearDown = test_audit.AuditTests.tearDown

    def example(self, scheme_id='contract'):
        scheme = copy.deepcopy(schemes.BASE[scheme_id])
        scheme['version'] = 2
        scheme['rules'][0]['id'] = 'results'
        rule, excluded = scheme['rules'][:2]
        result = {**rule, 'evidence_ids':['results'], 'verdict':'不通过',
                  'facts':'实际值有差异', 'basis':'字段比对', 'execution':'completed'}
        evidence = {'id':'results', 'name':'附件.docx','document_id':'D001','page':8,
                    'category':'业务材料','text':'数量：0','locator':{'type':'row','table':'清单','row':3},
                    'method':'Word 正文','sha256':'abc'}
        store.put('project', {'id':'p','name':'演示事项'})
        return {'id':'r', 'project_id':'p', 'scheme_id':scheme_id, 'scheme_snapshot':scheme, 'rules':[rule], 'business_id':'BIZ-015',
                'excluded_rules':[{'id':excluded['id'], 'name':excluded['name'], 'reason':'限其他材料'}],
                'results':[result], 'rule_version':scheme_id+'/2', 'evidence':[evidence],
                'facts':[{'label':'数量', 'value':0, 'evidence_id':'results'}],
                'knowledge_snapshot':[{'id':'k', 'title':'适用指引', 'source':str(self.root/'指引.docx'),
                                       'version':'3', 'chunk_id':'k001', 'line':2, 'text':'已发布指引'}]}

    def test_actual_scope_anchors_sources_and_frozen_exports(self):
        for scheme_id in ('contract', 'carrier'):
            with self.subTest(scheme_id=scheme_id):
                run = self.example(scheme_id)
                record = report.generate(run, 'json')
                raw_json, _, fmt = report.download(record)
                self.assertEqual(fmt, 'json')
                snapshot = json.loads(raw_json)
                self.assertEqual(snapshot['schema_version'], 'audit-report-v2')
                self.assertEqual(snapshot['scheme']['version'], 2)
                self.assertEqual(snapshot['applicable_rule_ids'], ['results'])
                self.assertEqual(snapshot['summary']['expected'], 1)
                self.assertTrue(snapshot['summary']['complete'])
                self.assertEqual(snapshot['facts'][0]['value'], 0)
                self.assertEqual(snapshot['knowledge_sources'][0]['source'], '指引.docx')
                self.assertNotIn(str(self.root), raw_json.decode())
                page = report.download(record, 'html')[0].decode()
                doc = PdfReader(io.BytesIO(report.download(record,'pdf')[0]))
                text = '\n'.join(p.extract_text() for p in doc.pages)
                title = run['scheme_snapshot']['report']['title']
                self.assertIn(title, doc.metadata.title)
                for rendered in (text, page):
                    for term in [title, 'BIZ-015', '指引.docx', 'k001','清单', '限其他材料', '数量：0']:
                        self.assertIn(term, rendered)
                    for term in ['经销商', '财务费用审核报告', '第8页']:
                        self.assertNotIn(term, rendered)
                self.assertIn('逐条审核结果（1 / 1）',page)
                anchors = Anchors()
                anchors.feed(page)
                self.assertEqual(len(anchors.targets),len(set(anchors.targets)))
                self.assertTrue(set(anchors.links).issubset(anchors.targets))
                for kind in ['rule', 'evidence']:
                    self.assertIn(report_snapshot.anchor(kind, 'results'), anchors.links)
                run['scheme_snapshot']['report']['title'] = '后续的新标题'
                run['evidence'][0]['text'] = '后续的新正文'
                self.assertEqual(report.download(record)[0], raw_json)

    def test_supervision_task_survives_all_formats_and_only_includes_supplied_source(self):
        run = self.example()
        task = {'id':'HCD-001', 'row_id':'PAY-002', 'org_id':'ORG-003', 'question':'核对付款依据 Q-004'}
        run['supervision_task'] = {**task, 'token':'private-routing-token'}
        record = report.generate(run)
        snapshot = json.loads(report.download(record,'json')[0])
        self.assertEqual(snapshot['run']['supervision_task'], task)
        pdf = PdfReader(io.BytesIO(report.download(record,'pdf')[0]))
        for rendered in (report.download(record,'html')[0].decode(), '\n'.join(p.extract_text() for p in pdf.pages)):
            self.assertIn('监管任务来源',rendered)
            for value in task.values():
                self.assertIn(value,rendered)
            self.assertNotIn('private-routing-token',rendered)
        del run['supervision_task']
        self.assertNotIn('supervision_task', report_snapshot.capture(run)[0]['run'])

    def test_invalid_scope_and_references_stop_publication(self):
        changes = [
            lambda run: run['results'][0].update(id='unknown'),
            lambda run: run['facts'][0].update(evidence_id='missing'),
            lambda run: run['results'][0].update(evidence_ids=['missing']),
            lambda run: run['excluded_rules'][0].update(id='results'),
            lambda run: run['evidence'][0].update(id='"><script>'),
            lambda run: run.pop('scheme_snapshot'),
        ]
        for change in changes:
            run = self.example()
            change(run)
            with self.assertRaises(ValueError):
                report.generate(run)
        self.assertFalse(store.all_items('report'))


if __name__ == '__main__':
    unittest.main()
