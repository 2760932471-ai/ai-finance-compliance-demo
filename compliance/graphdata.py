"""Explicitly sourced graph records, separate from confirmed audit facts."""
import copy
from .domain import require,text,number
from .store import Store,Conflict
KINDS={'source':'来源材料待核实','synthetic':'演示生成','anonymized':'匿名化场景'}

def validate(rows):
    require(isinstance(rows,list) and 1<=len(rows)<=500,'图谱数据须为1—500条')
    result=[];ids=set()
    for raw in rows:
        require(isinstance(raw,dict),'图谱记录格式错误');r={}
        for k in ('id','name','supplier','bank_account','source_note'):
            text(raw.get(k),k,500);r[k]=raw[k]
        require(r['id'] not in ids,'记录编号重复');ids.add(r['id'])
        require(raw.get('source_kind') in KINDS,'请明确来源类型');r['source_kind']=raw['source_kind']
        for k in ('person','document','payment_date','source_ref'):
            v=raw.get(k,'');require(isinstance(v,str) and len(v)<=500,k+'格式错误');r[k]=v
        r['amount']=raw.get('amount');
        if r['amount'] is not None:number(r['amount'],'金额')
        result.append(r)
    return result

def state(store):
    with store.connect() as db:
        r=db.execute("SELECT body FROM state WHERE key='graph-data'").fetchone()
        if r:return store.get(db,'graph-data')
    return {'revision':0,'source':'当前业务记录','rows':[]}

def save(store,data):
    rows=validate(data.get('rows'));text(data.get('source'),'数据来源',160)
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE');r=db.execute("SELECT body FROM state WHERE key='graph-data'").fetchone();old=store.get(db,'graph-data') if r else {'revision':0}
        if data.get('revision')!=old['revision']:raise Conflict('图谱数据已变化，请刷新')
        if old['revision']:store.put(db,'graph-data-history-'+str(old['revision']),old)
        value={'revision':old['revision']+1,'source':data['source'],'rows':rows};store.put(db,'graph-data',value);store.audit(db,'导入图谱来源',f'{len(rows)}条 / {data["source"]}')
    return value

def scenario():
    from .seed import demo_events
    names=['锦程设备（演示）','青禾材料（演示）','明川商贸（演示）','新程服务（演示）','远山机电（演示）','和盛物流（演示）']
    rows=[]
    for i,r in enumerate(demo_events()):
        group=i%6
        r=copy.deepcopy(r);r.update(supplier=names[group],bank_account='演示结算账户-8031' if group in (0,1) else f'演示结算账户-{8200+group}',person=f'采购经办{group%3+1}（演示）',source_kind='synthetic',source_note='沿用原演示业务金额及流程场景，供应商、账户及关联关系均为演示生成。',source_ref='内置演示场景 v0.7')
        rows.append(r)
    return validate(rows)
