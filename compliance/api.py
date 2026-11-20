"""Compliance endpoints hosted behind the original application's session gate."""
import copy
import hashlib
import json
import re
from .domain import build_graph, graph_path, require, text, validate_events
from .reports import csv_report, html_report
from .store import Store


def database(handler):
    from app import store
    return getattr(handler.server, 'compliance_store', None) or Store(store.DB)


def validate_provenance(rows):
    from app import store
    for row in rows:
        ref = row.get('provenance')
        if not ref:
            continue
        require(isinstance(ref, dict), '证据来源格式错误')
        require(isinstance(ref.get('run_id'), str) and re.fullmatch(r'[a-f0-9]{16}', ref['run_id']), '原审核编号无效')
        try:
            original = store.get('run', ref['run_id'])
        except KeyError:
            raise ValueError('原审核记录不存在，无法关联证据') from None
        require(original.get('status') not in ('queued', 'processing', 'failed'), '请等待原材料识别完成')
        ids = ref.get('evidence_ids')
        require(isinstance(ids, list) and 1 <= len(ids) <= 100 and all(isinstance(x, str) for x in ids), '至少关联一条原件证据')
        evidence = {e['id']: e for e in original.get('evidence', [])}
        require(all(x in evidence for x in ids), '引用了原审核中不存在的证据')
        for key in ('actor', 'note'):
            text(ref.get(key), '事实确认人' if key == 'actor' else '事实确认依据', 500)
        digest = hashlib.sha256(json.dumps([evidence[x] for x in ids], sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        if ref.get('evidence_sha256'):
            require(ref['evidence_sha256'] == digest, '原件证据已发生变化，请重新确认业务事实')
        ref['evidence_sha256'] = digest
        ref['source_hash'] = original.get('source_hash', '')
        ref['method'] = '人工对照原件确认结构化事实'


def capture(db, body):
    from app import store as original_store
    snapshot = db.state()
    require(body.get('revision') == snapshot['dataset']['revision'], '业务数据已变化，请刷新后重新提交')
    row = copy.deepcopy(body.get('row'))
    require(isinstance(row, dict), '请填写业务事实')
    row['provenance'] = {'run_id': body.get('source_run'), 'evidence_ids': body.get('evidence_ids'),
                         'actor': body.get('actor'), 'note': body.get('note'), 'captured_at': original_store.now()}
    validate_provenance([row])
    demo = snapshot['dataset']['source'] == '内置虚构演示数据'
    if demo:
        require(body.get('replace_demo') is True, '请确认以本项目业务替换虚构演示数据，历史筛查仍保留')
    existing = [] if demo else snapshot['dataset']['rows']
    require(not any(r['id'] == row.get('id') for r in existing), '业务单号已存在，请使用新的业务单号；不覆盖已确认事实')
    return db.import_events(existing + [row], '原审核证据 / 人工确认业务事实', body['revision'])


def read(handler, path, query):
    db = database(handler)
    if path.startswith('/lab/'):
        from .risklab import RiskLab
        lab = RiskLab(db)
        if path == '/lab/state': return handler.send(lab.state())
        if path == '/lab/run': return handler.send(lab.get_run(query.get('id', [''])[0]))
        if path == '/lab/export': return handler.send(lab.get_run(query.get('id', [''])[0]), extra={'Content-Disposition': 'attachment; filename=statistical-evaluation.json'})
        if path == '/lab/data': return handler.send(lab.state()['dataset']['rows'], extra={'Content-Disposition': 'attachment; filename=historical-data.json'})
        raise FileNotFoundError()
    if path == '/state':
        return handler.send(db.state())
    if path == '/run':
        try:
            result = db.get_run(query.get('id', [''])[0])
        except LookupError:
            raise FileNotFoundError() from None
        return handler.send(result)
    if path in ('/export/config', '/export/data'):
        state = db.state()
        value = state['active_config'] if path.endswith('config') else state['dataset']['rows']
        name = 'ontology-and-models.json' if path.endswith('config') else 'business-data.json'
        return handler.send(value, extra={'Content-Disposition': f'attachment; filename="{name}"'})
    if path.startswith('/export/'):
        try:
            run = db.get_run(query.get('run', [''])[0])
        except LookupError:
            raise FileNotFoundError() from None
        kind = path.rsplit('/', 1)[-1]
        if kind == 'html':
            return handler.send(html_report(run), kind='text/html; charset=utf-8')
        if kind == 'csv':
            return handler.send(csv_report(run), kind='text/csv; charset=utf-8', extra={'Content-Disposition': 'attachment; filename="risk-ledger.csv"'})
        if kind == 'json':
            return handler.send(run, extra={'Content-Disposition': 'attachment; filename="risk-snapshot.json"'})
    if path == '/tools':
        return handler.send({'version': '0.5', 'tools': ['query', 'metrics', 'graph', 'path'],
                             'authentication': '原应用 X-Session-Token 与同源校验',
                             'scope': '本机工具调用，生产认证与gRPC尚未实现'})
    raise FileNotFoundError()


def write(handler, path, data):
    db = database(handler)
    if path.startswith('/lab/'):
        from .risklab import RiskLab
        lab = RiskLab(db)
        action = path.rsplit('/', 1)[-1]
        result = lab.run(data) if action == 'run' else lab.label(data) if action == 'label' else lab.mutate(action, data)
        return handler.send(result)
    if path == '/config':
        result = db.save_config(data['config'], data['revision'])
    elif path == '/publish':
        result = db.publish(data['revision'], data['note'])
    elif path == '/import':
        rows = validate_events(data['rows'], db.state()['active_config']['ontology'])
        validate_provenance(rows)
        result = db.import_events(rows, data['source'], data['revision'])
    elif path == '/capture':
        result = capture(db, data)
    elif path == '/run':
        result = db.run()
    elif path == '/review':
        result = db.review(data['run_id'], data['result_id'], data['status'], data['actor'], data['note'])
    elif path == '/tools/call':
        rows = db.state()['dataset']['rows']
        filters = data.get('filters', {})
        require(isinstance(filters, dict) and set(filters) <= {'unit', 'business_type', 'supplier'}, '不支持的筛选字段')
        rows = [r for r in rows if all(r[k] == v for k, v in filters.items())]
        name = data.get('name')
        if name == 'query':
            result = {'rows': rows, 'count': len(rows), 'source': db.state()['dataset']['source']}
        elif name == 'metrics':
            total = sum(r['amount'] for r in rows)
            result = {'count': len(rows), 'total_amount': round(total, 2), 'average_amount': round(total / len(rows), 2) if rows else 0,
                      'definition': '按业务记录逐笔合计支付金额；不合并供应商'}
        elif name in ('graph', 'path'):
            graph = build_graph(rows)
            result = graph if name == 'graph' else {'path': graph_path(graph, data.get('source'), data.get('target'))}
        else:
            raise ValueError('工具名称不存在')
    else:
        raise FileNotFoundError()
    return handler.send(result)
