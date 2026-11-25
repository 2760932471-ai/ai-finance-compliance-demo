"""Versioned report contract shared by all export formats; no model calls."""
import copy
import hashlib
import json
import re
from pathlib import PurePosixPath, PureWindowsPath
from . import checks, engine, security, store

LEGACY_VERSION = 'audit-report-v1'
VERSION = 'audit-report-v2'
SUPERVISION_FIELDS = {'id':'监管核查单号', 'row_id':'关联付款流水', 'org_id':'所属组织', 'question':'定向核验问题'}
SAFE_ID = re.compile(r'[a-zA-Z0-9][a-zA-Z0-9_.:-]{0,127}')


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2).encode('utf-8')


def clean(value):
    """Remove filesystem/configuration fields from downloadable structured data."""
    excluded = {'path', 'image', 'source', 'cache_key', 'api_key', 'token', 'input_path', 'output_path'}
    if isinstance(value, dict):
        return {k: clean(v) for k, v in value.items() if k.lower() not in excluded}
    if isinstance(value, list):
        return [clean(v) for v in value]
    return copy.deepcopy(value)


def anchor(kind, identifier):
    # Preserve the fragment URLs of legacy reports. Namespace all other IDs so a
    # rule named "results" can never shadow the section or an evidence bookmark.
    legacy_pattern = r'T1-0[1-5]-\d{2}' if kind == 'rule' else r'D\d+-P\d+'
    if re.fullmatch(legacy_pattern, identifier):
        return identifier
    return kind + '-' + identifier.encode('utf-8').hex()


def presentation(snapshot):
    scheme = snapshot.get('scheme', {})
    finance = scheme.get('id', 'finance-t1') == 'finance-t1'
    return {
        'brand': 'AI财务审核' if finance else 'AI审核工作台',
        'title': scheme.get('report', {}).get('title') or ('财务费用审核报告' if finance else '业务审核报告'),
        'name': scheme.get('name') or ('活动类费用审核 / 经销商垫付 F 类' if finance else '业务审核'),
        'scope': scheme.get('scope') or '',
        'version': str(scheme.get('version', '')),
        'total': len(snapshot.get('applicable_rule_ids', engine.RULES)),
        'finance': finance,
    }


def location(entry):
    locator = entry.get('locator') or {}
    if not isinstance(locator, dict):
        locator = {}
    parts = []
    # DOCX and TXT use the page index internally for stable evidence IDs.
    # Only print a physical page for a locator which actually supplies one.
    page = locator.get('page')
    if page is None and not (locator.get('clause') or locator.get('row') or locator.get('type') in ('clause','row')):
        page = entry.get('page')
    if page is not None:
        parts.append(f'第{page}页')
    for key, label in [('clause', '条款'), ('table', '表'), ('row', '行'), ('field', '字段')]:
        if locator.get(key) is not None:
            parts.append(f'{label} {locator[key]}')
    if locator.get('bbox'):
        parts.append('图像区域 ' + str(locator['bbox']))
    return ' · '.join(parts)


def _source_label(value):
    value = str(value or '')
    if value.startswith('file://'):
        value = value[7:]
    if PurePosixPath(value).is_absolute() or PureWindowsPath(value).is_absolute():
        return PureWindowsPath(value).name
    return value


def knowledge_sources(run):
    sources = []
    for chunk in run.get('knowledge_snapshot') or []:
        if not isinstance(chunk, dict):
            continue
        item = {k: clean(chunk[k]) for k in ('id', 'title', 'version', 'chunk_id', 'line', 'start', 'end') if k in chunk}
        if chunk.get('source'):
            item['source'] = _source_label(chunk['source'])
        sources.append(item)
    return sources


def source_description(item):
    title = str(item.get('title') or item.get('id') or '知识材料')
    details = [str(item.get('source') or ''), '版本 '+str(item['version']) if item.get('version') else '',
               '片段 '+str(item['chunk_id']) if item.get('chunk_id') else '',
               '第'+str(item['line'])+'行' if item.get('line') else '']
    return ' · '.join([title, *filter(None, details)])


def _checked_ids(records, label):
    ids = set()
    for item in records:
        ident = item.get('id') if isinstance(item, dict) else None
        if not isinstance(ident, str) or not SAFE_ID.fullmatch(ident) or ident in ids:
            raise ValueError(f'{label}编号无效或重复')
        ids.add(ident)
    return ids


def _check_refs(refs, valid):
    if not isinstance(refs, list) or any(not isinstance(ref, str) or ref not in valid for ref in refs):
        raise ValueError('报告包含不存在的证据引用')


def fact_refs(fact):
    refs = fact.get('evidence_ids', [])
    if not isinstance(refs, list):
        raise ValueError('事实证据引用须为列表')
    if 'evidence_id' in fact and fact['evidence_id'] not in refs:
        refs = [*refs, fact['evidence_id']]
    return refs


def fact_text(fact):
    return f"{fact.get('label') or fact.get('field') or '业务事实'}：{fact.get('value', '')}"


def capture(run):
    project = store.get('project', run['project_id'])
    reviews = store.related('review', 'run_id', run['id'])
    evidence = clean(run.get('evidence', []))
    evidence_ids = _checked_ids(evidence, '证据')
    scheme = run.get('scheme_snapshot')
    rules = run.get('rules', engine.RULES)
    rule_ids = _checked_ids(rules, '方案审核项')
    raw_results = run.get('results', [])
    if not _checked_ids(raw_results, '审核结果').issubset(rule_ids):
        raise ValueError('报告含未列入本次适用范围的审核项')
    if scheme:
        published_ids = _checked_ids(scheme['rules'], '已发布方案')
        excluded_ids = _checked_ids(run.get('excluded_rules') or [], '不适用审核项')
        if not (rule_ids | excluded_ids) <= published_ids or rule_ids & excluded_ids:
            raise ValueError('本次审核范围与方案快照不一致')
    else:
        if (run.get('scheme_id', 'finance-t1') != 'finance-t1'
                or any(not re.fullmatch(r'T1-0[1-5]-\d{2}', rid) for rid in rule_ids)):
            raise ValueError('业务审核报告缺少运行时方案快照')
        if any(not re.fullmatch(r'D\d+-P\d+', eid) for eid in evidence_ids):
            raise ValueError('旧版审核报告的证据编号无效')
    results = engine.effective_results(run, reviews=reviews)
    for entry in [*raw_results, *results, *reviews]:
        _check_refs(entry.get('evidence_ids', []), evidence_ids)
    if any(review.get('rule_id') not in rule_ids for review in reviews):
        raise ValueError('报告含未列入本次适用范围的人工复核项')
    images = {}
    for entry in run.get('evidence', []):
        if entry.get('image'):
            image = security.local_file(entry['image'], store.DATA/'runs'/run['id'])
            images[entry['id']] = image.read_bytes()
    snapshot = {
        'schema_version': VERSION if scheme else LEGACY_VERSION, 'id': store.uid(), 'time': store.now(),
        'project': {'id': project['id'], 'name': project['name']},
        'run': {k: run.get(k, '') for k in (
            'id', 'project_id', 'rule_version', 'source_hash', 'created_at',
            'confirmed_at', 'confirmed_by', 'app_version', 'status', 'parent_run')},
        'summary': engine.summarize_results(results, rules), 'results': clean(results),
        'ai_results': clean(raw_results), 'evidence': evidence,
        'reviews': clean(list(reversed(reviews))),
        'technical_checks': clean(checks.snapshot(run)),
        'trace': clean({k: run.get(k) for k in (
            'quality', 'harness_version', 'recognition_version', 'recognition_usage',
            'decision_review', 'traces', 'knowledge_snapshot', 'corrections', 'manifest')}),
    }
    if scheme:
        facts = []
        for original in run.get('facts') or []:
            _check_refs(fact_refs(original), evidence_ids)
            fact = clean(original)
            if original.get('source'):
                fact['source'] = _source_label(original['source'])
            facts.append(fact)
        snapshot.update(
            scheme=clean({k: scheme[k] for k in ('id','version','name','scope','rules','knowledge_ids','material_types','report') if k in scheme}),
            applicable_rule_ids=[r['id'] for r in rules],
            excluded_rules=clean(run.get('excluded_rules') or []),
            knowledge_sources=knowledge_sources(run),
            facts=facts,
        )
        if run.get('business_id'):
            snapshot['run']['business_id'] = run['business_id']
        task = run.get('supervision_task')
        if task:
            if not isinstance(task, dict) or not all(isinstance(task.get(k), str) and task[k].strip() for k in SUPERVISION_FIELDS):
                raise ValueError('监管任务来源缺少核查单号、付款流水、所属组织或定向问题')
            snapshot['run']['supervision_task'] = {k:task[k] for k in SUPERVISION_FIELDS}
    for entry in snapshot['evidence']:
        if entry['id'] in images:
            entry['preview_sha256'] = hashlib.sha256(images[entry['id']]).hexdigest()
    return snapshot, images
