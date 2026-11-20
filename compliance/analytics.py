"""Historical baselines and explainable anomaly candidates; never final risk verdicts."""
import copy
import hashlib
import math
import random
import re
from collections import defaultdict
from datetime import date
from statistics import median

METRICS = {'amount':'金额','quantity':'数量','unit_price':'单价','payment_days':'付款周期（天）'}
GROUPS = {'unit':'单位','business_type':'业务类型','item':'品类','supplier':'供应商'}
ALGORITHMS = {'robust':'稳健统计区间','rolling':'跨周期滚动基线','isolation':'多指标 Isolation Forest'}


def require(condition, message):
    if not condition: raise ValueError(message)


def month(value):
    require(isinstance(value,str) and re.fullmatch(r'\d{4}-\d{2}', value), '月份须为YYYY-MM')
    try: date.fromisoformat(value+'-01')
    except ValueError: raise ValueError('月份无效') from None
    return value


def shift(value, offset):
    y,m=map(int,value.split('-'));n=y*12+m-1+offset
    return f'{n//12:04d}-{n%12+1:02d}'


def quantile(values, p):
    values=sorted(values);position=(len(values)-1)*p;i=int(position);fraction=position-i
    return values[i]*(1-fraction)+values[min(i+1,len(values)-1)]*fraction


def templates():
    base={'business_type':'全部','group_by':['unit','business_type','item'],'window_months':12,
          'min_samples':30,'sensitivity':3.5,'interval':'mad','quantile_low':.025,'quantile_high':.975,
          'contamination':.05,'seasonal':False,'enabled':True}
    return [dict(base,id='price',name='采购单价异常',algorithm='robust',metrics=['unit_price'],aggregation='transaction'),
            dict(base,id='expense',name='月度费用异常增长',algorithm='rolling',metrics=['amount'],aggregation='monthly_sum',min_samples=6,group_by=['unit','business_type']),
            dict(base,id='payment',name='多指标付款行为异常',algorithm='isolation',metrics=['amount','quantity','payment_days'],aggregation='transaction')]


def validate_models(models):
    require(isinstance(models,list) and 1<=len(models)<=12,'模型数量须为1—12个')
    result=[];ids=set()
    for raw in models:
        require(isinstance(raw,dict),'模型格式错误');m=copy.deepcopy(raw)
        require(isinstance(m.get('id'),str) and re.fullmatch(r'[a-z][a-z0-9_-]{0,40}',m['id']) and m['id'] not in ids,'模型编码无效或重复');ids.add(m['id'])
        require(isinstance(m.get('name'),str) and 0<len(m['name'].strip())<=100,'模型名称无效')
        require(m.get('algorithm') in ALGORITHMS,'算法不存在')
        require(isinstance(m.get('metrics'),list) and 1<=len(m['metrics'])<=4 and len(set(m['metrics']))==len(m['metrics']) and all(x in METRICS for x in m['metrics']),'指标无效或重复')
        require(isinstance(m.get('group_by'),list) and 1<=len(m['group_by'])<=4 and len(set(m['group_by']))==len(m['group_by']) and all(x in GROUPS for x in m['group_by']),'对比群体字段无效')
        require(isinstance(m.get('business_type'),str) and 0<len(m['business_type'])<=100,'业务范围无效')
        for key,low,high in [('window_months',1,36),('min_samples',3,1000)]:
            require(type(m.get(key)) is int and low<=m[key]<=high,f'{key}超出支持范围')
        for key,low,high in [('sensitivity',1,10),('quantile_low',0,.2),('quantile_high',.8,1),('contamination',.01,.2)]:
            value=m.get(key);require(type(value) in (int,float) and math.isfinite(value) and low<=value<=high,f'{key}超出支持范围')
        require(type(m.get('seasonal')) is bool and type(m.get('enabled')) is bool,'启停与季节选项须为布尔值')
        require(m.get('interval') in ('mad','quantile'),'区间方法无效')
        require(m.get('aggregation') in ('transaction','monthly_sum'),'聚合口径无效')
        if m['algorithm']=='rolling':
            require(m['aggregation']=='monthly_sum' and m['metrics']==['amount'],'滚动模型使用按月金额合计')
        else: require(m['aggregation']=='transaction','统计与多指标模型使用逐笔记录')
        if m['algorithm']=='isolation':require(len(m['metrics'])>=2 and m['min_samples']>=20,'多指标模型至少2个指标、20个历史样本')
        result.append(m)
    return result


def validate_rows(rows):
    require(isinstance(rows,list) and 1<=len(rows)<=5000,'历史数据须为1—5000条记录')
    result=[];ids=set()
    for raw in rows:
        require(isinstance(raw,dict),'业务记录须为对象');r={}
        for key in ('id','date',*GROUPS):
            value=raw.get(key);require(isinstance(value,str) and 0<len(value.strip())<=160,f'{key}缺失或无效');r[key]=value.strip()
        require(r['id'] not in ids,'业务编号重复');ids.add(r['id'])
        try: date.fromisoformat(r['date'])
        except ValueError:raise ValueError('业务日期无效') from None
        require(re.fullmatch(r'\d{4}-\d{2}-\d{2}',r['date']),'日期须为YYYY-MM-DD')
        for key in ('amount','quantity','payment_days'):
            v=raw.get(key);require(type(v) in (int,float) and math.isfinite(v) and 0<=v<=1e12,f'{key}缺失或非有限正数');r[key]=v
        require(r['quantity']>=1e-6,'数量须至少为0.000001，无法以缺失数量计算单价')
        r['unit_price']=r['amount']/r['quantity']
        require(raw.get('label') is None or type(raw['label']) is bool,'label只支持true、false或null')
        r['label']=raw.get('label');result.append(r)
    return sorted(result,key=lambda r:(r['date'],r['id']))


def demo_rows():
    rng=random.Random(20261120);rows=[]
    for offset in range(30):
        period=shift('2024-05',offset)
        for unit in ('示例华东','示例华南'):
            for business in ('采购付款','服务结算'):
                for i in range(12):
                    quantity=round(rng.uniform(60,140),2);price=round(100+rng.gauss(0,8)+(8 if period.endswith(('11','12')) else 0),2)
                    abnormal=period in ('2026-07','2026-10') and unit=='示例华东' and business=='采购付款' and i<2
                    if abnormal:price*=4
                    rows.append({'id':f'H-{offset:02d}-{unit[-2:]}-{business[:2]}-{i:02d}','date':period+f'-{i+1:02d}',
                        'unit':unit,'business_type':business,'item':'设备配件' if business=='采购付款' else '标准服务小时',
                        'supplier':f'示例供应商{i%3+1}','amount':round(quantity*price,2),'quantity':quantity,
                        'payment_days':round(rng.uniform(20,40),1),'label':abnormal})
    return validate_rows(rows)


def observations(rows, model):
    rows=[r for r in rows if model['business_type']=='全部' or r['business_type']==model['business_type']]
    if model['aggregation']=='transaction':return rows
    groups=defaultdict(list)
    for r in rows:groups[(r['date'][:7],*(r[k] for k in model['group_by']))].append(r)
    result=[]
    for key, group in sorted(groups.items()):
        labels=[r['label'] for r in group]
        result.append({**{k:group[0][k] for k in GROUPS},'id':'MONTH-'+hashlib.sha256(repr(key).encode()).hexdigest()[:16],
            'date':key[0]+'-01','amount':sum(r['amount'] for r in group),'quantity':sum(r['quantity'] for r in group),
            'label':True if True in labels else False if all(v is False for v in labels) else None,
            'source_ids':[r['id'] for r in group]})
    return result


def metrics(results, labels=None):
    labels=labels or {};evaluated=[r for r in results if r['status']!='insufficient']
    marked=[(r,labels.get(r['id'],r['row'].get('label'))) for r in evaluated]
    marked=[(r,v) for r,v in marked if type(v) is bool]
    tp=sum(r['anomaly'] and label for r,label in marked);fp=sum(r['anomaly'] and not label for r,label in marked)
    fn=sum(not r['anomaly'] and label for r,label in marked);tn=sum(not r['anomaly'] and not label for r,label in marked)
    return {'total':len(results),'evaluated':len(evaluated),'insufficient':len(results)-len(evaluated),
            'candidates':sum(r['anomaly'] for r in evaluated),'labeled':len(marked),'unlabeled':len(evaluated)-len(marked),
            'tp':tp,'fp':fp,'fn':fn,'tn':tn,'precision':tp/(tp+fp) if tp+fp else None,
            'recall':tp/(tp+fn) if tp+fn else None,'false_positive_rate':fp/(fp+tn) if fp+tn else None}


def evaluate_period(rows, model, period):
    month(period);obs=observations(rows,model);start=shift(period,-model['window_months'])
    history=[r for r in obs if start<=r['date'][:7]<period and (not model['seasonal'] or r['date'][5:7]==period[5:7])]
    targets=[r for r in obs if r['date'][:7]==period]
    groups=defaultdict(list);target_groups=defaultdict(list)
    for r in history:groups[tuple(r[k] for k in model['group_by'])].append(r)
    for r in targets:target_groups[tuple(r[k] for k in model['group_by'])].append(r)
    results=[]
    for group, current in sorted(target_groups.items()):
        train=groups[group];profiles={}
        for metric in model['metrics']:
            values=[r[metric] for r in train]
            if not values:continue
            med=median(values);mad=median(abs(v-med) for v in values);scale=max(1.4826*mad,abs(med)*.01,1e-6)
            low,high=(quantile(values,model['quantile_low']),quantile(values,model['quantile_high'])) if model['interval']=='quantile' else (med-model['sensitivity']*scale,med+model['sensitivity']*scale)
            profiles[metric]={'median':med,'mad':mad,'scale':scale,'low':max(0,low),'high':high}
        sufficient=len(train)>=model['min_samples'];forest=None;threshold=None;scores=[]
        if sufficient and model['algorithm']=='isolation':
            try:
                import sklearn
                from sklearn.ensemble import IsolationForest
            except ImportError:raise ValueError('请安装requirements.txt中的scikit-learn后运行多指标模型') from None
            matrix=lambda rs:[[ (r[k]-profiles[k]['median'])/profiles[k]['scale'] for k in model['metrics']] for r in rs]
            forest=IsolationForest(n_estimators=100,max_samples=min(256,len(train)),random_state=42,n_jobs=1)
            forest.fit(matrix(train));threshold=quantile(list(-forest.score_samples(matrix(train))),1-model['contamination'])
            scores=list(-forest.score_samples(matrix(current)))
        for i,row in enumerate(current):
            details=[{'metric':k,'name':METRICS[k],'value':row[k],**p,'deviation':abs(row[k]-p['median'])/p['scale']} for k,p in profiles.items()]
            score=float(scores[i]) if forest else max((v['deviation'] for v in details),default=0)
            anomaly=bool(sufficient and (score>threshold if forest else any(v['value']<v['low'] or v['value']>v['high'] for v in details)))
            comparisons={}
            if model['aggregation']=='monthly_sum':
                for title,delta in [('mom',-1),('yoy',-12)]:
                    previous=next((r['amount'] for r in obs if r['date'][:7]==shift(period,delta) and tuple(r[k] for k in model['group_by'])==group),None)
                    comparisons[title]={'reference':previous,'change':(row['amount']/previous-1) if previous is not None and previous!=0 else None}
            results.append({'id':row['id'],'row':row,'status':'candidate' if anomaly else 'normal' if sufficient else 'insufficient',
                'anomaly':anomaly,'score':score if sufficient else None,'threshold':float(threshold) if threshold is not None else None,
                'group':dict(zip(model['group_by'],group)),'baseline':{'start':start,'end':shift(period,-1),'count':len(train),'sample_ids':[r['id'] for r in train]},
                'details':details,'comparisons':comparisons,'explanation':f'同类历史样本{len(train)}条，少于要求{model["min_samples"]}条，未评估。' if not sufficient else
                ('联合异常得分超过历史训练分数分位阈值；下列单指标偏离供核查，不是模型因果归因。' if forest else '当前值与同类历史正常区间比较；异常仅作为横向统计线索。'),
                'algorithm_version':('scikit-learn '+sklearn.__version__) if forest else 'robust-baseline-v1'})
    return {'period':period,'results':sorted(results,key=lambda r:(r['anomaly'],r['score'] or 0),reverse=True),'metrics':metrics(results)}


def backtest(rows, model, start, end):
    month(start);month(end);require(start<=end and end<=shift(start,11),'回测范围须为连续1—12个月')
    periods=[];p=start
    while p<=end:
        periods.append(evaluate_period(rows,model,p));p=shift(p,1)
    return {'start':start,'end':end,'periods':periods,'metrics':metrics([r for p in periods for r in p['results']]),
            'method':'逐月向前回测；每月仅使用该月之前的历史数据，阈值不使用当月标签调优。'}
