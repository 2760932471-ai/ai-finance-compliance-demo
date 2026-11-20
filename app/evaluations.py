"""Reviewed expectations stay separate from model output and official exam material."""
import hashlib,json
from . import store,engine

def material_key(run):
    return hashlib.sha256(json.dumps(sorted(d['sha256'] for d in run.get('documents',[]))).encode()).hexdigest()

def add_case(feedback,expected,actor,reason):
    if expected not in engine.STATES or not actor.strip() or len(reason.strip())<5:raise ValueError('回归样本需提供预期结论、确认人及至少5字的依据')
    run=store.get('run',feedback.get('run_id',''))
    if not any(r['id']==feedback.get('rule_id') for r in run.get('results',[])):raise ValueError('请从具体审核条目的反馈建立回归样本')
    existing=next((c for c in store.all_items('eval_case') if c['feedback_id']==feedback['id']),None)
    if existing:return existing
    return store.put('eval_case',{'id':store.uid(),'feedback_id':feedback['id'],'source_run':run['id'],'material_key':material_key(run),'rule_id':feedback['rule_id'],'expected':expected,'actor':actor.strip(),'reason':reason.strip(),'created_at':store.now()})

def evaluate():
    cases=store.all_items('eval_case');runs=store.all_items('run');rows=[]
    for c in cases:
        # Compare raw system output; using a manual override would grade the answer against itself.
        candidates=[r for r in runs if r.get('status')=='completed' and material_key(r)==c['material_key']]
        candidates.sort(key=lambda r:r['created_at'],reverse=True)
        run=candidates[0] if candidates else None
        actual=next((r for r in run.get('results',[]) if r['id']==c['rule_id']),None) if run else None
        status='未运行' if not actual or actual.get('execution')!='completed' else '一致' if actual['verdict']==c['expected'] else '差异'
        rows.append({'case_id':c['id'],'rule_id':c['rule_id'],'expected':c['expected'],'actual':actual['verdict'] if actual else None,'run_id':run['id'] if run else '', 'status':status})
    return store.put('evaluation',{'id':store.uid(),'created_at':store.now(),'total':len(rows),'matched':sum(x['status']=='一致' for x in rows),'different':sum(x['status']=='差异' for x in rows),'not_run':sum(x['status']=='未运行' for x in rows),'rows':rows,'scope':'人工确认样本一致性，不代表全量业务准确率'})
