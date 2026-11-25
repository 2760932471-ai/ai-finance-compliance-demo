"""Rule extraction crosses into the versioned scheme only through explicit adoption."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from app import schemes, store


class AdoptionTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.enterContext(patch.object(store, 'DB', Path(temp.name) / 'audit.sqlite3'))
        store.init()
        self.clause = '单笔超过五万元，需附付款审批记录。'
        self.cid = 'EX-8198ce2a4e9f'
        self.source = {'segment_id': 'S001', 'name': '付款制度.txt',
                       'locator': '第 1 行', 'quote': self.clause}
        self.rule = {'id': self.cid, 'group': '制度条款', 'name': '付款审批',
                     'risk': 'P1', 'method': 'model', 'principle': self.clause,
                     'pass_condition': '审批齐全', 'fail_condition': '存在明确反证',
                     'manual_condition': '无法核实则转人工'}
        candidate = {'id': self.cid, 'rule': self.rule, 'sources': [self.source],
                     'questions': ['审批主体如何认定？'], 'needs_confirmation': True}
        self.job = {'id': 'job-1', 'status': 'completed', 'scheme_id': 'contract',
                    'filename': self.source['name'], 'sha256': sha256(self.clause.encode()).hexdigest(),
                    'candidates': [candidate]}
        store.put('rule_extraction', self.job)

    def adopt(self, revision=0, ids=None, **extra):
        body = {'revision': revision, 'candidate_ids': ids if ids is not None else [self.cid], **extra}
        return schemes.adopt_extracted('contract', self.job['id'], body)

    def test_adoption_edit_trial_publication_and_frozen_attribution(self):
        original = schemes.published('contract')
        schemes.trial('contract', {'revision': 0})
        adopted = self.adopt(provenance={'filename': '伪造.txt'}, candidates=[])
        self.assertEqual(adopted['revision'], 1)
        self.assertIsNone(adopted['trial'])
        self.assertEqual(schemes.published('contract'), original)
        self.assertEqual(adopted['draft']['rules'][:-1], original['rules'])
        rule = adopted['draft']['rules'][-1]
        origin = deepcopy(rule['provenance'])
        self.assertEqual(origin, {
            'kind': 'rule_extraction', 'job_id': self.job['id'], 'candidate_id': self.cid,
            'sha256': self.job['sha256'], 'filename': self.job['filename'],
            'sources': [self.source], 'questions': ['审批主体如何认定？'], 'original_rule': self.rule,
        })
        with self.assertRaises(ValueError):
            schemes.publish('contract', {'revision': 1, 'note': '立即发布'})
        self.assertFalse(schemes.trial('contract', {'revision': 1})['trial']['passed'])
        # Later edits to stored extraction records or submitted metadata cannot rewrite the snapshot.
        self.job['candidates'][0]['sources'][0]['quote'] = '被替换的引用'
        store.put('rule_extraction', self.job)
        rule.update(provenance={'filename': '手工伪造.txt'}, method='required_fields',
                    fields=['审批单位'], principle='核对审批单位字段')
        adopted['draft']['rules'][0]['provenance'] = origin
        saved = schemes.save('contract', {'revision': 1, 'draft': adopted['draft']})
        self.assertEqual(saved['draft']['rules'][-1]['provenance'], origin)
        self.assertNotIn('provenance', saved['draft']['rules'][0])
        del saved['draft']['rules'][-1]['provenance']
        saved = schemes.save('contract', {'revision': 2, 'draft': saved['draft']})
        self.assertEqual(saved['draft']['rules'][-1]['provenance'], origin)
        sample = {'name': '虚构审批单', 'pages': [{'category': '待审合同', 'text': '审批单位：示例审核组'}],
                  'expected': {self.cid: '通过'}}
        self.assertTrue(schemes.trial('contract', {'revision': 3, 'samples': [sample]})['trial']['passed'])
        result = schemes.publish('contract', {'revision': 3, 'note': '补充已验证的审批字段'})
        self.assertEqual(result['published']['rules'][-1]['provenance'], origin)
        self.assertEqual(result['published']['rules'][-1]['principle'], '核对审批单位字段')
        self.assertEqual(schemes.published('contract', 1), original)

    def test_invalid_selection_is_atomic_and_does_not_consume_revision(self):
        original = schemes.detail('contract')
        for ids in ([], [self.cid, 'unknown'], [self.cid, self.cid]):
            with self.subTest(ids=ids), self.assertRaises(ValueError):
                self.adopt(ids=ids)
        for change in ({'status': 'running'}, {'status': 'failed'}, {'scheme_id': 'carrier'},
                       {'sha256': ''}, {'candidates': [dict(self.job['candidates'][0], sources=[])]}):
            with self.subTest(change=change):
                store.put('rule_extraction', {**self.job, **change})
                with self.assertRaises(ValueError):
                    self.adopt()
                self.assertEqual(schemes.detail('contract'), original)
        store.put('rule_extraction', self.job)
        adopted = self.adopt()
        with self.assertRaisesRegex(ValueError, '刷新'):
            self.adopt(revision=0)
        with self.assertRaisesRegex(ValueError, '已存在'):
            self.adopt(revision=1)
        self.assertEqual(schemes.detail('contract'), adopted)

    def test_two_concurrent_adoptions_cannot_overwrite_draft(self):
        barrier = threading.Barrier(2)

        def adopt_once():
            barrier.wait(timeout=5)
            try:
                return self.adopt()['revision']
            except ValueError as exc:
                return str(exc)

        with ThreadPoolExecutor(max_workers=2) as pool:
            calls = [pool.submit(adopt_once) for _ in range(2)]
        results = [call.result() for call in calls]
        self.assertCountEqual(results, [1, '方案已更新，请刷新后重试'])
        self.assertEqual([r['id'] for r in schemes.detail('contract')['draft']['rules']].count(self.cid), 1)

    def test_copy_of_draft_cannot_claim_cross_scheme_attribution(self):
        draft = self.adopt()['draft']
        copied = schemes.save('other', {'revision': 0, 'draft': dict(draft, id='other')})
        self.assertNotIn('provenance', copied['draft']['rules'][-1])

    def test_renaming_the_same_document_does_not_duplicate_its_clause(self):
        adopted = self.adopt()
        other = deepcopy(self.job)
        other.update(id='another-job', filename='付款制度副本.txt')
        item = other['candidates'][0]
        item['sources'][0]['name'] = other['filename']
        item['id'] = item['rule']['id'] = 'EX-another'
        store.put('rule_extraction', other)
        with self.assertRaisesRegex(ValueError, '相同条款'):
            schemes.adopt_extracted('contract', other['id'], {'revision': 1, 'candidate_ids': [item['id']]})
        self.assertEqual(schemes.detail('contract'), adopted)
