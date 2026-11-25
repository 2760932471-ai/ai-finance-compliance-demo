"""Project/issue identity and phase timing shared by the workflow API."""
import io
import zipfile
from pathlib import Path
from . import store, materials

FINANCE = 'finance-t1'


def business_identity(run):
    if run.get('business_id'):
        return run['business_id']
    current = run
    seen = {run['id']}
    while current.get('parent_run') and current['parent_run'] not in seen:
        seen.add(current['parent_run'])
        try:
            current = store.get('run', current['parent_run'])
        except KeyError:
            break
        if current.get('business_id'):
            return current['business_id']
    return 'BUS-' + current['id']


def wrap_material(raw, filename):
    """Keep original ZIPs unchanged. Wrap an explicitly named, supported single file."""
    if not filename or Path(filename).suffix.lower() == '.zip':
        return raw
    materials.safe_name(filename)
    name = Path(filename).name
    if name != filename or '\\' in name or len(name) > 220 or Path(name).suffix.lower() not in materials.SUPPORTED:
        raise ValueError('请选择 ZIP、PDF、DOCX、TXT、HTML、JPG 或 PNG 材料')
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_STORED) as package:
        package.writestr(name, raw)
    return archive.getvalue()


def scoped_documents(scheme_id):
    return [doc for doc in store.all_items('knowledge') if doc.get('scheme_id', FINANCE) == scheme_id]


def scoped_document(identifier, scheme_id):
    doc = store.get('knowledge', identifier)
    if doc.get('scheme_id', FINANCE) != scheme_id:
        raise KeyError('资料不在当前方案')
    return doc


def timing_metrics(runs):
    samples = {key: [] for key in ('cold', 'cached', 'mixed', 'no_model', 'failed')}
    for run in runs:
        if not isinstance(run.get('duration'), (int, float)):
            continue
        usage = run.get('usage', [])
        jobs = [item for item in usage if item.get('cached') or item.get('job_id')]
        hits = sum(bool(item.get('cached')) for item in jobs)
        errors = [item['error'] for item in usage if item.get('error')]
        for trace in run.get('traces', []):
            if trace.get('status') == 'failed':
                errors.extend(trace.get('errors', {}).values())
        if any('离线演示' not in str(message) for message in errors):
            bucket = 'failed'
        elif not jobs:
            bucket = 'no_model'
        elif not hits:
            bucket = 'cold'
        else:
            bucket = 'cached' if hits == len(jobs) else 'mixed'
        samples[bucket].append(run['duration'])
    return {key: {'count': len(times), 'avg_seconds': round(sum(times)/len(times),1) if times else None}
            for key,times in samples.items()}
