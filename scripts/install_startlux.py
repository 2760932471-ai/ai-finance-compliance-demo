"""Download pinned official CPU inference assets; no credentials or business data sent."""
import hashlib,json,shutil,urllib.request,urllib.parse,zipfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
DEST=ROOT/'data'/'startlux'
MODEL='StartLux-Decision-0.8B-Q8_0-GGUF'
MODEL_REV='c38d5037dc5250bff5569f4f2c3964eae9b19c91'
SOURCE_REV='0e7a2e81b9c92756e26d8edd843a44d50e362669'
LLAMA='b11514'
LLAMA_SHA='5a1d6ba1b414ab0234b12e29d911b3b8fe63051b4455215e184cf74870eac596'

def download(url,path,sha=None):
    path.parent.mkdir(parents=True,exist_ok=True)
    if path.exists() and sha and hashlib.file_digest(path.open('rb'),'sha256').hexdigest()==sha:return
    tmp=path.with_suffix(path.suffix+'.part');h=hashlib.sha256();n=0;next_log=64*1024*1024
    print('Downloading',path.name,flush=True)
    with urllib.request.urlopen(urllib.request.Request(url,headers={'User-Agent':'FinancialAudit-LocalDecision/1.0'}),timeout=90) as r,tmp.open('wb') as f:
        while block:=r.read(1024*1024):
            f.write(block);h.update(block);n+=len(block)
            if n>=next_log:print(path.name,round(n/1048576),'MiB',flush=True);next_log+=64*1024*1024
    if sha and h.hexdigest()!=sha:raise ValueError('SHA-256 mismatch: '+path.name)
    tmp.replace(path)

def main():
    DEST.mkdir(parents=True,exist_ok=True)
    if shutil.disk_usage(DEST).free<4*1024**3:raise RuntimeError('At least 4GiB free disk required')
    endpoint=f'https://modelscope.cn/api/v1/models/StartLuxAI/{MODEL}/repo/files?Revision={MODEL_REV}&Recursive=true'
    with urllib.request.urlopen(endpoint,timeout=30) as r:meta=json.load(r)
    wanted={'config.json','decision_config.json','tokenizer.json','tokenizer_config.json','vocab.json','merges.txt','LICENSE','NOTICE','StartLux-Decision-0.8B-Q8_0.gguf'}
    rows=[f for f in meta['Data']['Files'] if f['Path'] in wanted]
    if {r['Path'] for r in rows}!=wanted:raise ValueError('Official model manifest incomplete')
    for f in rows:
        url=f'https://modelscope.cn/api/v1/models/StartLuxAI/{MODEL}/repo?Revision={MODEL_REV}&FilePath='+urllib.parse.quote(f['Path'])
        download(url,DEST/MODEL/f['Path'],f['Sha256'])
    source=DEST/'source'
    for name in ('__init__.py','gguf_server.py','model.py','jevfmt.py'):
        download(f'https://raw.githubusercontent.com/StartLuxLabs/StartLux-Decision/{SOURCE_REV}/startlux_decision/{name}',source/'startlux_decision'/name)
    download(f'https://github.com/ggml-org/llama.cpp/releases/download/{LLAMA}/llama-{LLAMA}-bin-win-cpu-x64.zip',DEST/'llama.zip',LLAMA_SHA)
    target=(DEST/'llama').resolve();target.mkdir(exist_ok=True)
    with zipfile.ZipFile(DEST/'llama.zip') as z:
        for info in z.infolist():
            if not (target/info.filename).resolve().is_relative_to(target):raise ValueError('Unsafe archive path')
        z.extractall(target)
    lock={'model':MODEL,'model_revision':MODEL_REV,'source_revision':SOURCE_REV,'llama':LLAMA,'llama_sha256':LLAMA_SHA,'files':[{k:r[k] for k in ('Path','Size','Sha256')} for r in rows]}
    (DEST/'deployment.json').write_text(json.dumps(lock,indent=2),encoding='utf-8')
    print('Verified assets ready:',DEST,flush=True)

if __name__=='__main__':main()
