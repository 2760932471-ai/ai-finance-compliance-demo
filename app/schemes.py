"""Versioned audit schemes, scoped knowledge and evidence-based checks."""
import copy
import json
import re
from pathlib import Path

from . import store

FINANCE = 'finance-t1'
ID = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$')
VERDICTS = ('通过', '不通过', '待人工判断')
FINANCE_RULES = json.loads((Path(__file__).parent/'rules.json').read_text(encoding='utf-8'))


def _rule(id, name, group, principle, method='model', fields=()):
    return {'id':id, 'name':name, 'group':group, 'group_name':group, 'risk':'P1',
            'principle':principle, 'method':method, 'fields':list(fields), 'enabled':True}


def _base(id, name, scope, materials, title, rules):
    return {'id':id,'version':1,'name':name,'scope':scope,'material_types':materials,
            'report':{'title':title},'knowledge_ids':[],'rules':rules}


BASE = {
    FINANCE:_base(FINANCE,'活动费用审核','活动费用材料及对应合同、发票、付款与执行凭证',
                  ['合同','报销主单','报销面单','支付凭证','活动方案','现场照片','发票','辅助凭证'],'财务费用审核报告',FINANCE_RULES),
    'contract':_base('contract','合同审核（示例）','对照待审合同与适用模板，记录条款差异和缺失依据',
        ['待审合同','标准模板','例外审批'],'合同条款审核报告',[
            _rule('CON-01','付款起算条件对照','条款比对','对照明确标注的合同与模板付款起算条件；比较原文，差异交法务核实。','compare_fields',['合同付款起算条件','模板付款起算条件']),
            _rule('CON-02','签约主体要素','要素检查','核对明确标注的甲方名称和乙方名称是否均可定位。','required_fields',['甲方名称','乙方名称']),
            _rule('CON-03','例外依据核验','条款比对','对照实际条款与适用模板；偏离时核对例外批准材料、对象及适用期限，缺依据时转人工。')]),
    'carrier':_base('carrier','载具审核（示例）','对照登记材料与现场凭证，核实载具标识与材料一致性',
        ['登记记录','现场凭证','检验记录'],'载具核验报告',[
            _rule('CAR-01','登记与现场标识','标识核验','核对登记标识和现场标识文字，无法明确辨认时应补证。','compare_fields',['登记载具编号','现场载具编号']),
            _rule('CAR-02','登记信息要素','登记核验','检查登记单位与登记载具编号是否均可定位。','required_fields',['登记单位','登记载具编号']),
            _rule('CAR-03','现场证据可核验性','现场核验','核查现场照片中对象与标识的可辨认程度，对照登记文件。遮挡、无图或无法确认对应关系时转人工。')])
}


def active_rules(scheme):
    return [r for r in scheme['rules'] if r.get('enabled',True)]


def applicable(scheme, evidence):
    types={e.get('category') for e in evidence}
    rules, omitted=[],[]
    for rule in active_rules(scheme):
        allowed=rule.get('applies_to',[])
        if allowed and types and '业务材料' not in types and not types.intersection(allowed):
            omitted.append({'id':rule['id'],'name':rule['name'],'reason':'本次材料未触发适用范围：'+'、'.join(allowed)})
        else:
            rules.append(rule)
    return rules,omitted


def classify(scheme,name,body):
    if scheme['id']==FINANCE:
        from .materials import classify as finance_type
        return finance_type(name,body)
    for source in (Path(name).stem, body):
        for kind in sorted(scheme['material_types'],key=len,reverse=True):
            if kind in source:
                return kind
    return '业务材料'


def knowledge_docs(scheme,docs=None):
    allow=set(scheme.get('knowledge_ids',[]))
    docs=store.all_items('knowledge') if docs is None else docs
    return [d for d in docs if d.get('scheme_id',FINANCE)==scheme['id']
            and not d.get('demo') and (not allow or d['id'] in allow)]


def _labelled_fields(labels, evidence):
    """Read configured label/value pairs without inferring missing values."""
    for page in evidence:
        for line in page.get('text','').splitlines():
            line=line.strip()
            key,sep,value=line.replace('：',':',1).partition(':')
            if not sep and line.count('|')==1:
                key,sep,value=line.partition('|')
            if sep and key.strip() in labels and value.strip():
                yield key.strip(),value.strip(),page


def extracted_facts(scheme,evidence):
    labels={f for rule in active_rules(scheme) for f in rule.get('fields',[])}
    facts=[]
    for key,value,page in _labelled_fields(labels,evidence):
        locator=dict(page.get('locator') or {'page':page.get('page')})
        locator['field']=key
        facts.append({'id':store.uid(),'label':key,'value':value,'evidence_id':page['id'],
                      'category':page.get('category','业务材料'),'confirmed':False,
                      'locator':locator,'source':'按方案定位原文标签'})
    return facts


def field_result(rule, evidence):
    """Only exact, labelled, unambiguous evidence is eligible for a program verdict."""
    facts={label:[] for label in rule['fields']}
    uncertain=False
    for key,value,page in _labelled_fields(facts,evidence):
        facts[key].append((value,page['id']))
        if (page.get('error') or '[看不清]' in value or
                not page.get('confirmed') and page.get('recognition',{}).get('status') in ('partial','unreadable','needs_review')):
            uncertain=True
    ids=list(dict.fromkeys(page for values in facts.values() for _,page in values))
    missing=[f for f,values in facts.items() if not values]
    ambiguous=[f for f,values in facts.items() if len({v for v,_ in values})>1]
    summary='；'.join(f'{label}：'+ '、'.join(dict.fromkeys(v for v,_ in values)) for label,values in facts.items() if values)
    if missing or ambiguous or uncertain:
        basis='；'.join(x for x in ('缺少可定位字段：'+'、'.join(missing) if missing else '',
                                  '同一字段出现多种值：'+'、'.join(ambiguous) if ambiguous else '',
                                  '识别存在不确定区域' if uncertain else '') if x)
        verdict='待人工判断'
    elif rule['method']=='compare_fields':
        equal=len({values[0][0] for values in facts.values()})==1
        verdict='通过' if equal else '不通过'
        basis='指定字段原文一致' if equal else '指定字段原文不一致，需核对差异与有效例外'
    else:
        verdict='通过'
        basis='每个指定字段均有唯一的非空文字值'
    return {'id':rule['id'],'facts':summary or '未定位到指定字段','verdict':verdict,'basis':basis,
            'evidence_ids':ids,'missing':'、'.join(missing),'suggestion':'请对照原件复核' if verdict!='通过' else '',
            'execution':'completed','engine':'程序字段核验'}


def _text(value, label, maximum):
    if not isinstance(value,str) or not value.strip() or len(value)>maximum:
        raise ValueError(f'{label}须为 1–{maximum} 字符')
    return value.strip()


def _strings(items, label, maximum, size=80):
    if not isinstance(items,list) or len(items)>maximum:
        raise ValueError(f'{label}最多 {maximum} 项')
    vals=[_text(x,label,size) for x in items]
    if len(set(vals))!=len(vals):raise ValueError(label+'包含重复值')
    return vals


def _id(value):
    if not isinstance(value,str) or not ID.fullmatch(value):
        raise ValueError('标识须使用 1–64 位字母、数字、下划线或短横线')
    return value


def _meaning(rule):
    if rule is None:
        return None
    return {'method':'model','enabled':True,'applies_to':[],'fields':[],**rule}


def _validate(id, value):
    if not isinstance(value,dict) or value.get('id',id)!=id: raise ValueError('方案标识无效')
    get=value.get
    materials=_strings(get('material_types'),'材料类型',60)
    if not materials: raise ValueError('至少指定一种材料类型')
    if not isinstance(get('report'),dict):raise ValueError('报告配置无效')
    scope=_text(get('scope'),'适用范围',1200)
    out=_base(id,_text(get('name'),'方案名称',80),scope,materials,_text(get('report').get('title'), '报告标题',120),[])
    out['knowledge_ids']=_strings(get('knowledge_ids',[]),'知识范围',120)
    if out['knowledge_ids']:
        candidates={d['id'] for d in store.all_items('knowledge') if d.get('scheme_id',FINANCE)==id and not d.get('demo')}
        if not set(out['knowledge_ids'])<=candidates:
            raise ValueError('引用的知识不存在或不属于本方案')
    rules=get('rules')
    if not isinstance(rules,list) or not 1<=len(rules)<=200:raise ValueError('方案须有 1–200 个审核项')
    seen=set()
    for rule in rules:
        if not isinstance(rule,dict):raise ValueError('审核项必须是对象')
        rid=_id(rule.get('id'))
        if rid in seen:raise ValueError('审核项编号重复')
        seen.add(rid)
        clean={'id':rid}
        for field,label,limit in [('name','审核项名称',100),('group','审核组',70),('principle','审核口径',3000),('risk','风险等级',20)]:
            clean[field]=_text(rule.get(field),label,limit)
        for field in ('group_name','version','source','pass_condition','fail_condition','manual_condition','conditions_note'):
            if field in rule:clean[field]=_text(rule[field],field,1500)
        if 'enabled' in rule:
            if type(rule['enabled']) is not bool:raise ValueError('启用状态必须为布尔值')
            clean['enabled']=rule['enabled']
        method=rule.get('method','model')
        if method not in ('model','required_fields','compare_fields'):raise ValueError('未注册的核验能力')
        if 'method' in rule:clean['method']=method
        fields=_strings(rule.get('fields',[]),'核验字段',10)
        if method=='compare_fields' and len(fields)!=2:raise ValueError('字段比对须配置两个标签')
        if method=='required_fields' and not fields:raise ValueError('字段检查至少指定一个标签')
        if 'fields' in rule:clean['fields']=fields
        types=_strings(rule.get('applies_to',[]),'适用材料',60)
        if not set(types)<=set(materials):raise ValueError('审核项引用了未配置的材料类型')
        if 'applies_to' in rule:clean['applies_to']=types
        out['rules'].append(clean)
    if not active_rules(out):raise ValueError('至少启用一个审核项')
    return out


def _seed(id):
    scheme=copy.deepcopy(BASE.get(id))
    return {'id':id,'draft':scheme,'revision':0,'versions':[scheme] if scheme else [],'trial':None}


def _load(db,id):
    row=db.execute("SELECT body FROM objects WHERE kind='audit_scheme' AND id=?",(id,)).fetchone()
    return json.loads(row[0]) if row else _seed(id)


def _write(db,record):
    db.execute("INSERT INTO objects VALUES('audit_scheme',?,?) ON CONFLICT(kind,id) DO UPDATE SET body=excluded.body",
               (record['id'],json.dumps(record,ensure_ascii=False)))


def _expected(record,body):
    if not isinstance(body,dict) or type(body.get('revision')) is not int or body['revision']!=record['revision']:
        raise ValueError('方案已更新，请刷新后重试')


def published(id=FINANCE,version=None):
    with store.connect() as db:record=_load(db,_id(id))
    if version is None and record['versions']:return record['versions'][-1]
    for snap in record['versions']:
        if str(snap['version'])==str(version):return snap
    raise ValueError('找不到此方案的发布版本')


def _ids():
    return list(dict.fromkeys([*BASE,*(r['id'] for r in store.all_items('audit_scheme'))]))


def list_schemes():
    with store.connect() as db:
        return [r['versions'][-1] for id in _ids() if (r:=_load(db,id))['versions']]


def manage_list():
    with store.connect() as db:
        records=[_load(db,id) for id in _ids()]
    return [{'id':r['id'],'name':r['draft']['name'],'revision':r['revision'],
             'version':r['versions'][-1]['version'] if r['versions'] else None,
             'status':'已发布' if r['versions'] and r['draft']==r['versions'][-1] else '草稿'} for r in records]


def _examples(id):
    if id==FINANCE:
        return [{'name':'费用明细为零','pages':[{'category':'报销主单','text':'费用明细表 0.00'}],'expected':{'T1-02-13':'不通过'}}]
    if id not in BASE:return []
    left,right,extra=('合同付款起算条件','模板付款起算条件','甲方名称：示例甲方\n乙方名称：示例乙方') if id=='contract' else ('登记载具编号','现场载具编号','登记单位：示例单位')
    labels=['CON-01','CON-02'] if id=='contract' else ['CAR-01','CAR-02']
    a,b=('验收后30日','交付后30日') if id=='contract' else ('ZJ-101','ZJ-102')
    return [{'name':name,'pages':[{'category':BASE[id]['material_types'][0],'text':f'{left}：{a}\n{extra}'},
                                  {'category':BASE[id]['material_types'][1],'text':text}],
             'expected':{labels[0]:verdict,labels[1]:'通过'}}
            for name,text,verdict in [('一致',right+'：'+a,'通过'),('差异',right+'：'+b,'不通过'),('缺少对照','未提供对应信息','待人工判断')]]


def detail(id):
    with store.connect() as db:r=_load(db,_id(id))
    if not r['draft']:raise ValueError('方案不存在')
    return {'id':id,'draft':r['draft'],'revision':r['revision'],'published':r['versions'][-1] if r['versions'] else None,
            'history':[{k:s.get(k) for k in ('version','name','published_at','note')} for s in reversed(r['versions'])],
            'trial':r['trial'],'sample_examples':_examples(id)}


def save(id,body):
    _id(id)
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        r=_load(db,id);_expected(r,body)
        snap=_validate(id,body.get('draft'))
        snap['version']=(r['versions'][-1]['version']+1) if r['versions'] else 1
        r.update(draft=snap,revision=r['revision']+1,trial=None);_write(db,r)
    return detail(id)


def _sample_pages(sample):
    if not isinstance(sample,dict):raise ValueError('样本须为对象')
    name=_text(sample.get('name'),'样本名称',100)
    pages=sample.get('pages')
    if not isinstance(pages,list) or not 1<=len(pages)<=20:raise ValueError('每份试跑样本须有 1–20 页')
    evidence=[]
    for i,page in enumerate(pages,1):
        if not isinstance(page,dict):raise ValueError('样本页须含类型及正文')
        evidence.append({'id':f'D{i:03d}-P1','category':_text(page.get('category'),'样本材料类型',80),
                         'name':name,'text':_text(page.get('text'),'样本正文',10000),'page':1,'method':'试跑文本','image':'','error':''})
    return name,evidence


def trial(id,body):
    from . import engine,harness,knowledge
    with store.connect() as db:r=_load(db,_id(id))
    _expected(r,body)
    scheme=r['draft']
    if not scheme:raise ValueError('请先保存方案')
    samples=body.get('samples',_examples(id))
    if not isinstance(samples,list) or not 1<=len(samples)<=12:raise ValueError('须提供 1–12 份带预期结果的试跑样本')
    rules={x['id']:x for x in active_rules(scheme)}
    cases=[]
    for sample in samples:
        name,evidence=_sample_pages(sample)
        expected=sample.get('expected')
        if not isinstance(expected,dict) or not expected or not set(expected)<=rules.keys():raise ValueError('预期须引用已启用审核项')
        if any(v not in (*VERDICTS,'不适用') for v in expected.values()):raise ValueError('样本预期结论无效')
        ids={x['id'] for x in applicable(scheme,evidence)[0]}
        pending=[]
        calc={}
        if id==FINANCE:
            for e in evidence:e['method']='PDF文本'
            calc=engine.finance_checks({'evidence':evidence,'facts':engine.facts_from(evidence)},list(rules.values()))
        answers={}
        for rid in expected:
            if rid not in ids:answers[rid]={'verdict':'不适用','basis':'未触发适用材料范围'}
            elif rules[rid].get('method','model')!='model':answers[rid]=field_result(rules[rid],evidence)
            elif rid in calc:answers[rid]=calc[rid]
            else:pending.append(rules[rid])
        if pending:
            query=' '.join(rule['name'] for rule in pending)+' '+' '.join(e['text'][:200] for e in evidence)
            snippets=knowledge.retrieve(query[:2000],method='hybrid',docs=knowledge_docs(scheme))['results']
            vals,errors,_,_=harness.execute(pending,evidence,snippets,calc,scheme=scheme)
            answers.update({v['id']:v for v in vals})
            for rid,reason in errors.items():answers.setdefault(rid,{'verdict':'技术未完成','basis':reason})
        for rid,want in expected.items():
            answer=answers[rid];actual=answer['verdict']
            cases.append({'sample':name,'rule_id':rid,'expected':want,'actual':actual,'matched':want==actual,
                          'basis':answer['basis'],'evidence_ids':answer.get('evidence_ids',[])})
    matched=sum(c['matched'] for c in cases)
    covered={x['rule_id'] for x in cases if x['actual']!='不适用'}
    old={x['id']:x for x in active_rules(r['versions'][-1])} if r['versions'] else {}
    needed=sorted(rid for rid,rule in rules.items() if _meaning(old.get(rid))!=_meaning(rule) and rid not in covered)
    outcome={'revision':r['revision'],'checked_at':store.now(),'total':len(cases),'matched':matched,'cases':cases,
             'covered_rule_ids':sorted(covered),'uncovered_changes':needed,
             'passed':matched==len(cases) and bool(covered) and not needed}
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        current=_load(db,id);_expected(current,body);current['trial']=outcome;_write(db,current)
    return detail(id)


def publish(id,body):
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        r=_load(db,_id(id));_expected(r,body)
        trial_result=r.get('trial')
        if not trial_result or not trial_result['passed'] or trial_result['revision']!=r['revision']:
            raise ValueError('须先用当前草稿通过样本试跑，覆盖新增或变更的启用项')
        if r['versions'] and r['versions'][-1]['version']==r['draft']['version']:raise ValueError('请先保存新草稿')
        snap=copy.deepcopy(r['draft'])
        snap.update(published_at=store.now(),note=_text(body.get('note'),'发布说明',500),trial=trial_result)
        r['versions'].append(snap);r.update(draft=snap,revision=r['revision']+1);_write(db,r)
    return detail(id)
