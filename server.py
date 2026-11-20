"""Local single-user audit application. Python stdlib server; loopback only."""
import base64,concurrent.futures,hashlib,io,json,mimetypes,os,re,secrets,threading,zipfile
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse,unquote,parse_qs
from compliance import api as compliance_api
from compliance.store import Conflict, Store as ComplianceStore
from app import knowledge,store,engine,materials,model,report,jev,harness,evaluations,decisions,checks,security

ROOT=Path(__file__).resolve().parent
if (ROOT/'.env').exists():
    for line in (ROOT/'.env').read_text(encoding='utf-8-sig').splitlines():
        if '=' in line and not line.lstrip().startswith('#'):
            key,val=line.split('=',1)
            if key.strip() in ('AUDIT_AI','AUDIT_CODEX_PATH','PORT','MODEL_TIMEOUT','AUDIT_CACHE','AUDIT_JEV','TYPESAFE_API_KEY','JEV_MODEL','JEV_CONFIDENCE','AUDIT_VISUAL_OCR','AUDIT_DECISION_PROVIDER','AUDIT_DECISION_MODE','DECISION_CONFIDENCE','STARTLUX_ENDPOINT','STARTLUX_MODEL','STARTLUX_API_KEY','STARTLUX_IMAGES','STARTLUX_REVISION','DECISION_TIMEOUT','DECISION_SCOPE','AUDIT_PDF_FONT'):os.environ.setdefault(key.strip(),val.strip().strip('"'))
TOKEN=secrets.token_urlsafe(32)
POOL=concurrent.futures.ThreadPoolExecutor(max_workers=1)
RUN_LOCK=threading.RLock()

def public_run(run,full=True):
    r=json.loads(json.dumps(run));r.pop('source',None);r['summary']=engine.summary(run)
    r['reviews']=store.related('review','run_id',run['id'])
    r['reports']= [report.public_record(x) for x in store.related('report','run_id',run['id'])]
    if not full:
        return {k:r.get(k) for k in ('id','project_id','status','phase','progress','created_at','updated_at','summary','duration','error','confirmed_at','parent_run')}
    r['results']=engine.effective_results(run)
    if run.get('evidence'):r['technical_checks']=checks.snapshot(run)
    for d in r.get('documents',[]):d.pop('path',None)
    for e in r.get('evidence',[]):e['has_image']=bool(e.pop('image',None));e.pop('cache_key',None)
    for m in r.get('manifest',[]):m.pop('text',None)
    r['chats']=store.related('chat','run_id',run['id'])
    return r

def projects():
    runs=store.all_items('run');out=[]
    for p in store.all_items('project'):
        pr=sorted([r for r in runs if r['project_id']==p['id']],key=lambda x:x['created_at'],reverse=True)
        out.append({**p,'runs':[public_run(r,False) for r in pr],'latest':public_run(pr[0],False) if pr else None})
    return out

def create_upload(raw,name,project_id='',parent_run='',corrections=None):
    if len(raw)>materials.MAX_UPLOAD:raise ValueError('文件超过160MB限制')
    if not zipfile.is_zipfile(io.BytesIO(raw)):raise ValueError('请上传有效的ZIP压缩包')
    if sum(r.get('status') in ('queued','processing') for r in store.all_items('run'))>=4:
        raise ValueError('待处理任务已达上限，请等待现有任务完成')
    name=str(name).strip()[:100]
    if not project_id:
        p=store.put('project',{'id':store.uid(),'name':name or '新建费用审核','created_at':store.now(),'archived':False});project_id=p['id']
    else:store.get('project',project_id)
    rid=store.uid();folder=store.DATA/'runs'/rid;folder.mkdir(parents=True)
    source=folder/'source.zip';source.write_bytes(raw)
    run={'id':rid,'project_id':project_id,'created_at':store.now(),'source':str(source),'source_hash':hashlib.sha256(raw).hexdigest(),'status':'queued','phase':'等待处理','progress':0,'events':[],'parent_run':parent_run,'rules':engine.RULES,'corrections':corrections or []}
    store.put('run',run);POOL.submit(engine.process,rid);return run

def seed():
    if not store.all_items('knowledge'):
        store.put('knowledge',{'id':'official-t1','title':'T1活动类审核标准 · 比赛原文','source':'2026年10月解决方案团队AI内部实战通知','version':'T1-2026.10-v1','status':'published','locked':True,'text':'\n'.join(r['id']+' '+r['name']+'：'+r['principle'] for r in engine.RULES),'created_at':store.now()})
    if os.getenv('AUDIT_AI')=='off' and not any(k['id']=='demo-procurement-policy' for k in store.all_items('knowledge')):
        guide=json.loads((ROOT/'examples/procurement-knowledge.json').read_text(encoding='utf-8'));guide['created_at']=store.now();store.put('knowledge',guide)
    for r in store.all_items('run'):
        if r['status'] in ('processing','queued'):
            r.update(status='interrupted',error='上次运行被中断，原包和阶段记录已保留；可重试');store.put('run',r)

class Handler(BaseHTTPRequestHandler):
    server_version='FinancialReview/0.7'
    sys_version=''
    def setup(self):
        super().setup()
        self.connection.settimeout(30)
    def log_message(self,*args):pass
    def send(self,data,status=200,kind='application/json; charset=utf-8',extra=None):
        self.response_status=status
        if (self.command=='POST' or status>=400) and not getattr(self,'event_written',False):
            self.event_written=True
            security.event('write' if self.command=='POST' else 'read_rejected',urlparse(self.path).path,status)
        if not isinstance(data,bytes):data=json.dumps(data,ensure_ascii=False).encode('utf-8') if kind.startswith('application/json') else str(data).encode('utf-8')
        self.send_response(status);self.send_header('Content-Type',kind);self.send_header('Content-Length',str(len(data)))
        self.send_header('X-Content-Type-Options','nosniff');self.send_header('Cache-Control','no-store')
        self.send_header('X-Frame-Options','DENY');self.send_header('Referrer-Policy','no-referrer')
        self.send_header('Cross-Origin-Resource-Policy','same-origin')
        csp="default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; frame-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
        if urlparse(self.path).path=='/demo.html' and status==200 and kind.startswith('text/html'):
            # Trust only the exact bundled presentation script, not arbitrary inline scripts.
            hashes=["'sha256-"+base64.b64encode(hashlib.sha256(script).digest()).decode()+"'" for script in re.findall(rb'<script>(.*?)</script>',data,re.S)]
            csp=csp.replace("script-src 'self'", "script-src 'self' "+' '.join(hashes))+"; font-src 'self' data:"
        self.send_header('Content-Security-Policy',csp)
        for k,v in (extra or {}).items():self.send_header(k,v)
        self.end_headers();self.wfile.write(data)
    def body(self,limit):
        if self.headers.get('Transfer-Encoding'):raise ValueError('不支持分块请求传输')
        lengths=self.headers.get_all('Content-Length',[])
        if len(lengths)!=1 or not lengths[0].isdigit():raise ValueError('请求长度无效')
        n=int(lengths[0])
        if not 0<n<=limit:raise ValueError('请求内容大小无效')
        payload=self.rfile.read(n)
        if len(payload)!=n:raise ValueError('请求内容不完整')
        return payload
    def jsonbody(self):
        if self.headers.get_content_type()!='application/json':raise ValueError('请求必须使用application/json')
        try:body=json.loads(self.body(2*1024*1024), parse_constant=lambda value: (_ for _ in ()).throw(ValueError('JSON 不允许非有限数值')))
        except (json.JSONDecodeError,UnicodeDecodeError):raise ValueError('JSON格式无效')
        if not isinstance(body,dict):raise ValueError('JSON请求必须为对象')
        return body
    def gate(self,write=False):
        security.request_origin(self.headers,self.server.server_port)
        if write and not secrets.compare_digest(self.headers.get('X-Session-Token',''),TOKEN):raise PermissionError('会话已过期，请刷新页面')
    def dispatch(self,write=False):
        self.response_status=500
        try:
            self.gate(write)
            if write:self.write()
            else:self.read()
        except (KeyError,FileNotFoundError):self.send({'error':'未找到记录'},404)
        except PermissionError as e:self.send({'error':security.parameter_error(e)},403)
        except Conflict as e:self.send({'error':str(e)},409)
        except report.IntegrityError as e:self.send({'error':str(e)},409)
        except ValueError as e:self.send({'error':security.parameter_error(e)},400)
        except (BrokenPipeError,ConnectionResetError,TimeoutError):self.response_status=408
        except Exception:
            self.send({'error':'处理未完成，请检查输入或重试；已有材料和历史结果已保留'},500)
        finally:
            if (write or self.response_status>=400) and not getattr(self,'event_written',False):
                security.event('write' if write else 'read_rejected',urlparse(self.path).path,self.response_status)
    def do_GET(self):self.dispatch()
    def read(self):
        u=urlparse(self.path);p=u.path;q=parse_qs(u.query)
        if p=='/health':return self.send({'status':'ok','version':'0.7','mode':'integrated-local'})
        if p.startswith('/api/compliance/'):
            return compliance_api.read(self,p[len('/api/compliance'):],q)
        if p=='/api/bootstrap':return self.send({'app_version':'0.7','offline':os.getenv('AUDIT_AI','codex')=='off','token':TOKEN,'model_available':model.available(),'model_diagnostic':model.diagnostic(),'engine':'Codex · 当前登录','rules_version':engine.RULES[0]['version'],'harness':harness.VERSION,'jev':jev.status(),'decision':decisions.status()})
        if p=='/api/model/diagnostic':return self.send(model.diagnostic())
        if p=='/api/knowledge/chunks':
            doc=store.get('knowledge',q.get('id',[''])[0]);return self.send({'document':doc,'chunks':knowledge.chunks(doc)})
        if p=='/api/projects':return self.send(projects())
        if p=='/api/rules':return self.send(engine.RULES)
        if p=='/api/knowledge':return self.send(store.all_items('knowledge'))
        if p=='/api/feedback':return self.send(store.all_items('feedback'))
        if p=='/api/evaluations':return self.send({'cases':store.all_items('eval_case'),'runs':store.all_items('evaluation')})
        if p=='/api/operations':
            ps=projects();runs=store.all_items('run');latest=[store.get('run',p['latest']['id']) for p in ps if p['latest']];risk={};counts={s:0 for s in engine.STATES}
            for r in latest:
                counts[engine.summary(r)['verdict']]+=1
                for x in engine.effective_results(r):
                    if x['verdict']!='通过':
                        k=x['id'];risk.setdefault(k,{'id':k,'name':x['name'],'fail':0,'manual':0,'runs':[]})
                        risk[k]['fail' if x['verdict']=='不通过' else 'manual']+=1;risk[k]['runs'].append(r['id'])
            return self.send({'projects':len(ps),'runs':len(runs),'counts':counts,'active':sum(r['status'] in ('processing','queued') for r in runs),'failed':sum(r['status'] in ('failed','needs_attention','interrupted') for r in runs),'reviews':len(store.all_items('review')),'average_seconds':round(sum(r.get('duration',0) for r in runs)/max(1,sum('duration'in r for r in runs)),1),'tokens':sum(u.get('usage',{}).get('input_tokens',0)+u.get('usage',{}).get('output_tokens',0) for r in runs for u in r.get('usage',[])),'risk':sorted(risk.values(),key=lambda x:x['fail']*2+x['manual'],reverse=True),'feedback':store.all_items('feedback')})
        m=re.fullmatch(r'/api/runs/([a-f0-9]+)',p)
        if m:return self.send(public_run(store.get('run',m[1])))
        m=re.fullmatch(r'/api/runs/([a-f0-9]+)/evidence/([A-Z0-9-]+)/image',p)
        if m:
            r=store.get('run',m[1]);e=next((x for x in r['evidence'] if x['id']==m[2]),None)
            if not e or not e.get('image'):raise FileNotFoundError()
            return self.send(security.local_file(e['image'],store.DATA/'runs'/r['id']).read_bytes(),kind='image/jpeg')
        m=re.fullmatch(r'/api/runs/([a-f0-9]+)/documents/([A-Z0-9]+)/original',p)
        if m:
            r=store.get('run',m[1]);d=next((x for x in r['documents'] if x['id']==m[2]),None)
            if not d:raise FileNotFoundError()
            suffix=Path(d['path']).suffix.lower()
            if suffix not in ('.pdf','.jpg','.jpeg','.png','.html','.htm'):raise PermissionError('不允许下载该材料类型')
            return self.send(security.local_file(d['path'],store.DATA/'runs'/r['id']/'files').read_bytes(),kind='application/octet-stream',extra={'Content-Disposition':f'attachment; filename="{d["id"]}{suffix}"'})
        m=re.fullmatch(r'/api/reports/([a-f0-9]+)',p)
        if m:
            x=store.get('report',m[1]);payload,kind,fmt=report.download(x,q.get('format',[None])[0])
            disposition='inline' if fmt=='html' and q.get('preview')==['1'] else 'attachment'
            return self.send(payload,kind=kind,extra={'Content-Disposition':f'{disposition}; filename="audit-{x["run_id"]}-{x["id"]}.{fmt}"'})
        if p=='/api/knowledge/search':
            return self.send(knowledge.retrieve(q.get('q',[''])[0])['results'])
        if p.startswith('/api/'):raise FileNotFoundError()
        static=ROOT/'static';path=static/('index.html' if p=='/' else p.lstrip('/'))
        if not path.resolve().is_relative_to(static.resolve()) or not path.is_file():raise FileNotFoundError()
        return self.send(path.read_bytes(),kind=(mimetypes.guess_type(path.name)[0] or 'application/octet-stream')+'; charset=utf-8')
    def do_POST(self):self.dispatch(True)
    def write(self):
        p=urlparse(self.path).path
        if p=='/api/upload':
            raw=self.body(materials.MAX_UPLOAD);name=unquote(self.headers.get('X-Project-Name',''));pid=self.headers.get('X-Project-Id','')
            with RUN_LOCK:return self.send(public_run(create_upload(raw,name,pid),False),201)
        m=re.fullmatch(r'/api/runs/([a-f0-9]+)/supplement',p)
        if m:
            prior=store.get('run',m[1]);n=int(self.headers.get('Content-Length','0'));filename=unquote(self.headers.get('X-Filename',''))
            if not prior.get('manifest') or not prior.get('documents') or prior['status'] in ('processing','queued','failed'):raise ValueError('请先完成原材料治理，再补充材料')
            if n<=0 or n>materials.MAX_UPLOAD or Path(filename).suffix.lower() not in ('.pdf','.jpg','.jpeg','.png','.html','.htm'):raise ValueError('补证支持PDF、JPG、PNG、HTML，单文件不超过160MB')
            raw=self.body(materials.MAX_UPLOAD);folder=store.DATA/'supplements';folder.mkdir(exist_ok=True);zpath=folder/(store.uid()+'.zip')
            with zipfile.ZipFile(zpath,'w',zipfile.ZIP_DEFLATED) as z:
                with zipfile.ZipFile(prior['source']) as previous:
                    for info in previous.infolist():
                        if info.is_dir():continue
                        # Preserve original resources and paths; next run performs safe governance again.
                        z.writestr(info,previous.read(info))
                z.writestr('补充材料/'+store.uid()+'_'+Path(filename).name,raw)
            return self.send(public_run(create_upload(zpath.read_bytes(),'',prior['project_id'],prior['id'],prior.get('corrections',[])),False),201)
        body=self.jsonbody()
        if p.startswith('/api/compliance/'):
            try:return compliance_api.write(self,p[len('/api/compliance'):],body)
            except (KeyError,TypeError,AttributeError):raise ValueError('请求结构错误，请检查字段与 JSON 格式') from None
        if p=='/api/evaluations':return self.send(evaluations.evaluate())
        m=re.fullmatch(r'/api/runs/([a-f0-9]+)/(review|confirm|retry|report|chat|correct)',p)
        if m:
            with RUN_LOCK:
                run=store.get('run',m[1]);action=m[2]
                if action=='retry':return self.send(public_run(create_upload(Path(run['source']).read_bytes(),'',run['project_id'],run['id'],run.get('corrections',[])),False),201)
                if run['status'] in ('processing','queued'):raise ValueError('请等待当前审核处理结束')
                if action=='correct':
                    e=next((e for e in run.get('evidence',[]) if e['id']==body.get('evidence_id')),None)
                    if not e:raise ValueError('证据不存在')
                    text=body.get('text','').strip();actor=body.get('actor','').strip();reason=body.get('reason','').strip()
                    if not text or len(text)>30000 or not actor or len(reason)<5:raise ValueError('请填写识别文字、校正人和至少5字的校正原因')
                    correction={'id':store.uid(),'evidence_id':e['id'],'sha256':e['sha256'],'page':e['page'],'text':text,'original_text':e.get('original_text',e['text']),'actor':actor,'reason':reason,'time':store.now(),'source_run':run['id']}
                    corrections=[c for c in run.get('corrections',[]) if c['evidence_id']!=e['id']]+[correction]
                    return self.send(public_run(create_upload(Path(run['source']).read_bytes(),'',run['project_id'],run['id'],corrections),False),201)
                if action=='review':
                    rid=body.get('rule_id');old=next((r for r in engine.effective_results(run) if r['id']==rid),None)
                    if not old or body.get('verdict') not in engine.STATES:raise ValueError('审核条目或结论无效')
                    reason=body.get('reason','').strip();actor=body.get('actor','').strip()
                    if len(reason)<5 or not actor:raise ValueError('请填写复核人和至少5字的依据说明')
                    refs=body.get('evidence_ids',old.get('evidence_ids',[]));valid={e['id'] for e in run.get('evidence',[])}
                    if any(x not in valid for x in refs):raise ValueError('复核证据不存在')
                    if body['verdict']=='通过' and not refs:raise ValueError('通过结论必须引用实际证据')
                    rev=store.put('review',{'id':store.uid(),'run_id':run['id'],'rule_id':rid,'before':old['verdict'],'after':body['verdict'],'reason':reason,'actor':actor,'time':store.now(),'evidence_ids':refs})
                    run.pop('confirmed_at',None);store.put('run',run)
                    store.put('feedback',{'id':store.uid(),'run_id':run['id'],'rule_id':rid,'kind':'人工复核','body':reason,'status':'待核实','created_at':store.now()})
                    return self.send(rev)
                if action=='confirm':
                    if not engine.summary(run)['complete']:raise ValueError('尚有未完成技术项，请重试或逐项复核后确认')
                    if not body.get('actor','').strip():raise ValueError('请填写确认人')
                    run.update(confirmed_at=store.now(),confirmed_by=body['actor']);store.put('run',run);return self.send(public_run(run))
                if action=='report':return self.send(report.public_record(report.generate(run,body.get('format','pdf'))))
            if action=='chat':
                question=body.get('question','').strip()
                if not question or len(question)>3000:raise ValueError('问题长度需为1至3000字')
                return self.send(engine.chat(run,question,body.get('rule_id','')))
        if p=='/api/projects/update':
            x=store.get('project',body['id']);x['name']=body.get('name',x['name'])[:100];x['archived']=bool(body.get('archived',x['archived']));return self.send(store.put('project',x))
        if p=='/api/model/diagnostic':return self.send(model.diagnostic(check_login=True))
        if p=='/api/knowledge/recall':return self.send(knowledge.retrieve(body.get('query'),body.get('top_k',6),body.get('method','bm25')))
        if p=='/api/knowledge/chunks':return self.send(knowledge.resegment(body['id'],body.get('config'),body.get('revision',1)))
        if p=='/api/knowledge':
            title=body.get('title','').strip();text=body.get('text','').strip()
            if not title or not text:raise ValueError('请填写标题和知识正文')
            if len(text)>100000:raise ValueError('知识正文过长，请按条款拆分')
            return self.send(store.put('knowledge',{'id':store.uid(),'title':title[:150],'text':text,'source':body.get('source','用户录入'),'status':'draft','version':'v1','created_at':store.now()}),201)
        if p=='/api/knowledge/status':
            x=store.get('knowledge',body['id'])
            if x.get('locked'):raise ValueError('比赛规则原文已锁定，不允许停用')
            if body.get('status') not in ('published','draft','disabled'):raise ValueError('状态无效')
            x['status']=body['status'];x['updated_at']=store.now();return self.send(store.put('knowledge',x))
        if p=='/api/feedback':
            if not body.get('body','').strip():raise ValueError('请填写反馈说明')
            return self.send(store.put('feedback',{'id':store.uid(),'run_id':body.get('run_id',''),'rule_id':body.get('rule_id',''),'kind':body.get('kind','改进建议'),'body':body['body'],'status':'待核实','created_at':store.now()}),201)
        if p=='/api/feedback/status':
            x=store.get('feedback',body['id']);status=body.get('status')
            if status not in ('待核实','已核实','已纳入回归','已关闭'):raise ValueError('状态无效')
            if status=='已纳入回归':x['eval_case_id']=evaluations.add_case(x,body.get('expected',''),body.get('actor',''),body.get('reason',''))['id']
            x['status']=status;x['updated_at']=store.now();return self.send(store.put('feedback',x))
        raise ValueError('未知操作')

def create_server(port=8765, database=None):
    seed()
    httpd=ThreadingHTTPServer(('127.0.0.1',port),Handler)
    httpd.compliance_store=ComplianceStore(database or store.DB)
    return httpd

if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description='AI财务审核 · 业财智能合规集成版')
    parser.add_argument('--port',type=int,default=int(os.getenv('PORT','8765')))
    args=parser.parse_args()
    server=create_server(args.port)
    print(f'AI Finance Audit v0.7: http://127.0.0.1:{server.server_port}',flush=True)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:server.server_close()
