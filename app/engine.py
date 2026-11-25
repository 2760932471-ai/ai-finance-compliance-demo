import copy,json,re,time,traceback
from collections import Counter
from pathlib import Path
from decimal import Decimal,InvalidOperation
from datetime import datetime
from . import knowledge as knowledge_service,store,materials,model,harness,decisions,recognition,checks,schemes

RULES=schemes.FINANCE_RULES
STATES=('通过','不通过','待人工判断')

def normalize(text):
    text=text.replace('．','.').replace('，',',')
    # Windows OCR spaces CJK glyphs. Preserve line boundaries and original text separately.
    return re.sub(r'(?<=[\u3400-\u9fff\d]) +(?=[\u3400-\u9fff\d])','',text)

def facts_from(evidence):
    facts=[]
    def add(label,val,e):
        facts.append({'id':store.uid(),'label':label,'value':val,'evidence_id':e['id'],'category':e['category'],'confirmed':e['method']=='PDF文本','source':'自动提取'})
    for e in evidence:
        t=normalize(e['text'])
        patterns={'报销单号':r'BX\d{8,}', '方案号':r'FA\d{10,}', '预算编号':r'YS\d{5,}', '活动日期':r'20\d{2}[-年/.]\d{1,2}[-月/.]\d{1,2}日?'}
        for label,pattern in patterns.items():
            for val in list(dict.fromkeys(re.findall(pattern,t)))[:16]:add(label,val,e)
        if e['category']=='报销主单':
            m=re.search(r'核报总额[:：]\s*([\d,.]+)',t)
            if m:add('核报金额',m.group(1).replace(',',''),e)
        for pattern in [r'合计\s*(\d[\d,]*\.\d{2})\s+(\d[\d,]*\.\d{2})',r'填报金额\s*[:：]\s*([\d,.]+)']:
            for m in re.finditer(pattern,t):
                if len(m.groups())==2:add('主表填报金额',m.group(1),e);add('主表核报金额',m.group(2),e)
        if e['category']=='现场照片':
            for brand in ('青花郎','红花郎','小郎酒','郎牌特曲'):
                if brand in t:add('照片可见品牌文字',brand,e)
    return facts

def deterministic(run):
    es=run['evidence'];out={};facts=run['facts']
    def make(rid,verdict,fact,ids,basis,suggestion=''):
        out[rid]={'id':rid,'verdict':verdict,'facts':fact,'evidence_ids':ids,'basis':basis,'missing':'','suggestion':suggestion,'engine':'程序校验','execution':'completed'}
    main=[e for e in es if e['category']=='报销主单' and e['method']=='PDF文本']
    for e in main:
        t=normalize(e['text'])
        if '费用明细表' in t and re.search(r'费用明细表\s*0\.00',t):
            make('T1-02-13','不通过','报销主单费用明细表栏显示0.00；需进一步核对独立明细文件。',[e['id']],'规则明确金额0.00属于P1拒绝条件。','提供真实、内容完整的费用明细并复核。')
    nums=[f for f in facts if f['label'] in ('主表填报金额','主表核报金额') and f['confirmed']]
    if len(nums)==2 and len({f['evidence_id'] for f in nums})==1:
        # This checks numeric part only; rule also requires uppercase consistency.
        try:
            amounts=[Decimal(f['value'].replace(',','')) for f in nums]
            if not all(n.is_finite() for n in amounts):raise InvalidOperation()
            if amounts[0]!=amounts[1]:
                make('T1-02-02','不通过',f'填报金额{nums[0]["value"]}与核报金额{nums[1]["value"]}不一致。',[nums[0]['evidence_id']],'数字文本中的填报与核报金额不一致。','核实填报金额和核报金额及其依据。')
        except InvalidOperation:
            make('T1-02-02','待人工判断','提取的金额格式异常，不能完成数值比对。',[nums[0]['evidence_id']],'数字文本可能提取错误，需对照原件确认金额。','核对并校正金额后重新审核。')
    photos=[e for e in es if e['category']=='现场照片']
    mismatches=[]
    for e in photos:
        t=normalize(e['text']);m=re.search(r'(20\d{2})年(\d{1,2})月(\d{1,2})日.{0,8}星期([一二三四五六日天])',t)
        if m:
            try:
                weekday='一二三四五六日'[datetime(*map(int,m.groups()[:3])).weekday()]
                if m.group(4).replace('天','日')!=weekday:mismatches.append(e['id'])
            except ValueError:pass
    if mismatches:make('T1-03-01','待人工判断','OCR识别的日期与星期存在矛盾候选，需对照原图确认。',mismatches,'程序仅验证OCR文字的内部一致性，尚不足以断定原图错误。','核实照片原始水印；确认后重新审核。')
    return out

def finance_checks(run,rules):
    """Hard-coded T1 checks apply only to their original normative conditions."""
    standards={r['id']:r for r in RULES}
    validated={r['id'] for r in rules
               if r['id'] in standards and all(r.get(k)==standards[r['id']].get(k)
                                               for k in ('principle','pass_condition','fail_condition','manual_condition'))}
    return {rid:result for rid,result in deterministic(run).items() if rid in validated}


def fallback(rule,es,why):
    cats={'T1-01':['合同','报销主单','活动方案'],'T1-02':['合同','支付凭证','报销主单','报销面单','发票'],'T1-03':['现场照片','报销面单','报销主单','合同'],'T1-04':['支付凭证','报销主单','发票','现场照片'],'T1-05':['合同','支付凭证','报销主单','现场照片']}
    relevant=([e for e in es if e['category'] in cats[rule['group']]] if rule['group'] in cats else es)
    return {'id':rule['id'],'verdict':'待人工判断','facts':f'已整理{len(relevant)}个相关材料页；本条需核对：{rule["principle"]}','evidence_ids':[e['id'] for e in relevant[:8]],'basis':why,'missing':'' if relevant else '材料缺失：未找到本条相关材料类别。','suggestion':'查看关联原件并完成本条人工复核。','engine':'待复核','execution':'pending' if '模型' in why else 'completed'}

def validate_results(results,rules,es):
    good,errors,extra=harness.inspect(results,rules,es)
    if errors or extra:raise ValueError('输出校验失败：'+json.dumps(errors,ensure_ascii=False))
    return results

def effective_results(run,reviews=None):
    # SQLite returns newest first. Reverse before the stable timestamp sort so
    # multiple reviews within one second retain their persisted write order.
    reviews=store.related('review','run_id',run['id']) if reviews is None else reviews
    reviews=sorted(reversed(reviews),key=lambda x:x['time'])
    latest={rev['rule_id']:rev for rev in reviews}
    final=[]
    for original in run.get('results',[]):
        result=dict(original)
        rev=latest.get(result['id'])
        if rev:
            result.update(ai_result=copy.deepcopy(original),ai_verdict=original.get('ai_verdict',original['verdict']),
                          verdict=rev['after'],review=rev,execution='completed',evidence_ids=rev['evidence_ids'])
        final.append(result)
    return final

def summary(run):
    return summarize_results(effective_results(run),run.get('rules',RULES))

def summarize_results(results,rules):
    counts={s:sum(r['verdict']==s for r in results) for s in STATES}
    expected=[rule['id'] for rule in rules]
    returned=[result['id'] for result in results]
    incomplete=not expected or Counter(expected)!=Counter(returned) or any(r.get('execution')!='completed' for r in results)
    verdict='不通过' if counts['不通过'] else '待人工判断' if counts['待人工判断'] or incomplete else '通过'
    risk={r['id']:r['risk'] for r in rules}
    return {'counts':counts,'verdict':verdict,'expected':len(expected),'total':len(results),'complete':not incomplete,'blockers':sum(r['verdict']=='不通过' and risk.get(r['id'])=='P0' for r in results),'reviewed':sum('review' in r for r in results)}

def update(run,phase,progress,message):
    run.update(phase=phase,progress=progress,updated_at=store.now());run.setdefault('events',[]).append({'time':store.now(),'message':message});store.put('run',run)

def process(run_id):
    run=store.get('run',run_id);started=time.time();folder=store.DATA/'runs'/run_id;folder.mkdir(parents=True,exist_ok=True)
    try:
        scheme = run.get('scheme_snapshot') or schemes.published(schemes.FINANCE,1)
        finance = scheme['id']==schemes.FINANCE
        version = scheme['rules'][0].get('version') if finance and scheme['version']==1 else None
        run.update(status='processing',error='',usage=[],results=[],traces=[],rules=schemes.active_rules(scheme),
                   rule_version=version or f"{scheme['id']}@{scheme['version']}",
                   harness_version=harness.VERSION,app_version='0.8',scheme_snapshot=scheme,
                   scheme_id=scheme['id'],scheme_version=scheme['version'])
        update(run,'整理材料',4,'正在检查原始压缩包与文件路径')
        manifest,docs=materials.unpack(Path(run['source']),folder/'files')
        run.update(manifest=manifest,documents=docs)
        update(run,'识别内容',16,f'已整理{len(docs)}份唯一业务材料，保留重复与资源映射')
        def progress(i,n,name):update(run,'识别内容',16+int(i/max(1,n)*26),f'处理材料 {i+1}/{n}：{name}')
        es=materials.recognize(docs,manifest,folder,progress)
        run['recognition_version']=recognition.VERSION
        run['recognition_usage']=[]
        def visual_progress(i,n,message):
            update(run,'视觉识别',42+int(i/max(1,n)*3),message)
        def visual_record(meta):
            run['recognition_usage'].append(meta);run['usage'].append(meta);store.put('run',run)
        recognition.enhance(es,docs,folder,visual_progress,visual_record,scheme=scheme)
        for correction in run.get('corrections',[]):
            page=correction.get('page',int(correction['evidence_id'].split('-P')[-1]))
            e=next((e for e in es if e['page']==page and e.get('sha256')==correction['sha256']),None)
            if not e:raise ValueError('待校正证据已变化，请对照当前材料重新提交')
            e.update(original_text=e.get('original_text',e['text']),text=correction['text'],method='人工校正',correction=correction,confirmed=True,error='')
        for d in docs:
            pages=[e for e in es if e['document_id']==d['id']]
            d['category']=schemes.classify(scheme,d['name'],'\n'.join(e['text'] for e in pages))
            for e in pages:e['category']=d['category']
            uncertain=[e for e in pages if e.get('error') or e.get('recognition',{}).get('status') in ('partial','unreadable','needs_review') and not e.get('confirmed')]
            d.update(status='识别待核对' if uncertain else '已识别',error='有不确定文字，请对照原图' if uncertain else '')
        applicable_rules,omitted = schemes.applicable(scheme,es)
        run.update(rules=applicable_rules,excluded_rules=omitted,evidence=es,
                   facts=facts_from(es) if finance else schemes.extracted_facts(scheme,es))
        calc=finance_checks(run,applicable_rules) if finance else {}
        run['technical_checks']=checks.collect(es,docs)
        # Stable inputs keep the existing T1 caches usable after migration.
        calc['_local_checks']={k:v for k,v in run['technical_checks'].items() if k!='checked_at'}
        run['calculations']=calc
        query=' '.join(r['name'] for r in applicable_rules)+' '+ ' '.join(e.get('text','')[:200] for e in es)[:500]
        recall=knowledge_service.retrieve((query.strip() or scheme['name'])[:2000],top_k=12,method='hybrid',docs=schemes.knowledge_docs(scheme))
        knowledge=recall['results']
        run['knowledge_snapshot']=knowledge
        run['knowledge_retrieval']={k:v for k,v in recall.items() if k!='results'}
        total=len(applicable_rules)
        update(run,'规则审核',46,f'材料识别完成，核对本次适用的 {total} 条审核项')
        program_rules=[r for r in applicable_rules if r.get('method','model')!='model']
        model_rules=[r for r in applicable_rules if r.get('method','model')=='model']
        values={r['id']:schemes.field_result(r,es) for r in program_rules}
        def traced(t):
            run['traces'].append(t);store.put('run',run)
        generated,errors,metas,_=harness.execute(model_rules,es,knowledge,calc,traced,scheme=scheme,task=run.get('supervision_task'))
        run['usage'].extend(metas)
        for result in generated:
            result.update(engine='Codex AI',execution='completed')
            values[result['id']]=result
        any_failure=False
        for rule in applicable_rules:
            rid=rule['id']
            value=values.get(rid)
            if value is None:
                any_failure=True
                value=fallback(rule,es,'模型执行未完成：'+errors.get(rid,'结果不完整'))
            override=calc.get(rid) if rule.get('method','model')=='model' else None
            if override and (override['verdict']=='不通过' or override['verdict']=='待人工判断' and value['verdict']=='通过'):
                value.update(override)
            value.update(name=rule['name'],group=rule['group'],risk=rule['risk'],principle=rule['principle'])
            run['results'].append(value)
        update(run,'规则审核',94,f'已记录 {len(run["results"])}/{total} 条审核项结果')
        run['decision_input_results']=json.loads(json.dumps(run['results']))
        if decisions.config()['provider']!='off':update(run,'证据一致性复核',95,'正在独立核查证据支持度与规则结论')
        def decision_progress(count,total,record):
            run['decision_review']={**record,'status':'处理中'}
            update(run,'证据一致性复核',95+int(count/max(1,total)*4),f'独立决策模型已复核 {count}/{total} 项')
        run['decision_review']=decisions.check(run['results'],es,decision_progress)
        run['quality']=harness.coverage(run['results'],applicable_rules,es)
        run.update(status='needs_attention' if any_failure or not applicable_rules else 'completed',duration=round(time.time()-started,1))
        update(run,'待人工复核',100,'逐项结果已保存；请检查待判断项并完成人工复核')
    except Exception as exc:
        run.update(status='failed',error=str(exc)[:500],duration=round(time.time()-started,1))
        update(run,'处理失败',run.get('progress',0),'处理失败，原包已保留，可重试')

def chat(run,question,rule_id=''):
    es=run.get('evidence',[]);results=effective_results(run)
    selected=[r for r in results if not rule_id or r['id']==rule_id]
    eids={i for r in selected for i in r.get('evidence_ids',[])}
    originals=[e['image'] for e in es if e['id'] in eids and e.get('image')][:8] if rule_id else []
    evidence=[{'id':e['id'],'file':e['name'],'page':e['page'],'text':e['text'][:4000],'image_attached':e.get('image') in originals} for e in es if e['id'] in eids]
    prompt=model.system_for(run.get('scheme_snapshot'))+model.task_context(run.get('supervision_task'))+'\n任务：根据当前项目结果解释问题，给出真实补证或识别纠错建议。不得改写凭证、补造事实或自动改判。未知信息明确说明。\n用户问题：'+question+'\n审核结果：'+json.dumps(selected,ensure_ascii=False)+'\n证据：'+json.dumps(evidence,ensure_ascii=False)+'\n生效知识：'+json.dumps(run.get('knowledge_snapshot',[]),ensure_ascii=False)
    if originals:prompt+='\n附图顺序对应证据中image_attached为true的条目。请用原图核对OCR，不把识别乱码当事实。'
    val,meta=model.infer(prompt,model.CHAT_SCHEMA,originals,timeout=300)
    valid={e['id'] for e in es};ids={r['id'] for r in run.get('rules',RULES)}
    if any(e not in valid for e in val['evidence_ids']) or any(r not in ids for r in val['rule_ids']):raise ValueError('回答引用无法核实，请重试')
    return store.put('chat',{'id':store.uid(),'run_id':run['id'],'question':question,'rule_id':rule_id,'time':store.now(),**val,'meta':meta})
