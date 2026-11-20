"""Versioned report contract shared by all export formats; no model calls."""
import copy
import hashlib
import json
import re
from . import checks, engine, security, store

VERSION = 'audit-report-v1'


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


def capture(run):
    project = store.get('project', run['project_id'])
    reviews = store.related('review', 'run_id', run['id'])
    results = engine.effective_results(run, reviews=reviews)
    evidence = clean(run.get('evidence', []))
    evidence_ids = {e['id'] for e in evidence}
    for entry in evidence:
        if not re.fullmatch(r'D\d+-P\d+', entry['id']):
            raise ValueError('证据编号无效，不能生成报告')
    for result in results:
        if not re.fullmatch(r'T1-0[1-5]-\d{2}', result['id']):
            raise ValueError('审核规则编号无效')
        if any(ref not in evidence_ids for ref in result.get('evidence_ids', [])):
            raise ValueError('报告包含不存在的证据引用')
    images = {}
    for entry in run.get('evidence', []):
        if entry.get('image'):
            image = security.local_file(entry['image'], store.DATA/'runs'/run['id'])
            images[entry['id']] = image.read_bytes()
    snapshot = {
        'schema_version': VERSION, 'id': store.uid(), 'time': store.now(),
        'project': {'id': project['id'], 'name': project['name']},
        'run': {k: run.get(k, '') for k in (
            'id', 'project_id', 'rule_version', 'source_hash', 'created_at',
            'confirmed_at', 'confirmed_by', 'app_version', 'status', 'parent_run')},
        'summary': engine.summarize_results(results,run.get('rules',engine.RULES)), 'results': clean(results),
        'ai_results': clean(run.get('results', [])), 'evidence': evidence,
        'reviews': clean(list(reversed(reviews))),
        'technical_checks': clean(checks.snapshot(run)),
        'trace': clean({k: run.get(k) for k in (
            'quality', 'harness_version', 'recognition_version', 'recognition_usage',
            'decision_review', 'traces', 'knowledge_snapshot', 'corrections', 'manifest')}),
    }
    # Snapshot contains immutable page image digests, while images are embedded by renderers.
    for entry in snapshot['evidence']:
        if entry['id'] in images:
            entry['preview_sha256'] = hashlib.sha256(images[entry['id']]).hexdigest()
    return snapshot, images
