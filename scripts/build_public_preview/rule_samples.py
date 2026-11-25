"""Hand-authored synthetic extraction results used only by the offline Pages build."""
from hashlib import sha256

SAMPLE_NOTICE = '演示样例：候选和引文由人工编写，公开镜像未调用模型。'
SAMPLES = {
    'finance-t1': {
        'name': '演示样例 · 活动报销口径.txt',
        'lines': [
            '活动报销须附经办人和验收人签字的验收记录。',
            '如为电子验收，可使用相应的电子签署记录。',
        ],
        'rule': '活动报销验收签署',
        'requirement': '核实验收记录是否包含经办人和验收人两方签署。',
        'pass': '验收记录中两方签署均清晰可定位。',
        'fail': '完整验收记录明确缺少任一必要签署。',
        'manual': '验收记录不全、签署模糊或电子签署效力需要确认。',
        'question': '可以接受哪些电子签署记录？',
    },
    'contract': {
        'name': '演示样例 · 合同付款条款.txt',
        'lines': [
            '验收前累计付款不得超过合同约定的验收前付款限额。',
            '超限付款须在支付前取得书面例外批准。',
        ],
        'rule': '验收前超限付款审批',
        'requirement': '按付款日有效条款核对累计金额，超限时定位支付前的书面批准。',
        'pass': '金额在适用限额内，或已有覆盖该笔付款的事前书面批准。',
        'fail': '已确认付款超限且例外批准在付款后生效。',
        'manual': '缺少验收状态、累计流水、有效限额或例外批准依据。',
        'question': '书面例外批准应由哪一级人员签署？',
    },
    'carrier': {
        'name': '演示样例 · 载具交接要求.txt',
        'lines': [
            '现场交接时，应核对现场载具编号与登记清单中的编号。',
            '编号无法辨认时，经办人应提交补充凭证。',
        ],
        'rule': '载具编号交接核对',
        'requirement': '逐一比对现场载具编号与登记编号，编号模糊时核对补充凭证。',
        'pass': '现场及登记编号均可辨认且相同。',
        'fail': '现场和登记编号清晰且确有差异。',
        'manual': '任一编号缺失、读数模糊或现场对应关系不明。',
        'question': '哪些补充凭证可用于确认无法辨认的编号？',
    },
}


def sample_result(scheme_id, filename, segments):
    """Use the parser's real source locators for our two invented clauses."""
    sample = SAMPLES[scheme_id]
    sources = []
    for line in sample['lines']:
        segment = next(segment for segment in segments if line in segment['text'])
        sources.append({'segment_id': segment['id'], 'name': filename,
                        'locator': segment['locator'], 'quote': line})
    identifier = 'EX-' + sha256((scheme_id + filename).encode()).hexdigest()[:12].upper()
    rule = {'id': identifier, 'name': sample['rule'], 'group':'制度条款', 'risk':'未指定',
            'method':'model', 'principle':sample['requirement'], 'pass_condition':sample['pass'],
            'fail_condition':sample['fail'], 'manual_condition':sample['manual']}
    candidate = {'id':identifier, 'rule':rule, 'sources':sources,
                 'needs_confirmation':True, 'questions':[sample['question']]}
    return {'candidates':[candidate], 'ambiguities':[{'question':sample['question'], 'sources':[sources[-1]]}],
            'warnings':[SAMPLE_NOTICE], 'batches':[]}


def save_sample(scheme_id):
    from app import rule_sources, store

    sample = SAMPLES[scheme_id]
    filename = sample['name']
    # Include a BOM so downloaded Chinese TXT files also open in legacy desktop readers.
    raw = ('\n'.join(sample['lines']) + '\n').encode('utf-8-sig')
    digest = sha256(raw).hexdigest()
    folder = store.DATA / 'rule-sources' / digest[:16]
    folder.mkdir(parents=True)
    (folder / 'original').write_bytes(raw)
    source = rule_sources.read_source(filename, raw, folder)
    result = sample_result(scheme_id, filename, source['segments'])
    stamp = store.now()
    job = {'id':digest[:16], 'scheme_id':scheme_id, 'filename':filename, 'sha256':digest,
           'created_at':stamp, 'updated_at':stamp, 'status':'completed', 'stage':'演示样例 · 人工编写',
           'demo':True, 'coverage':None, 'error':'', **result}
    return store.put('rule_extraction', job)
