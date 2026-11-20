"""Validation, deterministic risk evaluation, and graph tools.

All findings are screening signals. No language model or external service is used.
"""
import copy
import math
import re
import statistics
from collections import defaultdict, deque
from datetime import date

from .seed import DIMENSIONS, LAYERS

KINDS = {"over_contract": "flow", "missing_acceptance": "flow", "date_order": "flow", "amount_outlier": "stat", "shared_account": "graph"}
FIELDS = {"id", "name", "unit", "business_type", "supplier", "bank_account", "person", "amount", "contract_amount", "contract_date", "payment_date", "accepted", "document"}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def text(value, label, limit=120):
    require(isinstance(value, str) and 0 < len(value.strip()) <= limit, f"{label}需要 1—{limit} 个字符")
    return value.strip()


def code(value, label):
    require(isinstance(value, str) and re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_-]{0,59}", value), f"{label}须为英文字母开头的编码")
    return value


def number(value, label, low=0, high=1e12):
    require(type(value) in (int, float) and math.isfinite(value) and low <= value <= high, f"{label}须在 {low} 至 {high} 之间")
    return value


def unique(items, key, label):
    require(isinstance(items, list), f"{label}须为列表")
    values = [item.get(key) for item in items if isinstance(item, dict)]
    require(len(values) == len(items) and len(set(values)) == len(values), f"{label}编码重复或格式错误")


def validate_config(config):
    require(isinstance(config, dict), "配置须为对象")
    ontology = config.get("ontology")
    require(isinstance(ontology, dict), "缺少本体配置")
    entities = ontology.get("entities", [])
    unique(entities, "code", "实体")
    require(1 <= len(entities) <= 100, "实体数量须为 1—100")
    entity_map = {}
    for entity in entities:
        c = code(entity.get("code"), "实体编码")
        text(entity.get("name"), "实体名称")
        require(entity.get("layer") in LAYERS, "本体层级无效")
        props = entity.get("properties", [])
        unique(props, "code", "属性")
        require(len(props) <= 100, "单实体属性不能超过100个")
        for prop in props:
            code(prop.get("code"), "属性编码")
            text(prop.get("name"), "属性名称")
            require(prop.get("type") in ("string", "number", "date", "boolean"), "不支持的属性类型")
            require(type(prop.get("required")) is bool, "属性必填标记须为布尔值")
        entity_map[c] = {p["code"]: p for p in props}
    relations = ontology.get("relations", [])
    unique(relations, "code", "关系")
    require(len(relations) <= 200, "关系不能超过200条")
    for relation in relations:
        code(relation.get("code"), "关系编码")
        text(relation.get("name"), "关系名称")
        require(relation.get("source") in entity_map and relation.get("target") in entity_map, "关系端点必须为已定义实体")
    classes = ontology.get("classifications")
    require(isinstance(classes, list) and 1 <= len(classes) <= 50, "业务分类须有1—50项")
    for c in classes:
        text(c, "业务分类", 40)
    require(len(set(classes)) == len(classes), "业务分类不可重复")
    mappings = ontology.get("mappings", [])
    unique(mappings, "source", "来源字段映射")
    unique(mappings, "target", "目标字段映射")
    require(len(mappings) <= len(FIELDS), "映射数量超过支持字段数")
    expected = {"amount": "number", "contract_amount": "number", "accepted": "boolean", "payment_date": "date", "contract_date": "date"}
    for m in mappings:
        code(m.get("source"), "来源字段")
        require(m.get("target") in FIELDS, "字段映射目标不在业务字段白名单")
        prop = entity_map.get(m.get("entity"), {}).get(m.get("property"))
        require(prop is not None, "字段映射引用了不存在的实体属性")
        require(prop["type"] == expected.get(m["target"], "string"), "本体属性类型与业务字段类型不一致")
    models = config.get("models", [])
    unique(models, "id", "模型")
    require(1 <= len(models) <= 30, "模型数量须为1—30")
    for model in models:
        code(model.get("id"), "模型编码")
        text(model.get("name"), "模型名称")
        text(model.get("description", "无"), "模型说明", 1000)
        require(type(model.get("enabled")) is bool, "模型启停须为布尔值")
        require(model.get("business_type") in ["全部", *classes], "模型业务范围不在本体分类中")
        number(model.get("medium"), "中风险阈值", 1, 99)
        number(model.get("high"), "高风险阈值", 2, 100)
        require(model["medium"] < model["high"], "中风险阈值必须小于高风险阈值")
        rules = model.get("rules", [])
        unique(rules, "id", "规则")
        require(1 <= len(rules) <= 50, "每个模型须有1—50条规则")
        for rule in rules:
            code(rule.get("id"), "规则编码")
            text(rule.get("name"), "规则名称")
            require(rule.get("kind") in KINDS, "不支持的规则运算")
            require(KINDS[rule["kind"]] == rule.get("dimension"), "规则与维度不匹配")
            number(rule.get("weight"), "规则权重", 1, 100)
            number(rule.get("threshold"), "规则阈值", 0, 100)
            if rule["kind"] == "shared_account":
                require(rule["threshold"] >= 2 and int(rule["threshold"]) == rule["threshold"], "账户关联阈值须为至少2的整数")
            if rule["kind"] == "amount_outlier":
                require(rule["threshold"] > 0, "离群阈值必须大于0")
            require(type(rule.get("enabled")) is bool, "规则启停须为布尔值")
    return copy.deepcopy(config)


def impact(old, new):
    """Compatibility is a contract: existing entities/properties/edges cannot break."""
    changes, breaking = [], []
    old_o, new_o = old["ontology"], new["ontology"]
    new_entities = {e["code"]: e for e in new_o["entities"]}
    for entity in old_o["entities"]:
        other = new_entities.get(entity["code"])
        if not other:
            breaking.append(f"删除实体 {entity['code']}")
            continue
        if other["layer"] != entity["layer"]:
            breaking.append(f"实体 {entity['code']} 层级改变")
        props = {p["code"]: p for p in other["properties"]}
        for p in entity["properties"]:
            q = props.get(p["code"])
            if q is None or q["type"] != p["type"] or (not p["required"] and q["required"]):
                breaking.append(f"属性 {entity['code']}.{p['code']} 删除、改型或改为必填")
        existing = {p["code"] for p in entity["properties"]}
        for p in other["properties"]:
            if p["code"] not in existing and p["required"]:
                breaking.append(f"既有实体新增必填属性 {entity['code']}.{p['code']}")
    new_rel = {r["code"]: r for r in new_o["relations"]}
    for r in old_o["relations"]:
        q = new_rel.get(r["code"])
        if q is None or (q["source"], q["target"]) != (r["source"], r["target"]):
            breaking.append(f"关系 {r['code']} 删除或端点改变")
    for m in old_o["mappings"]:
        if m not in new_o["mappings"]:
            breaking.append(f"已发布映射 {m['source']} 改变或删除")
    for c in old_o["classifications"]:
        if c not in new_o["classifications"]:
            breaking.append(f"删除业务分类 {c}")
    for key in ("entities", "relations", "mappings", "classifications"):
        if old_o[key] != new_o[key]:
            changes.append({"section": key, "before": len(old_o[key]), "after": len(new_o[key])})
    if old["models"] != new["models"]:
        changes.append({"section": "models", "before": len(old["models"]), "after": len(new["models"])})
    return {"changes": changes, "breaking": breaking, "compatible": not breaking, "note": "新发布仅用于后续筛查；历史运行保留原配置及业务快照。"}


def validate_events(rows, ontology):
    require(isinstance(rows, list) and 1 <= len(rows) <= 500, "每批导入须有1—500笔业务")
    result = []
    for raw in rows:
        require(isinstance(raw, dict), "业务记录须为对象")
        row = copy.deepcopy(raw)
        for m in ontology["mappings"]:
            if m["source"] in row:
                require(m["target"] not in row or row[m["target"]] == row[m["source"]], f"来源字段 {m['source']} 与 {m['target']} 冲突")
                row[m["target"]] = row[m["source"]]
        provenance = row.get('provenance')
        row = {k: row.get(k) for k in FIELDS}
        if provenance is not None:
            require(isinstance(provenance, dict), '证据来源格式错误')
            allowed = {'run_id','evidence_ids','actor','note','captured_at','evidence_sha256','source_hash','method'}
            require(set(provenance) <= allowed, '证据来源包含不支持字段')
            require(isinstance(provenance.get('run_id'), str) and re.fullmatch(r'[a-f0-9]{16}', provenance['run_id']), '原审核编号无效')
            refs=provenance.get('evidence_ids')
            require(isinstance(refs,list) and 1 <= len(refs) <= 100 and all(isinstance(x,str) and re.fullmatch(r'[A-Z0-9-]{1,60}',x) for x in refs), '证据编号无效')
            for key in ('actor','note'):
                text(provenance.get(key), key, 500)
            row['provenance'] = copy.deepcopy(provenance)
        for field in FIELDS - {"amount", "contract_amount", "accepted"}:
            text(row[field], field)
        require(row["business_type"] in ontology["classifications"], "业务类型未在已发布本体中定义")
        number(row["amount"], "支付金额")
        number(row["contract_amount"], "合同金额", 0.01)
        require(type(row["accepted"]) is bool, "accepted 须为 true 或 false")
        for field in ("contract_date", "payment_date"):
            require(re.fullmatch(r"\d{4}-\d{2}-\d{2}", row[field]) is not None, "日期须为 YYYY-MM-DD")
            try:
                date.fromisoformat(row[field])
            except ValueError:
                raise ValueError("日期不存在") from None
        result.append(row)
    unique(result, "id", "业务单号")
    return result


def build_graph(events):
    nodes, edges = {}, {}
    for row in events:
        kind=row.get('source_kind','business')
        for prefix, field, label in (("supplier", "supplier", "供应商"), ("bank", "bank_account", "收款账户"), ("person", "person", "经办人"), ("event", "id", "业务事件")):
            if not row.get(field):continue
            id_=f"{prefix}:{row[field]}"
            node=nodes.setdefault(id_,{"id":id_,"label":row[field],"type":prefix,"type_label":label,"record_ids":[],"source_kinds":[]})
            if row['id'] not in node['record_ids']:node['record_ids'].append(row['id'])
            if kind not in node['source_kinds']:node['source_kinds'].append(kind)
        links=[(f"supplier:{row['supplier']}",f"bank:{row['bank_account']}","使用账户"),(f"event:{row['id']}",f"supplier:{row['supplier']}","支付给")]
        if row.get('person'):links.append((f"event:{row['id']}",f"person:{row['person']}","经办"))
        for a,b,label in links:
            edge=edges.setdefault((a,b,label),{'source':a,'target':b,'label':label,'record_ids':[],'source_kinds':[]})
            edge['record_ids'].append(row['id'])
            if kind not in edge['source_kinds']:edge['source_kinds'].append(kind)
    degree=defaultdict(int)
    for a,b,_ in edges:degree[a]+=1;degree[b]+=1
    for node in nodes.values():
        node['degree']=degree[node['id']];node['centrality']=round(node['degree']/max(1,len(nodes)-1),4)
    return {'nodes':list(nodes.values()),'edges':[edges[k] for k in sorted(edges)],'records':events}


def graph_path(graph, source, target):
    ids = {n["id"] for n in graph["nodes"]}
    require(source in ids and target in ids, "路径端点不存在")
    adjacency = defaultdict(set)
    for e in graph["edges"]:
        adjacency[e["source"]].add(e["target"])
        adjacency[e["target"]].add(e["source"])
    queue, seen = deque([[source]]), {source}
    while queue:
        path = queue.popleft()
        if path[-1] == target:
            return path
        for node in sorted(adjacency[path[-1]] - seen):
            seen.add(node)
            queue.append([*path, node])
    return []


def evaluate(events, config):
    results = []
    accounts = defaultdict(set)
    for row in events:
        accounts[row["bank_account"]].add(row["supplier"])
    for row in events:
        peers = [r["amount"] for r in events if r["id"] != row["id"] and r["unit"] == row["unit"] and r["business_type"] == row["business_type"]]
        median = statistics.median(peers) if peers else 0
        mad = statistics.median([abs(x - median) for x in peers]) if peers else 0
        scale = max(1.4826 * mad, abs(median) * .05, 1)
        z = abs(row["amount"] - median) / scale
        for model in config["models"]:
            if not model["enabled"] or model["business_type"] not in ("全部", row["business_type"]):
                continue
            checks = []
            for rule in model["rules"]:
                if not rule["enabled"]:
                    continue
                threshold, kind = rule["threshold"], rule["kind"]
                hit, evaluated = False, True
                if kind == "over_contract":
                    ratio = row["amount"] / row["contract_amount"] - 1
                    hit = ratio > threshold + 1e-10
                    explanation = f"支付 {row['amount']:,.2f}，合同 {row['contract_amount']:,.2f}；超付比例 {ratio:.1%}，阈值 {threshold:.1%}。"
                    evidence = {k: row[k] for k in ("amount", "contract_amount", "document")}
                elif kind == "missing_acceptance":
                    hit = not row["accepted"]
                    explanation = "缺少已验收标记，需要核对验收原件。" if hit else "已记录验收标记，真实凭证仍应由原审核系统追溯。"
                    evidence = {"accepted": row["accepted"], "document": row["document"]}
                elif kind == "date_order":
                    hit = row["payment_date"] < row["contract_date"]
                    explanation = f"签约 {row['contract_date']}，付款 {row['payment_date']}。"
                    evidence = {k: row[k] for k in ("contract_date", "payment_date", "document")}
                elif kind == "amount_outlier":
                    evaluated = len(peers) >= 5
                    hit = evaluated and z > threshold
                    explanation = (f"同单位同业务类型、排除本笔后 {len(peers)} 个样本，中位数 {median:,.2f}；稳健偏离值 {z:.2f}，阈值 {threshold}。" if evaluated else f"同类参照样本仅 {len(peers)} 个，少于5个，未评估。")
                    evidence = {"amount": row["amount"], "peer_count": len(peers), "median": median, "mad": mad, "scale": scale, "deviation": round(z, 4), "formula": "abs(amount-median) / max(1.4826*MAD, abs(median)*0.05, 1)"}
                else:
                    suppliers = sorted(accounts[row["bank_account"]])
                    hit = len(suppliers) >= threshold
                    explanation = f"账户 {row['bank_account']} 关联 {len(suppliers)} 家不同供应商，阈值 {int(threshold)} 家；共用账户需核验委托收款等合理原因。"
                    evidence = {"bank_account": row["bank_account"], "suppliers": suppliers}
                checks.append({**rule, "hit": hit, "evaluated": evaluated, "explanation": explanation, "evidence": evidence})
            hits = [c for c in checks if c["hit"]]
            dims = sorted({c["dimension"] for c in hits})
            score = min(100, sum(c["weight"] for c in hits))
            status = "cross" if len(dims) >= 2 else "clue" if dims else "incomplete" if any(not c["evaluated"] for c in checks) or not checks else "clear"
            level = ("高" if score >= model["high"] else "中" if score >= model["medium"] else "低") if status == "cross" else "待核验" if status in ("clue", "incomplete") else "未命中"
            results.append({"id": f"{row['id']}::{model['id']}", "event": copy.deepcopy(row), "model_id": model["id"], "model_name": model["name"], "score": score, "level": level, "status": status, "dimensions": dims, "checks": checks})
    return sorted(results, key=lambda r: (r["status"] == "cross", r["score"]), reverse=True)
