"""Transactional SQLite persistence, immutable run/config snapshots and audit trail."""
import copy
import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .domain import evaluate, impact, require, text, validate_config, validate_events
from .seed import demo_events, initial_config


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def encode(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


class Conflict(ValueError):
    pass


class Store:
    def __init__(self, path):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS versions (id INTEGER PRIMARY KEY AUTOINCREMENT, created TEXT NOT NULL, note TEXT NOT NULL, body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, created TEXT NOT NULL, version INTEGER NOT NULL, body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS reviews (id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL, result_id TEXT NOT NULL, created TEXT NOT NULL, status TEXT NOT NULL, actor TEXT NOT NULL, note TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS audit (id INTEGER PRIMARY KEY AUTOINCREMENT, created TEXT NOT NULL, action TEXT NOT NULL, detail TEXT NOT NULL);
            """)
            if not db.execute("SELECT 1 FROM state WHERE key='draft'").fetchone():
                config = initial_config()
                db.execute("INSERT INTO versions(created,note,body) VALUES(?,?,?)", (now(), "初始演示配置", encode(config)))
                self.put(db, "draft", {"revision": 1, "config": config})
                self.put(db, "active", 1)
                self.put(db, "events", {"revision": 1, "source": "内置虚构演示数据", "rows": demo_events()})
                self.audit(db, "初始化", "载入虚构业务和初始配置")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def get(db, key):
        return json.loads(db.execute("SELECT body FROM state WHERE key=?", (key,)).fetchone()[0])

    @staticmethod
    def put(db, key, body):
        db.execute("INSERT OR REPLACE INTO state(key,body) VALUES(?,?)", (key, encode(body)))

    @staticmethod
    def audit(db, action, detail):
        db.execute("INSERT INTO audit(created,action,detail) VALUES(?,?,?)", (now(), action, detail))

    def state(self):
        with self.connect() as db:
            active = self.get(db, "active")
            config = json.loads(db.execute("SELECT body FROM versions WHERE id=?", (active,)).fetchone()[0])
            draft = self.get(db, "draft")
            return {"active_version": active, "active_config": config, "draft": draft, "impact": impact(config, draft["config"]), "dataset": self.get(db, "events"), "versions": [dict(r) for r in db.execute("SELECT id,created,note FROM versions ORDER BY id DESC")], "runs": [dict(r) for r in db.execute("SELECT id,created,version FROM runs ORDER BY created DESC,rowid DESC LIMIT 100")], "audit": [dict(r) for r in db.execute("SELECT * FROM audit ORDER BY id DESC LIMIT 100")]}

    def save_config(self, config, revision):
        validated = validate_config(config)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            current = self.get(db, "draft")
            if revision != current["revision"]:
                raise Conflict("配置已被其他页面更新，请刷新后重试")
            self.put(db, "draft", {"revision": revision + 1, "config": validated})
            self.audit(db, "保存草稿", f"草稿修订 {revision+1}")
        return self.state()

    def publish(self, revision, note):
        text(note, "发布说明", 500)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            draft = self.get(db, "draft")
            if revision != draft["revision"]:
                raise Conflict("配置草稿已变化，请刷新并重新检查影响")
            config = validate_config(draft["config"])
            old = json.loads(db.execute("SELECT body FROM versions WHERE id=?", (self.get(db, "active"),)).fetchone()[0])
            delta = impact(old, config)
            require(delta["compatible"], "发布被阻止：" + "；".join(delta["breaking"]))
            require(delta["changes"], "配置与已发布版本相同，无需重复发布")
            cursor = db.execute("INSERT INTO versions(created,note,body) VALUES(?,?,?)", (now(), note, encode(config)))
            self.put(db, "active", cursor.lastrowid)
            self.put(db, "draft", {"revision": revision + 1, "config": config})
            self.audit(db, "发布配置", f"v{cursor.lastrowid}：{note}")
        return self.state()

    def import_events(self, rows, source, revision):
        text(source, "数据来源", 160)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            old = self.get(db, "events")
            if old["revision"] != revision:
                raise Conflict("业务数据已更新，请刷新后重新导入")
            config = json.loads(db.execute("SELECT body FROM versions WHERE id=?", (self.get(db, "active"),)).fetchone()[0])
            validated = validate_events(rows, config["ontology"])
            self.put(db, "events", {"revision": revision + 1, "source": source, "rows": validated})
            self.audit(db, "导入业务", f"{len(validated)} 笔；来源：{source}；数据修订 {revision+1}")
        return self.state()

    def run(self):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            version = self.get(db, "active")
            config = json.loads(db.execute("SELECT body FROM versions WHERE id=?", (version,)).fetchone()[0])
            dataset = self.get(db, "events")
            require(any(m["enabled"] and any(r["enabled"] for r in m["rules"]) for m in config["models"]), "没有启用的有效模型规则，请配置并发布")
            results = evaluate(dataset["rows"], config)
            require(results, "启用模型的业务范围与当前数据不匹配")
            run = {"id": uuid.uuid4().hex[:16], "created": now(), "version": version, "dataset": copy.deepcopy(dataset), "config": config, "results": results}
            db.execute("INSERT INTO runs(id,created,version,body) VALUES(?,?,?,?)", (run["id"], run["created"], version, encode(run)))
            self.audit(db, "执行筛查", f"运行 {run['id']}；配置 v{version}；{len(results)} 条模型结果")
        return self.get_run(run["id"])

    def get_run(self, id_):
        with self.connect() as db:
            row = db.execute("SELECT body FROM runs WHERE id=?", (id_,)).fetchone()
            if not row:
                raise LookupError("运行记录不存在")
            run = json.loads(row[0])
            run["reviews"] = [dict(r) for r in db.execute("SELECT * FROM reviews WHERE run_id=? ORDER BY id", (id_,))]
            return run

    def review(self, run_id, result_id, status, actor, note):
        text(actor, "复核人", 60)
        text(note, "复核依据", 1000)
        require(status in ("pending", "confirmed", "dismissed", "resolved"), "不支持的研判状态")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT body FROM runs WHERE id=?", (run_id,)).fetchone()
            require(row is not None, "运行不存在")
            result = next((r for r in json.loads(row[0])["results"] if r["id"] == result_id), None)
            require(result is not None, "模型结果不存在")
            require(result["status"] != "clear", "未命中记录无需研判")
            if status == "confirmed":
                require(len(result["dimensions"]) >= 2, "单维线索或未完成核验不可直接确认风险，请补证并重新筛查")
            previous = db.execute("SELECT status FROM reviews WHERE run_id=? AND result_id=? ORDER BY id DESC LIMIT 1", (run_id, result_id)).fetchone()
            if status == "resolved":
                require(previous is not None and previous[0] == "confirmed", "整改关闭前须先确认风险")
            db.execute("INSERT INTO reviews(run_id,result_id,created,status,actor,note) VALUES(?,?,?,?,?,?)", (run_id, result_id, now(), status, actor, note))
            self.audit(db, "人工研判", f"{actor}：{run_id}/{result_id} → {status}；{note}")
        return self.get_run(run_id)
