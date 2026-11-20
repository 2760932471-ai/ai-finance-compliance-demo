"""Bounded execution and evidence checks. No generated claim bypasses validation."""
import hashlib,json,os,time
from collections import Counter
from . import model,store

VERSION='audit-harness-v3'
STATES=('通过','不通过','待人工判断')

def inspect(results,rules,evidence):
    expected={r['id'] for r in rules};valid={e['id'] for e in evidence}
    if not isinstance(results,list):return {},{r:'结果必须为数组' for r in expected},['无效结果数组']
    counts=Counter(r.get('id') for r in results if isinstance(r,dict) and isinstance(r.get('id'),str))
    accepted={};errors={};extra=[]
    for rid in expected:
        if counts[rid]!=1:errors[rid]='规则缺失或重复';continue
        r=next(x for x in results if isinstance(x,dict) and x.get('id')==rid)
        if r.get('verdict') not in STATES:errors[rid]='结论必须为三个规定值之一'
        elif not isinstance(r.get('evidence_ids'),list) or any(not isinstance(e,str) or e not in valid for e in r['evidence_ids']):errors[rid]='证据引用不存在'
        elif not r['evidence_ids'] and (r['verdict']=='通过' or not isinstance(r.get('missing'),str) or not r['missing'].strip()):errors[rid]='结论缺少证据或缺失说明，通过结论必须有证据'
        elif any(not isinstance(r.get(k),str) or not r[k].strip() for k in ('facts','basis')):errors[rid]='事实或依据为空'
        else:accepted[rid]=r
    extra=[str(k) for k in counts if k not in expected]
    if any(not isinstance(r,dict) or not isinstance(r.get('id'),str) for r in results):extra.append('结果包含无效条目或编号')
    return accepted,errors,extra

def execute(rules,evidence,knowledge,calculations,on_trace=lambda x:None,infer=None):
    infer=infer or model.infer
    pending=list(rules);accepted={};metas=[];traces=[];repair={}
    for attempt in range(1,3):
        prompt=model.rule_prompt(pending,evidence,knowledge,calculations)
        if repair:prompt+='\n前次校验失败，仅修复本次请求条目：'+json.dumps(repair,ensure_ascii=False)
        trace={'id':store.uid(),'version':VERSION,'group':'T1全部规则' if len({r['group'] for r in rules})>1 else rules[0]['group'],'attempt':attempt,'rule_ids':[r['id'] for r in pending],'input_hash':hashlib.sha256(prompt.encode()).hexdigest(),'time':store.now(),'evidence_count':len(evidence),'checks':['规则覆盖','结论枚举','证据存在','事实与依据','通过必须有证据']}
        start=time.monotonic()
        try:
            value,meta=infer(prompt,model.RESULT_SCHEMA,[e['image'] for e in evidence if e.get('image')],timeout=int(os.getenv('MODEL_TIMEOUT','600')),force=attempt>1)
            metas.append(meta);trace['model_job']=meta.get('job_id',meta.get('original_job',''));trace['cached']=meta.get('cached',False)
            good,repair,extra=inspect(value.get('results'),pending,evidence)
            accepted.update(good);trace.update(status='passed' if not repair and not extra else 'repair',accepted=len(good),errors=repair,extra_ids=extra)
            pending=[r for r in pending if r['id'] not in good]
        except Exception as exc:
            repair={r['id']:str(exc)[:240] for r in pending};trace.update(status='failed',accepted=0,errors=repair)
        trace['seconds']=round(time.monotonic()-start,2);traces.append(trace);on_trace(trace)
        if not pending:break
        # Configuration/authentication failures cannot be repaired by prompting.
        if any('配置不兼容' in x or '登录失效' in x or '用量受限' in x for x in repair.values()):break
    return [accepted[r['id']] for r in rules if r['id'] in accepted],repair,metas,traces

def coverage(results,rules,evidence):
    good,errors,extra=inspect(results,rules,evidence)
    return {'version':VERSION,'expected':len(rules),'returned':len(results),'valid':len(good),'errors':errors,'extra_ids':extra,'passed':not errors and not extra,'checked_at':store.now()}
