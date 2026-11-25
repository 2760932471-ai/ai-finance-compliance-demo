import copy
import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

from compliance import supervision as sv
from compliance.store import Conflict, Store


class SupervisionTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.db=Store(Path(tmp.name)/"isolated.sqlite")

    def import_rows(self, rows):
        return sv.mutate(self.db,"import",{"source":"测试导入","rows":rows})

    def row(self,id,paid="2026-08-01",amount=2000,**extra):
        return {"id":id,"org_id":sv.ORG,"date":paid,"amount":amount,"supplier":sv.SUPPLIER,
                "supplier_id":sv.SUPPLIER_ID,"contract_id":sv.CONTRACT_ID,**extra}

    def assign(self):
        return sv.mutate(self.db,"assign",{"event_id":"PAY-002","actor":"监管岗","owner":"经办甲",
                         "due":"2026-12-01","question":"请核实提前付款授权"})

    def move(self,action,owner=False,**extra):
        return sv.mutate(self.db,"transition",{"task_id":"HCD-0001","actor":"经办甲" if owner else "复核乙",
                         "action":action,"note":"已核对付款日生效文件","evidence":"核查登记表第1行",**extra})

    def test_ledger_coverage_and_event_date_evidence(self):
        state=sv.status(self.db)
        m=state["metrics"]
        self.assertEqual((m["total"],m["amount"],m["audited"],m["linked_unaudited"],m["unlinked"]),(3,61000,1,1,1))
        self.assertEqual((m["clues"],m["excess"]),(1,15000))
        a,b,c=state["rows"]
        self.assertFalse(b["audited"])
        self.assertEqual((b["contributors"],b["cap"],b["cumulative"],b["excess"]),(["PAY-001","PAY-002"],40000,55000,15000))
        self.assertEqual(b["applied_term"],"CONTRACT-1")
        self.assertFalse(c["linked"])
        self.assertEqual(c["supplier"],b["supplier"])
        self.assertNotEqual(c["supplier_id"],b["supplier_id"])
        for record in (a,b):
            for ref in record["evidence"]:
                uri=urlsplit(ref["url"])
                send=lambda output,**kw: (output,kw["kind"])
                h=SimpleNamespace(server=SimpleNamespace(compliance_store=self.db),send=send)
                body,kind=sv.read(h,"/evidence",parse_qs(uri.query))
                self.assertIn(ref["excerpt"],body)
                self.assertIn("text/plain",kind)

    def test_conflict_rolls_back_and_duplicate_import_is_noop(self):
        row=self.row("P-A")
        state=self.import_rows([row,row])
        self.assertEqual(state["result"],{"added":1,"duplicates":1})
        version=state["revision"]
        state=self.import_rows([row])
        self.assertEqual(state["revision"],version)
        with self.assertRaises(Conflict): self.import_rows([self.row("P-B"),dict(row,amount=2001)])
        self.assertEqual(sv.status(self.db)["metrics"]["total"],4)
        for change in ({"org_id":"OTHER"},{"date":"2026-10-01"},{"amount":0.001},{"currency":"USD"},{"audited":True}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.import_rows([dict(row,**change)])
        self.assertEqual(sv.status(self.db)["revision"],version)

    def test_concurrent_assigns_produce_one_frozen_case(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            results=list(pool.map(lambda _:self.assign()["result"], range(4)))
        self.assertEqual(sum(not r["existing"] for r in results),1)
        self.assertEqual({r["task_id"] for r in results},{"HCD-0001"})
        self.assertEqual(len(sv.status(self.db)["tasks"][0]["history"]),1)

    def test_later_agreement_and_same_day_order_unknown(self):
        rows=self.import_rows([self.row("P-LATE","2026-09-11",1000),self.row("P-ACCEPT","2026-09-18")])["rows"]
        by_id={r["id"]:r for r in rows}
        self.assertTrue(by_id["PAY-002"]["clue"])
        self.assertEqual((by_id["P-LATE"]["cap"],by_id["P-LATE"]["cumulative"],by_id["P-LATE"]["check_state"]),(60000,56000,"未触发"))
        self.assertEqual(by_id["P-ACCEPT"]["check_state"],"待核验")
        same_day=self.import_rows([self.row("P-SAME","2026-08-20",1)])
        self.assertEqual(next(r for r in same_day["rows"] if r["id"]=="PAY-002")["check_state"],"待核验")

    def test_task_history_snapshots_resubmission_and_separation(self):
        case=self.assign()
        task=case["tasks"][0]
        snap=copy.deepcopy(task["snapshot"])
        self.assertTrue(self.assign()["result"]["existing"])
        self.assertEqual(len(sv.status(self.db)["tasks"]),1)
        self.import_rows([self.row("P-NEW")])
        self.assertEqual(snap["row"]["excess"],15000)
        with self.assertRaises(ValueError): self.move("feedback")
        with self.assertRaises(Conflict): self.move("verify")
        self.move("feedback",True)
        self.move("return")
        self.move("feedback",True)
        self.move("rectify")
        self.move("resubmit",True)
        with self.assertRaises(ValueError): self.move("verify",True)
        with self.assertRaises(Conflict): self.move("verify",version=1)
        self.move("return")
        self.move("resubmit",True)
        result=self.move("verify")
        finished=result["tasks"][0]
        self.assertEqual(finished["snapshot"],snap)
        self.assertEqual(finished["status"],"已办结")
        self.assertEqual(finished["history"][-1]["observed"]["excess"],17000)
        self.assertEqual(len(finished["history"]),9)
        with self.assertRaises(Conflict): self.move("dismiss")
        self.assertEqual(sv.status(self.db)["tasks"][0]["history"],finished["history"])

    def test_existing_audit_evidence_binding_validates_origin_and_freezes_excerpt(self):
        case=self.assign()
        rid="abcdef0123456789"
        def put(kind,obj):
            with self.db.connect() as c:
                c.execute("CREATE TABLE IF NOT EXISTS objects (kind TEXT,id TEXT,body TEXT, PRIMARY KEY(kind,id))")
                c.execute("INSERT OR REPLACE INTO objects VALUES(?,?,?)",(kind,obj["id"],json.dumps(obj)))
        project={"id":"p1","org_id":"OTHER"}
        run={"id":rid,"status":"completed","project_id":"p1","source_hash":"source","evidence":[
            {"id":"D001-P1","name":"演示凭证","page":1,"sha256":"document","text":"核查原文"}]}
        put("project",project);put("run",run)
        ref={"run_id":rid,"evidence_ids":["D001-P1"],"scope_confirmed":True}
        with self.assertRaises(ValueError): self.move("feedback",True,**ref)
        put("project",{"id":"p1"})
        with self.assertRaises(ValueError): self.move("feedback",True,**dict(ref,scope_confirmed=False))
        with self.assertRaises(ValueError): self.move("feedback",True,**dict(ref,evidence_ids=["D001-P2"]))
        task=self.move("feedback",True,**ref)["tasks"][0]
        evidence=task["history"][-1]["audit_evidence"][0]
        self.assertEqual(evidence["url"],f"/#run/{rid}/evidence/D001-P1")
        run["evidence"][0]["text"]="后续修改";put("run",run)
        self.assertEqual(sv.status(self.db)["tasks"][0]["history"][-1]["audit_evidence"][0]["excerpt"],"核查原文")


if __name__=="__main__":
    unittest.main()
