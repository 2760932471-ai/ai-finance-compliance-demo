from pathlib import Path
import shutil,json,sys
r=Path(__file__).resolve().parents[1];sys.path.insert(0,str(r))
from compliance.store import Store
from compliance.domain import build_graph
s=Store(r.parent/'preview-seed.sqlite3');run=s.run();state=s.state()
import json
rules=json.loads((r/'app/rules.json').read_text())
d={'/api/bootstrap':{'token':'preview','offline':True,'model_available':False},'/api/projects':[],'/api/rules':rules,'/api/knowledge':[], '/api/evaluations':{'cases':[],'runs':[]},'/api/operations':{'projects':0,'runs':0,'counts':{},'active':0,'failed':0,'reviews':0,'average_seconds':0,'tokens':0,'risk':[],'feedback':[]},'/api/compliance/state':state,'/api/compliance/run':run}
from compliance.risklab import RiskLab
lab=RiskLab(s)
labruns={m['id']:lab.run({'model_id':m['id'],'start':'2026-10','end':'2026-10'}) for m in lab.state()['active']}
d['/api/compliance/lab/state']=lab.state()
d['/api/compliance/lab/data']=lab.state()['dataset']['rows']
out=r.parent/'finance-web-preview';shutil.copytree(r/'static',out,dirs_exist_ok=True)
js="const labRuns="+json.dumps(labruns,ensure_ascii=False)+";const previewData="+json.dumps(d,ensure_ascii=False)+";const previewGraph="+json.dumps(build_graph(state['dataset']['rows']),ensure_ascii=False)+";\n"+'''window.fetch=async function(url,options={}){let path=new URL(url,location.href).pathname;let body={};try{body=JSON.parse(options.body||'{}')}catch{}let value=previewData[path];if(path==='/api/compliance/lab/run'){const id=new URL(url,location.href).searchParams.get('id');value=Object.values(labRuns).find(r=>r.id===id)||labRuns.price;}let ok=true;if(options.method==='POST'){if(path==='/api/compliance/lab/run'){if(body.start!=='2026-10'||body.end!=='2026-10'){ok=false;value={error:'只读镜像预计算月份为2026-10，其他月份请使用本地完整版。'}}else value=labRuns[body.model_id];}else if(path==='/api/compliance/run')value=previewData[path];else if(path==='/api/compliance/tools/call'&&body.name==='graph')value=previewGraph;else{ok=false;value={error:'只读网页镜像：保存、上传与工具调用请使用本地完整版。'}}}if(!value){ok=false;value={error:'此功能请在本地完整版中使用'}}return {ok,json:async()=>structuredClone(value)};};
document.addEventListener('click',e=>{const a=e.target.closest('a');if(a&&a.getAttribute('href')?.startsWith('/api/')){e.preventDefault();alert('报告导出请使用本地完整版。')}});
'''
(out/'preview.js').write_text(js)
p=out/'index.html';html=p.read_text().replace('href="/','href="./').replace('src="/','src="./').replace('<body>','<body><div style="position:fixed;bottom:0;left:0;right:0;z-index:999;background:#172d4b;color:white;padding:10px;text-align:center">只读网页镜像 · 全部数据为虚构 · 上传、保存及真实AI审核请使用本地完整版</div><script src="./preview.js"></script>');p.write_text(html)
p=out/'compliance/module.js';p.write_text(p.read_text().replace('href="/compliance/style.css"','href="./compliance/style.css"'))
(out/'.nojekyll').touch()
