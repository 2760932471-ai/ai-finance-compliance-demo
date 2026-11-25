"""Local, scheme-scoped jobs for deriving candidate rules from a source file."""
import hashlib
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import quote, unquote

from . import model, rule_extraction, rule_sources, schemes, security, store

KIND = 'rule_extraction'
POOL = ThreadPoolExecutor(max_workers=1, thread_name_prefix='rule-extraction')
SUBMISSION = threading.Lock()
ROUTE = re.compile(r'/api/schemes/([A-Za-z0-9_-]+)/extractions(?:/([a-f0-9]{16})(?:/(adopt|original))?)?')
SUMMARY_KEYS = ('id','scheme_id','filename','sha256','created_at','updated_at','status','stage','error','demo')


def update(job, **changes):
    job.update(changes, updated_at=store.now())
    store.put(KIND, job)


def record(scheme_id, job_id):
    job = store.get(KIND, job_id)
    if job['scheme_id'] != scheme_id:
        raise KeyError('规则来源不在此方案内')
    return job


def original_bytes(job):
    directory = store.DATA/'rule-sources'
    path = security.local_file(directory/job['id']/'original',directory)
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != job['sha256']:
        raise ValueError('来源文件已变化，请重新上传以核实引文。')
    return raw


def run_job(job_id, scheme):
    job = store.get(KIND,job_id)
    try:
        update(job, status='processing', stage='正在读取制度原文')
        folder = store.DATA/'rule-sources'/job_id
        source = rule_sources.read_source(job['filename'],original_bytes(job),folder)
        update(job, warnings=source['warnings'], stage='正在提炼候选审核规则')
        def progress(done,total,message):
            update(job, stage=message, progress={'done':done,'total':total})
        result = rule_extraction.extract(source['segments'],scheme,on_progress=progress)
        result['warnings'] = source['warnings'] + result['warnings']
        stage = '提炼完成' if result['candidates'] else '未取得可采纳的规则'
        update(job, **result, status='completed', stage=stage)
    except (ValueError,RuntimeError) as exc:
        update(job, status='failed', stage='提炼未完成', error=security.parameter_error(exc))
    except Exception:
        update(job, status='failed', stage='提炼未完成', error='文件读取或规则提炼失败，请核对原件并重试。')


def submit(scheme_id,filename,raw):
    scheme = schemes.detail(scheme_id)['draft']
    if not filename or len(filename)>200 or any(ord(c)<32 or c in '/\\:' for c in filename):
        raise ValueError('文件名无效，请重新选择制度文件')
    if Path(filename).suffix.lower() not in rule_sources.SUPPORTED:
        raise ValueError('不支持此类型的制度文件')
    if not raw or len(raw)>rule_sources.MAX_BYTES:
        raise ValueError('文件为空或超过 12 MiB')
    digest = hashlib.sha256(raw).hexdigest()
    with SUBMISSION:
        active = [j for j in store.all_items(KIND) if j['status'] in ('queued','processing')]
        for job in active:
            if job['scheme_id']==scheme_id and job['sha256']==digest and job['filename']==filename:
                return job
        if len(active)>=4:
            raise ValueError('已有 4 个提炼任务在处理中，请稍后再提交')
        job = {'id':store.uid(),'scheme_id':scheme_id,'filename':filename,'sha256':digest,
               'created_at':store.now(),'candidates':[],'ambiguities':[],'warnings':[],'error':''}
        folder = store.DATA/'rule-sources'/job['id']
        folder.mkdir(parents=True)
        (folder/'original').write_bytes(raw)
        update(job,status='queued',stage='等待提炼')
        try:
            POOL.submit(run_job,job['id'],scheme)
        except RuntimeError:
            update(job,status='failed',stage='任务未启动',error='本机服务正在关闭，请重新启动后提交。')
        return job


def recover():
    for job in store.all_items(KIND):
        if job['status'] in ('queued','processing'):
            update(job,status='failed',stage='上次提炼中断',error='请重新上传原文件以继续提炼。')


def handle(handler,match):
    scheme_id, job_id, action = match.groups()
    if handler.command=='GET':
        if job_id is None:
            schemes.detail(scheme_id)
            jobs = store.related(KIND,'scheme_id',scheme_id)
            return handler.send([{key:j[key] for key in SUMMARY_KEYS if key in j} for j in jobs])
        job = record(scheme_id,job_id)
        if action=='original':
            disposition = "attachment; filename*=UTF-8''"+quote(job['filename'],safe='')
            return handler.send(original_bytes(job),kind='application/octet-stream',
                                extra={'Content-Disposition':disposition})
        if action is None:
            return handler.send(job)
    elif job_id is None:
        if not model.available():
            return handler.send({'error':'AI 提炼需要已启用的模型，请使用 AI 模式启动并在审核能力页测试连接。'},503)
        name = unquote(handler.headers.get('X-Filename',''))
        job = submit(scheme_id,name,handler.body(rule_sources.MAX_BYTES))
        return handler.send(job,202)
    elif action=='adopt':
        return handler.send(schemes.adopt_extracted(scheme_id,job_id,handler.jsonbody()))
    raise FileNotFoundError()
