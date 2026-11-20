"""Local, searchable A4 report. Uploaded text never becomes active PDF markup."""
import io
import os
import re
import threading
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, PageBreak, Image, Table, TableStyle,
)

FONT_LOCK = threading.Lock()
FONT_NAME = 'AuditChinese'
NAVY = colors.HexColor('#172D4B')
BLUE = colors.HexColor('#365DE8')
MUTED = colors.HexColor('#58677E')


def font_name():
    with FONT_LOCK:
        if FONT_NAME in pdfmetrics.getRegisteredFontNames(): return FONT_NAME
        candidates = [os.getenv('AUDIT_PDF_FONT', ''), str(Path(__file__).resolve().parents[1] / 'assets/fonts/wqy-zenhei.ttc'), 'C:/Windows/Fonts/msyh.ttc',
                      '/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc']
        for candidate in candidates:
            if candidate and Path(candidate).is_file():
                pdfmetrics.registerFont(TTFont(FONT_NAME, candidate, subfontIndex=0))
                pdfmetrics.registerFontFamily(FONT_NAME, normal=FONT_NAME, bold=FONT_NAME, italic=FONT_NAME, boldItalic=FONT_NAME)
                return FONT_NAME
        raise RuntimeError('PDF中文字体不可用，请设置AUDIT_PDF_FONT为中文TrueType字体路径')


def safe_text(value):
    text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', str(value or '—'))
    return escape(text).replace('\n', '<br/>')


def excerpt(value, max_chars, max_lines):
    """Keep the PDF readable without confusing an excerpt with the source text."""
    text = str(value or '—')
    lines = text.splitlines()
    if len(lines)>max_lines:
        head = max_lines//2
        tail = max_lines-head-1
        lines = lines[:head]+['[中间文字省略]']+(lines[-tail:] if tail else [])
    selected = '\n'.join(lines)
    if len(selected)>max_chars:
        head = max_chars*2//3
        selected = selected[:head]+'\n[中间文字省略]\n'+selected[-(max_chars-head):]
    if selected != text:
        return selected + '\n[以上为文字摘录；完整内容见同一报告的HTML或JSON快照。]'
    return selected


class ReportDocument(SimpleDocTemplate):
    def afterFlowable(self, flowable):
        anchor = getattr(flowable, 'audit_anchor', None)
        if anchor:
            self.canv.bookmarkPage(anchor)
            self.canv.addOutlineEntry(flowable.getPlainText()[:100], anchor,
                                      getattr(flowable, 'audit_level', 0))


def render(snapshot, images):
    """Return complete PDF bytes or raise; caller publishes only after success."""
    font = font_name()
    styles = {
        'title': ParagraphStyle('title', fontName=font, fontSize=23, leading=33, textColor=NAVY, spaceAfter=16),
        'heading': ParagraphStyle('heading', fontName=font, fontSize=15, leading=23, textColor=NAVY, spaceBefore=14, spaceAfter=9, keepWithNext=True),
        'body': ParagraphStyle('body', fontName=font, fontSize=10, leading=17, wordWrap='CJK', spaceAfter=7),
        'small': ParagraphStyle('small', fontName=font, fontSize=8.5, leading=14, wordWrap='CJK', textColor=MUTED, spaceAfter=5),
    }
    stream = io.BytesIO()
    doc = ReportDocument(stream, pagesize=A4, leftMargin=42, rightMargin=42,
                         topMargin=44, bottomMargin=42, title='AI财务审核 · '+snapshot['project']['name'],
                         author='AI财务审核', pageCompression=1)
    story = []

    def paragraph(text, style='body', markup=False):
        item = Paragraph(text if markup else safe_text(text), styles[style])
        story.append(item)
        return item

    def heading(text, anchor):
        item = paragraph(text, 'heading')
        item.audit_anchor = anchor
        item.audit_level = 1 if re.match(r'^(T1-|D\d+-P)', anchor) else 0

    def field(label, value):
        paragraph(f'<b>{safe_text(label)}</b>　{safe_text(value)}', markup=True)

    def table(rows, widths):
        data = [[Paragraph(safe_text(cell), styles['small']) for cell in row] for row in rows]
        item = Table(data, colWidths=widths, repeatRows=1, hAlign='LEFT')
        item.setStyle(TableStyle([
            ('BACKGROUND',(0,0),(-1,0),colors.HexColor('#EAF0FA')),
            ('VALIGN',(0,0),(-1,-1),'TOP'), ('GRID',(0,0),(-1,-1),.4,colors.HexColor('#DCE3EE')),
            ('LEFTPADDING',(0,0),(-1,-1),8), ('RIGHTPADDING',(0,0),(-1,-1),8),
            ('TOPPADDING',(0,0),(-1,-1),7), ('BOTTOMPADDING',(0,0),(-1,-1),7),
        ]))
        story.append(item)

    run = snapshot['run']; summary = snapshot['summary']; counts = summary['counts']
    paragraph('AI财务审核', 'title')
    paragraph(snapshot['project']['name'], 'heading')
    field('项目结论', summary['verdict'])
    field('确认状态', f"已人工确认：{run['confirmed_by']} / {run['confirmed_at']}" if run['confirmed_at'] else 'AI初审 · 待人工确认')
    field('优先事项', f"P0阻断 {summary['blockers']} 项；规则执行 {'完整' if summary['complete'] else '未完整，不可作为完整审核结论'}")
    table([['通过','不通过','待人工判断','规则返回'],
           [counts['通过'],counts['不通过'],counts['待人工判断'],f"{summary['total']} / 65"]], [128,128,128,127])
    paragraph(f"报告时间：{snapshot['time']}\n运行编号：{run['id']}\n报告编号：{snapshot['id']}\n规则版本：{run['rule_version']}", 'small')
    heading('优先处理事项', 'risks')
    failures = sorted((r for r in snapshot['results'] if r['verdict']=='不通过'), key=lambda r:(r.get('risk')!='P0', r['id']))
    for result in failures[:6]:
        paragraph(f'<link href="#{result["id"]}" color="#365DE8">{safe_text(result["id"]+" "+result.get("name","")+" "+result.get("risk",""))}</link>', markup=True)
        paragraph(result.get('suggestion') or '请对照关联原件核实后处理。', 'small')
    if not failures: paragraph('未发现明确不通过项；待人工项仍需核实。')
    paragraph('阅读顺序：概览与风险 → 逐项审核 → 证据原件与识别 → 人工记录 → 执行来源。PDF书签及证据编号可跳转。', 'small')
    paragraph('AI观察与转写不等于真实性认证。无法核实的内容保留人工判断；最终责任由财务确认。', 'small')
    story.append(PageBreak())
    heading('逐项审核结果', 'results')
    for result in snapshot['results']:
        heading(f"{result['id']}  {result.get('name','')}  [{result['verdict']}]", result['id'])
        field('风险与审核标准', result.get('risk','')+' · '+result.get('principle',''))
        field('AI观察事实', result.get('facts'))
        field('原审核依据', result.get('basis'))
        references = result.get('evidence_ids', [])
        if references:
            paragraph('当前引用证据：'+'　'.join(f'<link href="#{ref}" color="#365DE8">{ref}</link>' for ref in references), markup=True)
        else: field('当前引用证据', result.get('missing') or '未引用材料，需人工补充依据。')
        if result.get('missing'): field('缺失或不确定性', result['missing'])
        field('处理建议', result.get('suggestion'))
        execution = result.get('execution','未记录')
        field('执行来源', result.get('engine','未记录')+' / '+{'completed':'已完成','technical_incomplete':'技术未完成'}.get(execution,execution))
        review = result.get('review')
        if review:
            field('AI原判 / 人工决定', result.get('ai_verdict','')+' / '+result['verdict'])
            original = result.get('ai_result', {})
            field('AI原始引用', '、'.join(original.get('evidence_ids', [])) or '未引用材料')
            field('人工依据', review.get('reason'))
            paragraph(f"复核人：{review.get('actor','')}　时间：{review.get('time','')}", 'small')
        decision = result.get('decision')
        if decision:
            # Explicit readable summary; full vendor payload remains in JSON snapshot.
            field('独立复核', json_summary(decision))
        story.append(Spacer(1, 10))
    story.append(PageBreak())
    heading('证据索引', 'evidence-index')
    table([['证据编号','来源文件 / 页码','识别方式']]+[
        [e['id'], f"{e['name']} / 第{e['page']}页", e['method']] for e in snapshot['evidence']], [84,320,107])
    for evidence in snapshot['evidence']:
        story.append(PageBreak())
        heading(f"{evidence['id']}  原件与识别", evidence['id'])
        field('来源', f"{evidence['name']} · 第{evidence['page']}页")
        if evidence['id'] in images:
            image = Image(io.BytesIO(images[evidence['id']]))
            scale = min(511/image.imageWidth, 320/image.imageHeight)
            image.drawWidth = image.imageWidth*scale; image.drawHeight = image.imageHeight*scale
            story.append(image); story.append(Spacer(1, 9))
        recognition = evidence.get('recognition', {})
        status = recognition.get('status','原文提取或历史记录')
        field('识别方式与状态', evidence['method']+' / '+{'readable':'可读','partial':'部分可读','unreadable':'不可读','no_text':'无可见文字','failed':'识别失败'}.get(status,status))
        uncertain = recognition.get('uncertain_regions', [])
        if uncertain: field('不确定区域', '；'.join(map(str, uncertain)))
        legacy = evidence['method']=='Windows OCR' and not recognition
        illustrated = evidence['id'] in images
        field('转写 / 提取文字', '仅有原始OCR候选，尚无可靠转写。' if legacy else
              excerpt(evidence.get('text'), 360 if illustrated else 900, 6 if illustrated else 16))
        if 'raw_text' in evidence or legacy:
            field('原始OCR候选（未经纠正）', excerpt(evidence.get('raw_text', evidence.get('text')),
                                                    120 if illustrated else 200, 2 if illustrated else 4))
        paragraph('原件SHA-256：'+evidence['sha256'], 'small')
        paragraph('识别结果仅说明材料可见内容，不证明照片、票据或印章真实。', 'small')
    story.append(PageBreak())
    heading('人工修订与确认记录', 'reviews')
    if not snapshot['reviews']: paragraph('暂无人工修订记录。')
    for review in snapshot['reviews']:
        field(review.get('rule_id','审核项'), f"{review.get('before','')} → {review.get('after','')}")
        field('修改依据', review.get('reason'))
        paragraph(f"复核人：{review.get('actor','')} / {review.get('time','')} / 证据：{'、'.join(review.get('evidence_ids',[]))}", 'small')
    field('最终确认', f"{run['confirmed_by']} / {run['confirmed_at']}" if run['confirmed_at'] else '尚未人工确认')
    heading('执行来源与完整性', 'execution')
    trace = snapshot['trace']; local = snapshot['technical_checks']
    field('报告快照版本', snapshot['schema_version'])
    field('Harness / 识别版本', str(trace.get('harness_version') or '历史未记录')+' / '+str(trace.get('recognition_version') or '历史未记录'))
    paragraph(local.get('scope_note', '本地核验记录由程序生成，日期匹配或含EXIF不等于照片真实。'))
    field('日期核验', f"{len(local.get('calendar',[]))} 条；匹配 {sum(x.get('status')=='matched' for x in local.get('calendar',[]))} 条")
    field('原图元数据', f"{len(local.get('photo_metadata',[]))} 份记录；读取成功 {sum(x.get('status')=='read' for x in local.get('photo_metadata',[]))} 份")
    for task in trace.get('traces') or []:
        paragraph(f"尝试 {task.get('attempt','')}：{task.get('status','')}；接受 {task.get('accepted',0)} 条；缓存：{task.get('cached',False)}；模型任务：{task.get('model_job','未记录')}", 'small')
    paragraph('原包SHA-256：'+str(run.get('source_hash') or '未记录'), 'small')
    paragraph('同一报告的HTML及JSON保存全部执行和识别元数据。报告文件摘要由下载记录提供；摘要用于一致性核对，不是数字签名。', 'small')

    def footer(canvas, document):
        canvas.saveState(); canvas.setFont(font, 8); canvas.setFillColor(MUTED)
        canvas.drawString(42, 24, 'AI财务审核 · '+snapshot['id'])
        canvas.drawRightString(A4[0]-42, 24, f'第 {document.page} 页')
        canvas.restoreState()

    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return stream.getvalue()


def json_summary(decision):
    fields = {'provider':'提供方','model':'模型','mode':'模式','suggested_verdict':'建议判定','needs_review':'需复核','threshold_calibrated':'阈值已标定','error':'异常'}
    labels = {'startlux':'StartLux','jev':'Jev','shadow':'仅记录对比，不改判','gate':'冲突转人工',
              'supported':'有证据支持','unsupported':'证据不支持','uncertain':'不确定',
              'satisfies':'符合','violates':'不符合','unknown':'无法判定'}
    def display(value):
        if isinstance(value,bool):return '是' if value else '否'
        return labels.get(str(value),str(value))
    pairs = [f'{label}：{display(decision[key])}' for key,label in fields.items() if key in decision and not isinstance(decision[key], (dict, list))]
    for key,label in (('support','证据支持'),('verdict','独立判定')):
        answer=decision.get('answers',{}).get(key,{})
        if answer:
            confidence=answer.get('confidence')
            score=f'{confidence:.3f}' if isinstance(confidence,(int,float)) else '未记录'
            pairs.append(f"{label}：{display(answer.get('choice',''))}；模型置信值：{score}")
    if pairs:pairs.append('模型置信值不代表业务准确率')
    return '；'.join(pairs) or '已保留独立复核记录；完整字段见同一报告JSON快照。'
