"""Deterministic, entirely fictitious examples for local demonstrations."""
import copy

DIMENSIONS = {"flow": "流程核验", "stat": "横向对比", "graph": "图谱联查"}
LAYERS = ["业务事件层", "单据与凭证层", "核算科目层", "主体对象层"]


def initial_config():
    entities = [
        ("event", "业务事件", LAYERS[0], [{"code": "amount", "name": "支付金额", "type": "number", "required": True}, {"code": "date", "name": "支付日期", "type": "date", "required": True}]),
        ("document", "单据凭证", LAYERS[1], [{"code": "number", "name": "单据编号", "type": "string", "required": True}]),
        ("contract", "合同", LAYERS[1], [{"code": "amount", "name": "合同金额", "type": "number", "required": True}]),
        ("account", "核算科目", LAYERS[2], [{"code": "code", "name": "科目编码", "type": "string", "required": True}]),
        ("supplier", "供应商", LAYERS[3], [{"code": "bank_account", "name": "收款账户", "type": "string", "required": True}]),
        ("person", "经办人员", LAYERS[3], [{"code": "name", "name": "姓名", "type": "string", "required": True}]),
        ("customer", "客户", LAYERS[3], [{"code": "name", "name": "客户名称", "type": "string", "required": False}]),
    ]
    return {
        "ontology": {
            "entities": [{"code": c, "name": n, "layer": l, "properties": p} for c, n, l, p in entities],
            "relations": [
                {"code": "has_document", "name": "附有凭证", "source": "event", "target": "document"},
                {"code": "under_contract", "name": "依据合同", "source": "event", "target": "contract"},
                {"code": "paid_to", "name": "支付给", "source": "event", "target": "supplier"},
                {"code": "handled_by", "name": "经办", "source": "event", "target": "person"},
                {"code": "booked_as", "name": "计入科目", "source": "event", "target": "account"},
            ],
            "classifications": ["采购付款", "费用报销", "服务结算"],
            "mappings": [
                {"source": "payment_total", "target": "amount", "entity": "event", "property": "amount"},
                {"source": "payment_day", "target": "payment_date", "entity": "event", "property": "date"},
                {"source": "vendor_account", "target": "bank_account", "entity": "supplier", "property": "bank_account"},
            ],
        },
        "models": [{
            "id": "purchase-cross", "name": "采购付款三维交叉核验", "description": "合同履约、同类金额基线与收款账户关系共同核验。示例规则，可配置调优。",
            "enabled": True, "business_type": "全部", "medium": 40, "high": 70,
            "rules": [
                {"id": "overpayment", "name": "支付超出合同金额", "dimension": "flow", "kind": "over_contract", "threshold": 0.05, "weight": 30, "enabled": True},
                {"id": "missing-acceptance", "name": "付款前缺少验收", "dimension": "flow", "kind": "missing_acceptance", "threshold": 0, "weight": 15, "enabled": True},
                {"id": "date-order", "name": "付款早于签约", "dimension": "flow", "kind": "date_order", "threshold": 0, "weight": 15, "enabled": True},
                {"id": "amount-outlier", "name": "同类金额显著离群", "dimension": "stat", "kind": "amount_outlier", "threshold": 3.5, "weight": 25, "enabled": True},
                {"id": "shared-account", "name": "多个供应商共用收款账户", "dimension": "graph", "kind": "shared_account", "threshold": 2, "weight": 30, "enabled": True},
            ],
        }],
    }


def demo_events():
    rows = []
    for i in range(1, 31):
        amount = 10000 + (i % 9 - 4) * 250
        rows.append({"id": f"DEMO-{i:03}", "name": f"设备配件采购 {i:02}", "unit": "示例集团·华东分部", "business_type": "采购付款", "supplier": f"示例供应商{i:02}", "bank_account": f"DEMO-BANK-{i:03}", "person": f"演示经办{(i % 4)+1}", "amount": amount, "contract_amount": amount, "contract_date": "2026-10-01", "payment_date": f"2026-10-{10+i%18:02}", "accepted": True, "document": f"DEMO-DOC-{i:03}"})
    scenarios = [
        ("DEMO-031", "三维互证 · 合同超付与关联收款", "示例远山设备", "DEMO-SHARED-01", 68000, 20000, False),
        ("DEMO-032", "单维线索 · 收款账户关联", "示例青禾材料", "DEMO-SHARED-01", 10500, 10500, True),
        ("DEMO-033", "双维互证 · 合同金额与统计异常", "示例新程服务", "DEMO-BANK-033", 42000, 15000, True),
        ("DEMO-034", "单维线索 · 缺少验收凭据", "示例明川商贸", "DEMO-BANK-034", 10250, 10250, False),
    ]
    for id_, name, supplier, account, amount, contract, accepted in scenarios:
        row = copy.deepcopy(rows[0])
        row.update(id=id_, name=name, supplier=supplier, bank_account=account, amount=amount, contract_amount=contract, accepted=accepted, document=id_.replace("DEMO-", "DEMO-DOC-"))
        rows.append(row)
    return rows
