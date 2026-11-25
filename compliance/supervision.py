"""Single-organization local supervision demo. Payments are independent of audit runs."""
import copy
import hashlib
import json
import re
from datetime import date
from decimal import Decimal
from urllib.parse import quote

from .store import Conflict, now

ORG = "ORG-DEMO"
KEY = "supervision_q3_v1"
SCOPE = {
    "org_id": ORG, "org_name": "示例一公司", "period": "2026Q3",
    "start": "2026-07-01", "end": "2026-09-30",
    "rule_id": "PRE-ACCEPT-01", "rule_version": "1.0",
    "rule": "按合同累计验收前付款，使用付款当日有效的限额，超出时生成核查线索。",
    "currency": "CNY", "basis": "季度付款台账，人民币元；按付款日计算，未关联和未审记录均计入分母。",
    "demo": True, "opening_balance": 0,
}
# The contract was signed in this quarter, so all payments since inception are in scope.
CONTRACT_ID = "HT-2026-071"
SUPPLIER_ID = "SUP-001"
SUPPLIER = "示例青禾设备有限公司"
CONTRACT = {
    "id": CONTRACT_ID, "supplier_id": SUPPLIER_ID, "supplier": SUPPLIER,
    "signed": "2026-07-01", "accepted": "2026-09-18",
    "terms": [("2026-07-01", 4000000, "CONTRACT-1"), ("2026-09-10", 6000000, "AMEND-1")],
}
BASE_SOURCES = {
    "CONTRACT-1": ("采购合同", "第 1 页 · 第四条", "验收前付款限额",
                   "合同 HT-2026-071｜供方 SUP-001｜2026-07-01 生效。第四条：验收签字前累计付款以人民币 40,000.00 元为限。"),
    "AMEND-1": ("补充协议", "第 1 页 · 第一条", "新限额生效日期",
                "补充协议一｜合同 HT-2026-071｜2026-09-10 起，验收前累计付款限额变更为人民币 60,000.00 元。此前支付不适用新限额。"),
    "ACCEPT-1": ("验收登记", "验收登记表 · 第 1 行", "验收日期",
                 "验收登记 YS-0918｜对应合同 HT-2026-071｜2026-09-18 签收，无日内时刻。"),
    "AUDIT-1": ("已有付款审核记录", "审核记录 A-001 · 第 1 项", "原审核覆盖范围",
                "审核记录 A-001｜对象 PAY-001｜已复核该笔 30,000.00 元付款的单据与合同编号。记录日期 2026-07-18。该次审核仅涉及 PAY-001，未覆盖 PAY-002。"),
}


def ensure(condition, message):
    if not condition:
        raise ValueError(message)


def string(value, label, limit=160):
    ensure(isinstance(value, str) and 0 < len(value.strip()) <= limit, f"{label}须为 1 至 {limit} 字")
    return value.strip()


def identifier(value, label):
    ensure(isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}", value), f"{label}格式无效")
    return value


def iso_day(value, label):
    ensure(isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value), f"{label}须为 YYYY-MM-DD")
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        raise ValueError(f"{label}不是有效日期") from None


def cents(value):
    ensure(not isinstance(value, bool) and isinstance(value, (str, int, float)), "付款金额须为人民币元")
    num = str(value)
    ensure(re.fullmatch(r"(?:0|[1-9]\d{0,9})(?:\.\d{1,2})?", num), "付款金额须为正数，最多两位小数")
    amount = int(Decimal(num) * 100)
    ensure(amount > 0, "只支持正向付款流水")
    return amount


def money(value):
    return value / 100


def normalize(payment):
    ensure(isinstance(payment, dict), "每笔付款须为 JSON 对象")
    ensure(payment.get("org_id") == ORG, "付款不属于本演示授权组织")
    allowed = {"id","org_id","date","amount","supplier","supplier_id","contract_id","currency"}
    ensure(set(payment) <= allowed, "付款包含未定义字段，不可导入审核状态或判断结果")
    paid = iso_day(payment.get("date"), "付款日期")
    ensure(SCOPE["start"] <= paid <= SCOPE["end"], "付款日期超出本季度台账")
    ensure(payment.get("currency", "CNY") == "CNY", "当前台账仅统计人民币付款")
    return {
        "id": identifier(payment.get("id"), "流水号"), "org_id": ORG, "date": paid,
        "amount_cents": cents(payment.get("amount")), "supplier": string(payment.get("supplier"), "收款方"),
        "supplier_id": identifier(payment["supplier_id"], "供应商主键") if payment.get("supplier_id") else "",
        "contract_id": identifier(payment["contract_id"], "合同编号") if payment.get("contract_id") else "",
    }


def template():
    return {"source": "演示导入模板（模拟银行台账）", "rows": [
        {"id":"IMPORT-001","org_id":ORG,"date":"2026-09-21","amount":"5000.00",
         "supplier":SUPPLIER,"supplier_id":SUPPLIER_ID,"contract_id":CONTRACT_ID}
    ]}


def initial():
    rows = []
    for rid, paid, amount, sid, contract in [
        ("PAY-001","2026-07-15",30000,SUPPLIER_ID,CONTRACT_ID),
        ("PAY-002","2026-08-20",25000,SUPPLIER_ID,CONTRACT_ID),
        ("PAY-003","2026-08-22",6000,"SUP-002",""),
    ]:
        row = normalize({"org_id":ORG,"id":rid,"date":paid,"amount":amount,"supplier":SUPPLIER,
                         "supplier_id":sid,"contract_id":contract})
        rows.append({**row, "source":"内置虚构银行流水", "source_type":"demo"})
    return {"revision":1, "rows":rows, "tasks":[], "created":now()}


def get_document(state, eid):
    if eid in BASE_SOURCES:
        return (*BASE_SOURCES[eid], "内置虚构业务档案")
    if not eid.startswith("PAYMENT."):
        raise FileNotFoundError()
    rid = eid.split(".",1)[1]
    row = next((v for v in state["rows"] if v["id"] == rid), None)
    if not row:
        raise FileNotFoundError()
    excerpt = (f'付款日期 {row["date"]}｜金额 ￥{money(row["amount_cents"]):,.2f}｜收款方 {row["supplier"]}'
               f'｜供应商主键 {row["supplier_id"] or "缺失"}｜合同编号 {row["contract_id"] or "缺失"}')
    return "付款台账来源行", f'流水 {rid}', "付款金额", excerpt, row["source"]


def evidence(state, eid, value):
    title, locator, field, excerpt, source = get_document(state,eid)
    return {"id":eid, "title":title, "locator":locator, "field":field, "value":value, "excerpt":excerpt,
            "source":source, "url":"/api/supervision/evidence?id="+quote(eid,safe=""),
            "sha256":hashlib.sha256(excerpt.encode()).hexdigest()}


def linked(row):
    return (row["contract_id"] == CONTRACT_ID and row["supplier_id"] == SUPPLIER_ID
            and row["date"] >= CONTRACT["signed"])


def row_views(state):
    """Only ordered payment dates are known. Same-day sequencing is never inferred from IDs."""
    sorted_rows = sorted(state["rows"], key=lambda r:(r["date"],r["id"]))
    daily = {}
    for r in sorted_rows:
        if linked(r) and r["date"] < CONTRACT["accepted"]:
            daily.setdefault(r["date"],[]).append(r)
    running = 0
    cumulative_by_day = {}
    contributors = []
    for day, payments in sorted(daily.items()):
        running += sum(p["amount_cents"] for p in payments)
        contributors.extend(p["id"] for p in payments)
        cumulative_by_day[day]=(running,contributors.copy())
    result=[]
    for raw in sorted_rows:
        rid, day = raw["id"], raw["date"]
        amount = money(raw["amount_cents"])
        audits = rid == "PAY-001" and raw["source_type"] == "demo"
        row={**{k:v for k,v in raw.items() if k!="amount_cents"}, "amount":amount,
             "linked":linked(raw), "audited":audits, "audit_ref":"A-001" if audits else "",
             "clue":False, "cap":None, "cumulative":None, "excess":None, "contributors":[],
             "check_state":"待核验", "reason":"缺少匹配的合同或供应商主键，不能仅按名称归并。",
             "evidence":[evidence(state, "PAYMENT."+rid, amount)], "relations":[]}
        if row["linked"]:
            effective, cap, term_id = max(t for t in CONTRACT["terms"] if t[0]<=day)
            row.update(cap=money(cap),cap_effective=effective,acceptance_date=CONTRACT["accepted"])
            row["relations"]=[
                {"from":rid,"to":CONTRACT_ID,"type":"结算合同","basis":"付款行的组织、合同编号及供应商主键匹配"},
                {"from":CONTRACT_ID,"to":SUPPLIER_ID,"type":"合同供方","basis":"合同登记主键"},
                {"from":CONTRACT_ID,"to":"YS-0918","type":"验收登记","basis":"验收登记合同号匹配"},
            ]
            row["evidence"].extend([evidence(state,"CONTRACT-1","40,000.00 元"),
                                    evidence(state,"ACCEPT-1",CONTRACT["accepted"]),
                                    evidence(state,"AMEND-1","2026-09-10 起")])
            row["applied_term"]=term_id
            if day==CONTRACT["accepted"]:
                row["reason"]="与验收同日且无时刻，无法判定先后，需人工查证。"
            elif day>CONTRACT["accepted"]:
                row.update(check_state="验收后", reason="付款晚于验收登记日期，不适用验收前限额。")
            else:
                total, ids = cumulative_by_day[day]
                excess=max(total-cap,0)
                ambiguous=len(daily[day])>1 and excess>0
                clue=excess>0 and not ambiguous
                reason=("同一合同当日多笔付款，日末累计超限但付款先后未确认。" if ambiguous else
                        "验收前累计付款超过付款当日有效合同限额，需核查当期依据。" if clue else
                        "验收前累计付款未超过当日有效限额。")
                row.update(cumulative=money(total),excess=money(excess),contributors=ids,
                           clue=clue, check_state="待核验" if ambiguous else "线索" if clue else "未触发",reason=reason)
        if audits:
            row["evidence"].append(evidence(state,"AUDIT-1","A-001"))
            row["relations"].append({"from":rid,"to":"A-001","type":"关联既有审核","basis":"审核记录明确引用同一流水号"})
        result.append(row)
    return result


def view(state):
    rows=row_views(state)
    audited=sum(r["audited"] for r in rows)
    linked_count=sum(r["linked"] for r in rows)
    clues=[r for r in rows if r["clue"]]
    # One contract's cumulative overrun may appear on several successive payment days.
    maxima={}
    for r in clues:
        maxima[r["contract_id"]] = max(r["excess"],maxima.get(r["contract_id"],0))
    return {"scope":SCOPE,"revision":state["revision"],"rows":rows,"tasks":list(reversed(state["tasks"])),
            "metrics":{"total":len(rows),"amount":money(sum(r["amount_cents"] for r in state["rows"])),
                       "linked":linked_count,"audited":audited,"unlinked":len(rows)-linked_count,
                       "unaudited":len(rows)-audited,"linked_unaudited":linked_count-audited,
                       "clues":len(clues),"excess":sum(maxima.values()),"pending":sum(r["check_state"]=="待核验" for r in rows)},
            "as_of":SCOPE["end"],"scope_note":"本机单组织授权样例；核查角色为演示角色。来源行与示例档案可逐项查看。"}


def load(db, conn):
    rec=conn.execute("SELECT body FROM state WHERE key=?", (KEY,)).fetchone()
    if rec:
        return json.loads(rec[0])
    state=initial()
    db.put(conn,KEY,state)
    return state


def status(db):
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        return view(load(db,conn))


def upsert_rows(state,body):
    source=string(body.get("source"),"导入来源")
    rows=body.get("rows")
    ensure(isinstance(rows,list) and 1<=len(rows)<=1000,"请导入 1 至 1000 笔流水")
    incoming=[normalize(r) for r in rows]
    existing={r["id"]:r for r in state["rows"]}
    added=duplicates=0
    for row in incoming:
        prior=existing.get(row["id"])
        if prior is not None:
            if any(prior[k]!=v for k,v in row.items()):
                raise Conflict("同一组织流水号已存在但字段不同："+row["id"])
            duplicates+=1
            continue
        fresh={**row,"source":source,"source_type":"import"}
        state["rows"].append(fresh)
        existing[row["id"]]=fresh
        added+=1
    ensure(len(state["rows"])<=1500,"本地演示台账最多 1500 行")
    return {"added":added,"duplicates":duplicates},bool(added)


def actor_name(value):
    return string(value,"操作人",60)


def assign_task(state,body):
    event_id=identifier(body.get("event_id"),"付款流水号")
    prev=next((t for t in state["tasks"] if t["event_id"]==event_id),None)
    if prev:
        return {"task_id":prev["id"],"existing":True},False
    owner=actor_name(body.get("owner"))
    actor=actor_name(body.get("actor"))
    ensure(owner!=actor,"派单人和责任人应为不同演示人员")
    due=iso_day(body.get("due"),"反馈期限")
    question=string(body.get("question"),"核查问题",800)
    rows=row_views(state)
    row=next((r for r in rows if r["id"]==event_id),None)
    ensure(row is not None and (row["clue"] or row["check_state"]=="待核验"),"仅线索或待核验记录可以发起核查")
    at=now()
    summary_keys=("id","date","supplier_id","contract_id","amount","source")
    snapshot={"captured_at":at, "revision":state["revision"], "scope":copy.deepcopy(SCOPE), "rule":SCOPE["rule"],
              "row":copy.deepcopy(row),"contributors":[{k:r[k] for k in summary_keys} for r in rows if r["id"] in row["contributors"]]}
    task={"id":f'HCD-{len(state["tasks"])+1:04d}',"event_id":event_id,"status":"待反馈","created":at,
          "owner":owner,"due":due,"question":question,"actor":actor,"snapshot":snapshot,"version":1,
          "history":[{"at":at,"actor":actor,"action":"assign","note":question,"evidence":"",
                      "from_status":"","to_status":"待反馈"}]}
    state["tasks"].append(task)
    return {"task_id":task["id"],"existing":False},True


def audit_request(conn, task_id):
    """Bind a new directed run to the current, still actionable task."""
    ensure(isinstance(task_id, str) and re.fullmatch(r'HCD-\d{4,}', task_id), '无效的核查单号')
    record = conn.execute('SELECT body FROM state WHERE key=?', (KEY,)).fetchone()
    tasks = json.loads(record[0])['tasks'] if record else []
    task = next((t for t in tasks if t['id'] == task_id), None)
    ensure(task is not None, '核查单不存在')
    ensure(task['status'] in ('待反馈','待整改'), '核查单当前状态不能发起定向核验')
    return {'id':task['id'], 'question':task['question'], 'row_id':task['event_id'], 'org_id':ORG}


TRANSITIONS={
    ("待反馈","feedback"):"待复核", ("待复核","return"):"待反馈",
    ("待复核","rectify"):"待整改", ("待复核","dismiss"):"已排除",
    ("待整改","resubmit"):"待复验", ("待复验","return"):"待整改",
    ("待复验","verify"):"已办结",
}


def audit_references(conn, body):
    run_id = body.get("run_id")
    ids = body.get("evidence_ids", [])
    if not run_id and not ids:
        return []
    ensure(isinstance(run_id, str) and re.fullmatch(r"[a-f0-9]{16}",run_id), "选择的审核编号无效")
    ensure(isinstance(ids,list) and 1<=len(ids)<=12 and
           all(isinstance(e,str) and re.fullmatch(r"[A-Z0-9-]{1,40}",e) for e in ids) and
           len(ids)==len(set(ids)), "请选择该审核的 1 至 12 条不同证据")
    ensure(conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='objects'").fetchone(),
           "当前数据库没有已完成的审核记录")
    rec=conn.execute("SELECT body FROM objects WHERE kind='run' AND id=?",(run_id,)).fetchone()
    ensure(rec is not None,"所选审核运行不存在")
    run=json.loads(rec[0])
    ensure(run.get("status") in ("completed","needs_attention"),"只能引用已经结束的审核")
    project=conn.execute("SELECT body FROM objects WHERE kind='project' AND id=?",(run.get("project_id"),)).fetchone()
    ensure(project is not None,"所选审核缺少项目归属")
    org=json.loads(project[0]).get("org_id")
    ensure(org in (None,"",ORG),"审核项目不属于当前组织")
    ensure(org==ORG or body.get("scope_confirmed") is True,"旧项目无组织元数据，请核实并确认其演示组织归属")
    by_id={e["id"]:e for e in run.get("evidence",[])}
    ensure(all(e in by_id for e in ids),"所选证据不属于本次审核")
    from app.report_snapshot import location
    refs=[]
    for eid in ids:
        e=by_id[eid]
        text=str(e.get("text") or "")
        digest=hashlib.sha256(json.dumps(
            {"source_hash":run.get("source_hash",""),"file_sha256":e.get("sha256",""),"page":e.get("page"),
             "id":eid,"text":text},ensure_ascii=False,sort_keys=True).encode()).hexdigest()
        refs.append({"id":eid,"run_id":run_id,"project_id":run["project_id"],"label":str(e.get("name",eid)),
                     "locator":location(e) or "原始材料","excerpt":text[:2000],"sha256":digest,
                     "url":f"/#run/{run_id}/evidence/{eid}","rule_version":run.get("rule_version",""),
                     "scope_basis":"原项目组织主键" if org else "操作人手动确认旧项目归属"})
    return refs


def advance(state,body,conn):
    task=next((t for t in state["tasks"] if t["id"]==body.get("task_id")),None)
    ensure(task is not None,"核查单不存在")
    if "version" in body and body["version"]!=task["version"]:
        raise Conflict("核查单已变化，请刷新后操作")
    actor=actor_name(body.get("actor"))
    action=string(body.get("action"),"操作",16)
    before=task["status"]
    after=TRANSITIONS.get((before,action))
    if not after:
        raise Conflict("当前状态不支持此操作")
    if action in ("feedback","resubmit"):
        ensure(actor==task["owner"],"须由派单责任人提交反馈或整改")
    else:
        ensure(actor!=task["owner"],"反馈/整改责任人不能自行复核、复验或关闭事项")
    note=string(body.get("note"),"处理说明",1200)
    proof=string(body.get("evidence"),"证据编号或可定位的依据说明",800)
    revision=state["revision"]
    event={"at":now(),"actor":actor,"action":action,"note":note,"evidence":proof,
           "from_status":before,"to_status":after,"ledger_revision":revision}
    if body.get("run_id") or body.get("evidence_ids"):
        ensure(action in ("feedback","resubmit"),"仅在核查反馈或整改提交时附审核运行")
        refs=audit_references(conn,body)
        event.update(run_id=refs[0]["run_id"],evidence_ids=[r["id"] for r in refs],audit_evidence=refs)
    if action in ("verify","dismiss","rectify","return"):
        current=next(r for r in row_views(state) if r["id"]==task["event_id"])
        event["observed"]={k:current[k] for k in ("date","cap","cumulative","excess","reason","contributors")}
    task["history"].append(event)
    task.update(status=after,version=task["version"]+1)
    return {"task_id":task["id"],"status":after},True


def mutate(db,action,body):
    ensure(isinstance(body,dict),"请求须为对象")
    ensure(body.get("org_id",ORG)==ORG,"超出演示组织范围")
    operation={"import":upsert_rows,"assign":assign_task,"transition":advance}.get(action)
    if not operation:
        raise FileNotFoundError()
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        state=load(db,conn)
        result,changed=operation(state,body,conn) if action=="transition" else operation(state,body)
        if changed:
            state["revision"]+=1
            db.put(conn,KEY,state)
            db.audit(conn,"监管"+{"import":"导入台账","assign":"发起核查","transition":"核查流转"}[action],
                     json.dumps(result,ensure_ascii=False))
        return {**view(state),"result":result}


def read(handler,path,query):
    ensure(query.get("org_id",[ORG])==[ORG],"超出演示组织范围")
    db=handler.server.compliance_store
    if path=="/state":
        return handler.send(status(db))
    if path=="/template":
        return handler.send(template(),extra={"Content-Disposition":'attachment; filename="supervision-ledger.json"'})
    if path=="/evidence":
        with db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            title,locator,field,excerpt,source=get_document(load(db,conn),query.get("id",[""])[0])
        return handler.send(f"{title}\n来源：{source}\n位置：{locator}\n字段：{field}\n\n{excerpt}",
                            kind="text/plain; charset=utf-8")
    raise FileNotFoundError()


def write(handler,path,body):
    return handler.send(mutate(handler.server.compliance_store,path.removeprefix("/"),body))
