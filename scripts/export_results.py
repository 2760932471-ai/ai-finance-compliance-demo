"""Export actual persisted run artifacts, never fabricate examination outputs."""
import argparse,csv,json,shutil,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from app import store,engine,report,checks

def export(run_id,label):
    if any(x in label for x in '/\\:') or label in ('.','..'):raise ValueError('Invalid label')
    run=store.get('run',run_id)
    if run['status'] in ('queued','processing'):raise ValueError('Wait until processing finishes')
    dest=ROOT/'deliverables'/label;dest.mkdir(parents=True,exist_ok=True)
    def write(name,data): (dest/name).write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
    manifest=[{k:v for k,v in m.items() if k!='text'} for m in run.get('manifest',[])]
    write('文件清单.json',manifest)
    with (dest/'文件清单.csv').open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=['name','status','size','sha256','document_id'],extrasaction='ignore');w.writeheader();w.writerows(manifest)
    evidence=[{k:v for k,v in e.items() if k not in ('image','cache_key')} for e in run.get('evidence',[])]
    write('识别结果.json',evidence);write('AI原始审核结果.json',run.get('decision_input_results',run.get('results',[])));write('当前审核结果.json',engine.effective_results(run))
    reviews=store.related('review','run_id',run_id);write('复核记录.json',{'status':'已有复核记录' if reviews else '待人工复核','records':reviews,'confirmed_at':run.get('confirmed_at')})
    write('AI问答记录.json',store.related('chat','run_id',run_id))
    write('本地程序核验.json',checks.snapshot(run))
    write('执行记录.json',{'run_id':run_id,'source_hash':run.get('source_hash'),'rules':run.get('rule_version'),'quality':run.get('quality'),'traces':run.get('traces'),'usage':run.get('usage'),'jev':run.get('jev'),'decision_review':run.get('decision_review'),'recognition_version':run.get('recognition_version'),'recognition_usage':run.get('recognition_usage',[]),'summary':engine.summary(run)})
    generated=report.generate(run)
    for fmt,meta in generated['formats'].items():
        shutil.copy2(meta['path'],dest/('报告快照.json' if fmt=='json' else '审核报告.'+fmt))
    write('报告文件校验.json',report.public_record(generated))
    return {'path':str(dest),'run_id':run_id,'summary':engine.summary(run),'report_id':generated['id']}

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('run_id');p.add_argument('--label',required=True);a=p.parse_args();print(json.dumps(export(a.run_id,a.label),ensure_ascii=False))
