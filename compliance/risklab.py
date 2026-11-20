"""Versioned statistical models and immutable evaluation snapshots."""
import hashlib
import json
import uuid
from . import analytics as a
from .store import Store, Conflict, now, encode

class RiskLab:
    def __init__(self, store):
        self.store=store
        with store.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS lab_runs (id TEXT PRIMARY KEY, created TEXT NOT NULL, body TEXT NOT NULL)')
            db.execute('BEGIN IMMEDIATE')
            if not db.execute("SELECT 1 FROM state WHERE key='lab'").fetchone():
                models=a.templates()
                store.put(db,'lab',{'revision':1,'draft':models,'active':models,'version':1,
                    'versions':[{'version':1,'created':now(),'note':'内置统计模型模板','models':models}],
                    'dataset':{'revision':1,'source':'虚构历史演示数据（合成标签，仅用于演示）','rows':a.demo_rows()}})
    def state(self):
        with self.store.connect() as db:
            value=self.store.get(db,'lab')
            value['runs']=[dict(r) for r in db.execute('SELECT id,created FROM lab_runs ORDER BY rowid DESC LIMIT 30')]
            return value
    def mutate(self,action,data):
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE');s=self.store.get(db,'lab')
            if data.get('revision')!=s['revision']:raise Conflict('模型或数据已更新，请刷新后重试')
            if action=='save':s['draft']=a.validate_models(data.get('models'))
            elif action=='publish':
                a.require(isinstance(data.get('note'),str) and 0<len(data['note'].strip())<=500,'请填写发布说明')
                models=a.validate_models(s['draft']);a.require(models!=s['active'],'草稿与已发布版本相同')
                s['active']=models;s['version']+=1
                s['versions'].append({'version':s['version'],'created':now(),'note':data['note'],'models':models})
            elif action=='import':
                a.require(isinstance(data.get('source'),str) and 0<len(data['source'].strip())<=160,'请填写数据来源')
                s['dataset']={'revision':s['dataset']['revision']+1,'source':data['source'],'rows':a.validate_rows(data.get('rows'))}
            else:raise FileNotFoundError()
            s['revision']+=1;self.store.put(db,'lab',s);self.store.audit(db,'统计模型 '+action,f'版本{s["version"]} / 修订{s["revision"]}')
        return self.state()
    def run(self,data):
        s=self.state();model=next((m for m in s['active'] if m['id']==data.get('model_id')),None)
        a.require(model is not None and model['enabled'],'请选择已启用的发布模型')
        result=a.backtest(s['dataset']['rows'],model,data.get('start'),data.get('end'))
        run={'id':uuid.uuid4().hex[:16],'created':now(),'model_version':s['version'],'model':model,
             'dataset':s['dataset'],'dataset_sha256':hashlib.sha256(encode(s['dataset']).encode()).hexdigest(),
             'result':result,'labels':{},'label_history':[]}
        with self.store.connect() as db:
            db.execute('INSERT INTO lab_runs VALUES(?,?,?)',(run['id'],run['created'],encode(run)))
        return run
    def get_run(self,id):
        with self.store.connect() as db:r=db.execute('SELECT body FROM lab_runs WHERE id=?',(id,)).fetchone()
        if not r:raise FileNotFoundError()
        run=json.loads(r[0]);rows=[r for p in run['result']['periods'] for r in p['results']]
        run['reviewed_metrics']=a.metrics(rows,run['labels'])
        return run
    def label(self,data):
        a.require(type(data.get('label')) is bool,'核实结论须为true或false')
        for key in ('actor','note'):a.require(isinstance(data.get(key),str) and 0<len(data[key].strip())<=500,'请填写核实人及依据')
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE');r=db.execute('SELECT body FROM lab_runs WHERE id=?',(data.get('run_id'),)).fetchone()
            if not r:raise FileNotFoundError()
            run=json.loads(r[0]);ids={r['id'] for p in run['result']['periods'] for r in p['results']}
            a.require(data.get('result_id') in ids,'样本不存在')
            run['labels'][data['result_id']]=data['label']
            run['label_history'].append({k:data[k] for k in ('result_id','label','actor','note')}|{'created':now()})
            db.execute('UPDATE lab_runs SET body=? WHERE id=?',(encode(run),run['id']))
        return self.get_run(run['id'])
