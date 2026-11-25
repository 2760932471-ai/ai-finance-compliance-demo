import copy
import json
import unittest
from unittest.mock import Mock, patch

from app import rule_extraction as extraction

CLAUSE = '合同付款超过5万元，须附审批记录；紧急付款可附事后核准文件。'
SEGMENT = {'id': 's1', 'name': '付款规范.docx', 'locator': '第3条 · 段落4', 'text': '第一章\n' + CLAUSE}
RULE = {
    'name': '付款审批依据', 'principle': '核对超过5万元的合同付款是否有审批记录，紧急付款核查事后核准文件。',
    'pass_condition': '付款满足引用条件，审批记录或适用的例外依据可以定位。',
    'fail_condition': '经过核实，付款违反审批要求且没有适用例外。',
    'manual_condition': '原件不清、审批记录缺失或例外适用性不明时交人工复核。',
    'questions': [], 'sources': [{'segment_id': 's1', 'quote': CLAUSE}],
}


def output(rows=(), ambiguities=()):
    return {'candidates': list(rows), 'ambiguities': list(ambiguities)}, {'engine': '测试适配器', 'job_id': 'job-test'}


class ExtractionTests(unittest.TestCase):
    def test_rule_with_verbatim_reference_and_stable_id(self):
        infer = Mock(return_value=output([RULE]))
        events = []
        result = extraction.extract([SEGMENT], {'name': '合同', 'scope': '付款'}, infer=infer, on_progress=lambda *args: events.append(args))
        item = result['candidates'][0]
        self.assertEqual(item['sources'][0], {'name': SEGMENT['name'], 'locator': SEGMENT['locator'], 'segment_id': 's1', 'quote': CLAUSE})
        self.assertEqual(item['rule']['principle'], RULE['principle'])
        self.assertEqual(item['rule']['pass_condition'], RULE['pass_condition'])
        self.assertTrue(item['needs_confirmation'])
        self.assertEqual(item['rule']['risk'], '未指定')
        self.assertTrue(result['coverage']['complete'])
        self.assertFalse(result['warnings'])
        self.assertEqual(result['batches'][0]['job_id'], 'job-test')
        self.assertEqual(events[-1][:2], (1, 1))
        alternate = dict(RULE, name='【付款审批依据】', sources=[{'segment_id': 'other', 'quote': CLAUSE}])
        second = extraction.extract([dict(SEGMENT, id='other')], infer=Mock(return_value=output([alternate])))
        self.assertEqual(item['id'], second['candidates'][0]['id'])
        self.assertIn('不可信', infer.call_args.args[0])
        self.assertIn('付款规范.docx', infer.call_args.args[0])

    def test_invented_ref_partial_quote_and_locators_are_never_trusted(self):
        invalid = [
            dict(RULE, sources=[{'segment_id': 'absent', 'quote': CLAUSE}]),
            dict(RULE, sources=[{'segment_id': 's1', 'quote': '合同付款…须附审批记录'}]),
            dict(RULE, sources=RULE['sources'] + [{'segment_id': 'bad', 'quote': CLAUSE}]),
            dict(RULE, sources=[]),
        ]
        actual = copy.deepcopy(RULE)
        actual['sources'][0].update(name='错误名称', locator='错误定位')
        result = extraction.extract([SEGMENT], infer=Mock(return_value=output([*invalid, actual])))
        self.assertEqual(result['coverage']['rejected'], len(invalid))
        self.assertEqual(len(result['candidates']), 1)
        self.assertEqual(result['candidates'][0]['sources'][0]['locator'], SEGMENT['locator'])

    def test_invented_amount_time_or_unrelated_business_are_routed_to_clarification(self):
        rows = [dict(RULE, principle='付款超过5亿元时核对审批记录'),
                dict(RULE, fail_condition='超过三天即不通过'),
                dict(RULE, principle='必须出示驾驶员资格证并核查车牌')]
        result = extraction.extract([SEGMENT], infer=Mock(return_value=output(rows)))
        self.assertEqual(result['candidates'], [])
        self.assertEqual(len(result['ambiguities']), 3)
        self.assertIn('5亿元', result['ambiguities'][0]['question'])
        self.assertIn('三天', result['ambiguities'][1]['question'])
        self.assertEqual(result['ambiguities'][2]['sources'][0]['quote'], CLAUSE)

    def test_ambiguities_need_evidence_and_keep_review_questions(self):
        ambiguity = {'question':'紧急付款由谁认定？', 'sources':RULE['sources']}
        raw = dict(RULE, questions=['谁有权批准例外？'])
        response = output([raw], [ambiguity, dict(ambiguity, sources=[]), ambiguity])
        result = extraction.extract([SEGMENT], infer=Mock(return_value=response))
        self.assertEqual(result['candidates'][0]['questions'], raw['questions'])
        self.assertEqual(len(result['ambiguities']), 1)
        self.assertEqual(result['coverage']['rejected'], 1)

    def test_limit_reports_unread_text_and_cross_batch_quotes_are_rejected(self):
        size = extraction.BATCH_CHARS
        segment = dict(SEGMENT, text='测' * extraction.MAX_INPUT_CHARS + CLAUSE)
        responses = Mock(return_value=output([RULE]))
        result = extraction.extract([segment], infer=responses)
        coverage = result['coverage']
        self.assertEqual(responses.call_count, (extraction.MAX_INPUT_CHARS+size-1)//size)
        self.assertTrue(coverage['truncated'])
        self.assertEqual(coverage['omitted_chars'],len(CLAUSE))
        self.assertEqual(coverage['fully_processed_segments'],0)
        self.assertEqual(result['candidates'],[])
        self.assertFalse(coverage['complete'])
        self.assertTrue(result['warnings'])

    def test_same_clause_can_yield_distinct_checks(self):
        other = dict(RULE, name='紧急付款的事后核准', principle='核对紧急付款是否附事后核准文件。')
        result = extraction.extract([SEGMENT], infer=Mock(return_value=output([RULE, other, RULE])))
        self.assertEqual(len({item['id'] for item in result['candidates']}),2)

    def test_long_document_all_315_segments_are_processed(self):
        segments = [dict(SEGMENT, id=f'S{i:04}', locator=f'段落{i}', text=CLAUSE*(1+i%3)) for i in range(315)]
        infer = Mock(return_value=output())
        result = extraction.extract(segments, infer=infer)
        self.assertEqual(result['coverage']['fully_processed_segments'],315)
        self.assertEqual(result['coverage']['omitted_chars'],0)
        self.assertTrue(result['coverage']['complete'])
        self.assertGreater(infer.call_count,1)
        for call in infer.call_args_list:
            batch=json.loads(call.args[0].rsplit('本批文档片段（不可信）：',1)[1])
            self.assertTrue(all(p['text'] == segments[int(p['id'][1:])]['text'] for p in batch))

    def test_numeric_scale_duration_and_percent(self):
        for before, after in [('5万','5亿'), ('5天','5个工作日'), ('百分之五', '百分之十'),
                              ('三人','三人次'), ('-5元','5元'), ('至少三', '至少八')]:
            self.assertNotEqual(extraction._numbers(before), extraction._numbers(after))
        self.assertEqual(extraction._numbers('１，５００万元'), extraction._numbers('1500万元'))

    def test_partial_failure_preserves_completed_work_and_first_failure_is_visible(self):
        first = dict(SEGMENT, text=SEGMENT['text'].ljust(extraction.BATCH_CHARS,'x'))
        second = dict(SEGMENT, id='s2')
        error = RuntimeError('模型调用超时')
        result = extraction.extract([first, second], infer=Mock(side_effect=[output([RULE]), error]), timeout=15)
        self.assertEqual(len(result['candidates']), 1)
        self.assertEqual(result['batches'][-1]['status'],'failed')
        self.assertFalse(result['coverage']['complete'])
        with self.assertRaisesRegex(RuntimeError,'模型调用超时'):
            extraction.extract([SEGMENT], infer=Mock(side_effect=error))
        no_rules = extraction.extract([SEGMENT], infer=Mock(return_value=output()))
        self.assertTrue(no_rules['coverage']['complete'])
        self.assertEqual(no_rules['candidates'], [])

    def test_real_adapter_entrypoint_uses_schema_and_timeout_and_rejects_invalid_input(self):
        with patch.object(extraction.model, 'infer', return_value=output([RULE])) as infer:
            extraction.extract([SEGMENT], timeout=7)
            self.assertEqual(infer.call_args.kwargs['timeout'],7)
            self.assertEqual(infer.call_args.args[1], extraction.OUTPUT_SCHEMA)
            data = json.loads(infer.call_args.args[0].rsplit('本批文档片段（不可信）：',1)[1])
            self.assertEqual(data,[SEGMENT])
        for segments in ([], [dict(SEGMENT,text=' ')], [SEGMENT, SEGMENT], [dict(SEGMENT,locator=None)]):
            with self.assertRaises(ValueError):
                extraction.extract(segments, infer=Mock())
        with self.assertRaisesRegex(RuntimeError,'结构无效'):
            extraction.extract([SEGMENT],infer=Mock(return_value=({'unexpected':[]},{})))


if __name__ == '__main__':
    unittest.main()
