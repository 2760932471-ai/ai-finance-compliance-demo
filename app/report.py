"""Standalone, source-linked report. Machine transcription and raw OCR stay distinct."""
import base64,hashlib,html,json,tempfile
from pathlib import Path
from . import store,report_snapshot,report_pdf,security

def esc(s):return html.escape(str(s) if s is not None else '')
def status_class(s):return {'通过':'pass','不通过':'fail','待人工判断':'manual'}.get(s,'neutral')
def badge(s):return f'<span class="badge {status_class(s)}">{esc(s)}</span>'
def json_block(value):return '<pre class="json">'+esc(json.dumps(value,ensure_ascii=False,indent=2))+'</pre>'

def transcription(e):
    v=e.get('recognition',{});status=v.get('status','legacy' if e['method']=='Windows OCR' else 'text')
    labels={'readable':'视觉转写完成 · 待人工核对','partial':'部分文字需核对','no_text':'未发现可转写文字','unreadable':'文字无法可靠识别','needs_review':'视觉转写未完成','legacy':'旧版 OCR 候选 · 尚未视觉核对','text':'原文提取'}
    if e['method']=='人工校正':label='已人工校正 · 原始记录保留'
    else:label=labels.get(status,'识别待核对')
    raw=e.get('raw_text',e['text'] if status=='legacy' else None)
    text=e['text'] if status!='legacy' else ''
    uncertain=''.join('<li>'+esc(x)+'</li>' for x in v.get('uncertain_regions',[]))
    note=f'<div class="recognition {"warning" if status in ("partial","unreadable","needs_review","legacy") else ""}"><b>{label}</b><p>自动转写不等于真实性认证；涉及不确定文字，请对照原件。</p>{"<ul>"+uncertain+"</ul>" if uncertain else ""}</div>'
    raw_html=f'<details class="raw"><summary>查看原始 Windows OCR（未经纠正）</summary><p class="muted">可能含误识别，保留用于追溯。</p><pre>{esc(raw or "未识别出文字")}</pre></details>' if raw is not None else ''
    meta=f'<details class="raw"><summary>识别版本与来源记录</summary>{json_block(v)}</details>' if v else ''
    return note+f'<h4>识别文字</h4><pre class="transcript">{esc(text or e.get("error") or ("该页没有可转写文字。" if status=="no_text" else "尚无可靠转写，请核对原图。"))}</pre>'+raw_html+meta

def evidence_links(ids):
    return ' '.join(f'<a class="evidence-link" href="#{report_snapshot.anchor("evidence", ref)}">{esc(ref)}</a>' for ref in ids)


def scheme_sections(snapshot):
    if not snapshot.get('scheme'):
        return ''
    evidence = {e['id']:e for e in snapshot['evidence']}
    rows = []
    for fact in snapshot.get('facts', []):
        refs = report_snapshot.fact_refs(fact)
        positions = list(filter(None,[report_snapshot.location(fact)]))
        if not positions:
            positions = list(filter(None,(report_snapshot.location(evidence[ref]) for ref in refs)))
        rows.append(f'<li><strong>{esc(report_snapshot.fact_text(fact))}</strong> {evidence_links(refs)} {esc("；".join(positions))}</li>')
    facts = '<h3>已提取事实</h3><ul>'+''.join(rows)+'</ul>' if rows else ''
    excluded = snapshot.get('excluded_rules', [])
    excluded_html = '<h3>不适用审核项</h3><ul>'+''.join(f'<li>{esc(r["id"])} · {esc(r.get("name"))} · {esc(r.get("reason"))}</li>' for r in excluded)+'</ul>' if excluded else ''
    task = snapshot['run'].get('supervision_task')
    task_html = ''
    if task:
        fields = ''.join(f'<dt>{label}</dt><dd>{esc(task[k])}</dd>' for k,label in report_snapshot.SUPERVISION_FIELDS.items())
        task_html = '<h3>监管任务来源</h3><dl>'+fields+'</dl>'
    sources = snapshot.get('knowledge_sources', [])
    knowledge = ('<ul>'+''.join(f'<li>{esc(report_snapshot.source_description(item))}</li>' for item in sources)+'</ul>'
                 if sources else '<p>本次运行未记录召回的知识片段。</p>')
    return f'<section><h2>审核方案与依据</h2>{task_html}{facts}{excluded_html}<h3>本次召回的知识来源</h3>{knowledge}</section>'


def render_html(snapshot,images):
    results=snapshot['results'];s=snapshot['summary'];project=snapshot['project']
    run={**snapshot['run'],'evidence':snapshot['evidence'],'manifest':snapshot['trace'].get('manifest') or []}
    info=report_snapshot.presentation(snapshot)
    local=snapshot['technical_checks']
    calendar = local.get('calendar', [])
    photos = local.get('photo_metadata', [])
    local_summary=f'公历文字匹配 {sum(x.get("status")=="matched" for x in calendar)}/{len(calendar)} 条；原图元数据已读取 {sum(x.get("status")=="read" for x in photos)}/{len(photos)} 份。'
    local_html='<details class="technical"><summary>日期星期与原图 EXIF 本地核验</summary><p>'+esc(local_summary)+'</p><p>'+esc(local.get('scope_note','本次审核前已执行，并提供给主模型。'))+'</p>'+json_block(local)+'</details>'
    if not info['finance'] and not (local.get('calendar') or local.get('photo_metadata')):
        local_html=''
    sections=[]
    for r in results:
        refs=evidence_links(r.get('evidence_ids',[]))
        review=r.get('review')
        review_html=f'<div class="review-note"><b>人工复核</b> {esc(review["actor"])} · {esc(review["time"])}<p>AI原判：{esc(r.get("ai_verdict"))} → 当前结论：{esc(r["verdict"])}<br>{esc(review["reason"])}</p></div>' if review else '<p class="muted micro">尚无人工修订记录</p>'
        if review and r.get('ai_result'):
            original=r['ai_result']
            original_refs=evidence_links(original.get('evidence_ids',[]))
            review_html+=f'<details><summary>人工修改前的原始事实、依据和证据</summary><p>{esc(original.get("facts"))}</p><p>{esc(original.get("basis"))}</p><p>{original_refs or esc(original.get("missing"))}</p></details>'
        decision_html='<details class="rule-detail"><summary>独立决策复核记录</summary>'+json_block(r['decision'])+'</details>' if r.get('decision') else ''
        sections.append(f'<article class="result {status_class(r["verdict"])}" id="{report_snapshot.anchor("rule",r["id"])}"><div class="result-head"><span class="rule-id">{esc(r["id"])}</span><span class="risk">{esc(r["risk"])}</span>{badge(r["verdict"])}</div><h3>{esc(r["name"])}</h3><dl><dt>材料事实</dt><dd>{esc(r["facts"])}</dd><dt>判定依据</dt><dd>{esc(r["basis"])}</dd><dt>引用证据</dt><dd>{refs or esc(r.get("missing"))}</dd><dt>处理建议</dt><dd>{esc(r.get("suggestion")) or "—"}</dd></dl><details class="rule-detail"><summary>审核标准与执行信息</summary><p>{esc(r["principle"])}</p><p class="muted micro">执行：{esc(r.get("execution"))} · {esc(r.get("engine"))}</p></details>{decision_html}{review_html}</article>')
    sources=[]
    for e in run.get('evidence',[]):
        image=''
        if e['id'] in images:image=f'<div class="image-stage"><img loading="lazy" alt="{esc(e["id"])} 证据原件预览" src="data:image/jpeg;base64,{base64.b64encode(images[e["id"]]).decode()}"></div>'
        sources.append(f'<details class="source" id="{report_snapshot.anchor("evidence",e["id"])}"><summary><span class="rule-id">{esc(e["id"])}</span> {esc(Path(e["name"]).name)} <span class="page-label">{esc(report_snapshot.location(e))}</span></summary><div class="source-body">{image}<div class="source-transcription">{transcription(e)}<p class="source-meta">{esc(e["name"])}<br>识别方式：{esc(e["method"])}<br>SHA-256：{esc(e["sha256"])}</p></div></div></details>')
    failures=[r for r in results if r['verdict']=='不通过']
    failures.sort(key=lambda r:(r.get('risk')!='P0',r.get('risk')!='P1',r['id']))
    risks=''.join(f'<a class="risk-row" href="#{report_snapshot.anchor("rule",r["id"])}"><span class="risk">{esc(r["risk"])}</span><strong>{esc(r["name"])}</strong><span>{esc(r["id"])}</span></a>' for r in failures[:6])
    revs=snapshot['reviews'];time=snapshot['time'];rid=snapshot['id']
    trace=snapshot['trace']
    css=(Path(__file__).parent/'report.css').read_text(encoding='utf-8')
    counts=s['counts'];review_state='已人工确认' if run.get('confirmed_at') else 'AI初审 · 待人工确认'
    total=info['total']
    version=' · V'+esc(info['version']) if info['version'] else ''
    subtitle=esc(info['name'])+version+(' · '+esc(info['scope']) if info['scope'] else '')
    business_meta = ''.join(f'<span>{label}<br><b>{esc(run[key])}</b></span>'
                            for key, label in [('business_id','业务编号'), ('parent_run','上次审核批次')] if run.get(key))
    out=f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{esc(project['name'])} · {esc(info['title'])}</title><style>{css}</style></head><body>
+<header class="report-cover"><div class="brand-line"><span class="brand">{esc(info['brand'])} <small>{esc(info['title'])}</small></span><span class="report-state">{review_state}</span></div><h1>{esc(project['name'])}</h1><p class="subtitle">{subtitle}</p><div class="conclusion {status_class(s['verdict'])}"><div><small>项目结论</small><h2>{s['verdict']}</h2></div><p>{'存在 '+str(s['blockers'])+' 项 P0 阻断问题，请优先核实。' if s['blockers'] else '请结合逐项结果与原件完成复核。'}<br>{str(total)+'项规则执行完整' if s['complete'] else '含未完成技术项，不得作为完整审核结论'}</p></div><div class="stats"><div><span>通过</span><b>{counts['通过']}</b></div><div class="fail"><span>不通过</span><b>{counts['不通过']}</b></div><div class="manual"><span>待人工判断</span><b>{counts['待人工判断']}</b></div><div><span>人工修订</span><b>{len(revs)}</b></div></div><div class="report-meta"><span>生成时间<br><b>{esc(time)}</b></span><span>审核标准<br><b>{esc(run.get('rule_version'))}</b></span><span>运行批次<br><b>{esc(run['id'])}</b></span>{business_meta}</div></header>
+<nav class="report-nav" aria-label="报告目录"><a href="#risks">主要风险</a><a href="#results">逐项审核</a><a href="#evidence">原件与识别</a><a href="#reviews">人工复核</a><a href="#execution">执行记录</a></nav>
+<section id="risks"><div class="section-heading"><h2>优先处理事项</h2><span>{len(failures)}项不通过</span></div><div class="risk-list">{risks or '<p>当前无明确不通过项；待判断项仍需人工核实。</p>'}</div><p class="muted micro">仅列出优先处理的{min(6,len(failures))}项。下方展示本次返回的逐项结果。</p></section>
+<section id="results"><div class="section-heading"><h2>逐条审核结果（{len(results)} / {total}）</h2></div>{''.join(sections)}</section>
+<section id="evidence"><div class="section-heading"><h2>证据索引及离线预览</h2><span>{len(sources)}处材料证据</span></div><p class="muted">点击展开原件及识别结果。视觉转写和原始 OCR 分开留存，文字不清的区域明确提示。</p>{''.join(sources)}</section>
+<section id="reviews"><div class="section-heading"><h2>完整人工修订记录</h2></div><p class="muted">{'已有 '+str(len(revs))+' 条人工修订。' if revs else '当前尚未进行人工修订，以上为 AI 初审结果。'}</p><details class="technical"><summary>查看完整复核记录</summary>{json_block(revs)}</details></section>
+{scheme_sections(snapshot)}<section id="execution"><div class="section-heading"><h2>执行检查与版本快照</h2></div>{local_html}<p class="muted micro">原包 SHA-256：{esc(run.get('source_hash'))}</p><details class="technical"><summary>规则覆盖、识别记录与模型运行</summary>{json_block(trace)}</details><details class="technical"><summary>材料清单与治理记录</summary>{json_block([{k:v for k,v in m.items() if k!='text'} for m in trace.get('manifest') or []])}</details></section><footer>{esc(info["brand"])} · 当前版本的材料与判定快照<br>自动识别和模型判断不等于专业鉴真。后续补证或改判需生成新报告。</footer></body></html>'''.replace('\n+','\n')
    return out


class IntegrityError(RuntimeError):
    """A stored report no longer matches its published digest."""


def generate(run, format='pdf'):
    if format not in ('pdf','html','json'):
        raise ValueError('报告格式仅支持pdf、html或json')
    snapshot,images=report_snapshot.capture(run)
    payloads={
        'json':report_snapshot.canonical(snapshot),
        'html':render_html(snapshot,images).encode('utf-8'),
        'pdf':report_pdf.render(snapshot,images),
    }
    if not payloads['pdf'].startswith(b'%PDF-'):
        raise RuntimeError('PDF生成未完成')
    folder=store.DATA/'reports';folder.mkdir(exist_ok=True)
    rid=snapshot['id'];formats={}
    media={'pdf':'application/pdf','html':'text/html; charset=utf-8','json':'application/json; charset=utf-8'}
    # No database row exists until all renderers and file writes succeed.
    with tempfile.TemporaryDirectory(prefix='report-',dir=folder) as staging:
        for fmt,payload in payloads.items():
            temp=Path(staging)/(rid+'.'+fmt);temp.write_bytes(payload)
            formats[fmt]={'path':str(folder/temp.name),'sha256':hashlib.sha256(payload).hexdigest(),
                          'content_type':media[fmt],'bytes':len(payload)}
        for fmt in payloads:
            (Path(staging)/(rid+'.'+fmt)).replace(formats[fmt]['path'])
    return store.put('report',{
        'id':rid,'run_id':run['id'],'project_id':run['project_id'],'time':snapshot['time'],
        'review_count':len(snapshot['reviews']),'path':formats['html']['path'],
        'default_format':format,'formats':formats,'schema_version':snapshot['schema_version'],
        'snapshot_sha256':formats['json']['sha256'],'verdict':snapshot['summary']['verdict'],
    })


def public_record(record):
    result={k:v for k,v in record.items() if k not in ('path','formats')}
    result['formats']={fmt:{k:v for k,v in meta.items() if k!='path'} for fmt,meta in record.get('formats',{}).items()}
    for fmt,meta in result['formats'].items():meta['url']=f'/api/reports/{record["id"]}?format={fmt}'
    return result


def download(record, requested=None):
    fmt=requested or record.get('default_format','html')
    if fmt not in ('pdf','html','json'):raise ValueError('不支持的报告格式')
    if record.get('formats'):
        if fmt not in record['formats']:raise FileNotFoundError()
        meta=record['formats'][fmt]
    elif fmt=='html':meta={'path':record['path'],'content_type':'text/html; charset=utf-8'}
    else:raise FileNotFoundError()
    payload=security.local_file(meta['path'],store.DATA/'reports').read_bytes()
    if meta.get('sha256') and hashlib.sha256(payload).hexdigest()!=meta['sha256']:
        raise IntegrityError('报告完整性校验失败，请保留原件并重新生成报告')
    return payload,meta['content_type'],fmt
