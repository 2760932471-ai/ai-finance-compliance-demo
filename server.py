#!/usr/bin/env python3
"""Local-only demonstration server. Run: python server.py --port 8765."""
import argparse
import json
import logging
import mimetypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from app.domain import build_graph, graph_path, require
from app.reports import csv_report, html_report
from app.store import Conflict, Store, encode

ROOT = Path(__file__).resolve().parent
STATIC = {"/": "index.html", "/app.js": "app.js", "/style.css": "style.css"}


class Handler(BaseHTTPRequestHandler):
    server_version = "FinanceDemo/1.0"

    def log_message(self, format_, *args):
        # Avoid reflecting arbitrary request paths or uploaded values to logs.
        pass

    def reply(self, status, body, content_type="application/json; charset=utf-8", filename=None):
        if not isinstance(body, bytes):
            body = encode(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; form-action 'self'; base-uri 'none'")
        if filename:
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.end_headers()
        self.wfile.write(body)

    def trusted(self):
        port = self.server.server_address[1]
        hosts = {f"localhost:{port}", f"127.0.0.1:{port}"}
        require(self.headers.get("Host") in hosts, "Host 不受信任；请从本机 localhost 地址访问")
        origin = self.headers.get("Origin")
        require(origin is None or origin in {f"http://{h}" for h in hosts}, "禁止跨站请求")

    def do_GET(self):
        try:
            self.trusted()
            url = urlsplit(self.path)
            query = parse_qs(url.query)
            path, store = url.path, self.server.store
            if path in STATIC:
                file = ROOT / "static" / STATIC[path]
                mime = mimetypes.guess_type(file.name)[0] or "text/plain"
                return self.reply(200, file.read_bytes(), mime + "; charset=utf-8")
            if path == "/health":
                return self.reply(200, {"status": "ok", "mode": "local-demo"})
            if path == "/api/state":
                return self.reply(200, store.state())
            if path == "/api/run":
                return self.reply(200, store.get_run(query.get("id", [""])[0]))
            if path == "/api/export/config":
                return self.reply(200, store.state()["active_config"], filename="ontology-and-models.json")
            if path == "/api/export/data":
                return self.reply(200, store.state()["dataset"]["rows"], filename="business-data.json")
            if path.startswith("/api/export/"):
                run = store.get_run(query.get("run", [""])[0])
                format_ = path.removeprefix("/api/export/")
                if format_ == "csv":
                    return self.reply(200, csv_report(run), "text/csv; charset=utf-8", "risk-ledger.csv")
                if format_ == "html":
                    return self.reply(200, html_report(run), "text/html; charset=utf-8")
                if format_ == "json":
                    return self.reply(200, run, filename="run-snapshot.json")
            if path == "/api/tools":
                return self.reply(200, {"version": "1.0", "transport": "HTTP JSON, loopback only", "tools": [{"name": "query", "description": "当前数据按单位、业务类型、供应商精确筛选"}, {"name": "metrics", "description": "业务数量、金额合计、平均金额"}, {"name": "graph", "description": "供应商—账户—业务—经办关系与度中心性"}, {"name": "path", "description": "无向关系图最短路径（须传 source/target 节点 ID）"}], "limitations": "原始影像调阅、OCR、模型推理、gRPC、生产鉴权待接入；此接口仅为本机演示"})
            self.reply(404, {"error": "资源不存在"})
        except LookupError as exc:
            self.reply(404, {"error": str(exc)})
        except ValueError as exc:
            self.reply(403, {"error": str(exc)})
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            logging.exception("GET failed")
            self.reply(500, {"error": "服务内部错误，请检查本地日志"})

    def do_POST(self):
        try:
            try:
                self.trusted()
                require(self.headers.get("X-Demo-Request") == "1", "缺少本机请求校验头")
            except ValueError as exc:
                return self.reply(403, {"error": str(exc)})
            require(self.headers.get("Content-Type", "").split(";")[0].strip() == "application/json", "仅接受 application/json")
            require(not self.headers.get("Transfer-Encoding"), "不支持分块请求")
            size = int(self.headers.get("Content-Length", "0"))
            require(0 < size <= 2_000_000, "请求大小须在1字节至2MB之间")
            self.connection.settimeout(10)
            raw = self.rfile.read(size)
            require(len(raw) == size, "请求内容不完整")
            def reject_constant(value):
                raise ValueError("JSON 不允许非有限数值")
            data = json.loads(raw, parse_constant=reject_constant)
            require(isinstance(data, dict), "请求必须为 JSON 对象")
            path, store = urlsplit(self.path).path, self.server.store
            if path == "/api/config":
                result = store.save_config(data["config"], data["revision"])
            elif path == "/api/publish":
                result = store.publish(data["revision"], data["note"])
            elif path == "/api/import":
                result = store.import_events(data["rows"], data["source"], data["revision"])
            elif path == "/api/run":
                result = store.run()
            elif path == "/api/review":
                result = store.review(data["run_id"], data["result_id"], data["status"], data["actor"], data["note"])
            elif path == "/api/tools/call":
                rows = store.state()["dataset"]["rows"]
                name = data.get("name")
                filters = data.get("filters", {})
                require(isinstance(filters, dict) and set(filters) <= {"unit", "business_type", "supplier"}, "不支持的筛选字段")
                rows = [r for r in rows if all(r[k] == v for k, v in filters.items())]
                if name == "query":
                    result = {"rows": rows, "count": len(rows), "source": "当前业务数据集"}
                elif name == "metrics":
                    total = sum(r["amount"] for r in rows)
                    result = {"count": len(rows), "total_amount": round(total, 2), "average_amount": round(total/len(rows), 2) if rows else 0, "definition": "按业务记录逐笔合计支付金额；不合并供应商"}
                elif name in ("graph", "path"):
                    graph = build_graph(rows)
                    result = graph if name == "graph" else {"path": graph_path(graph, data.get("source"), data.get("target"))}
                else:
                    raise ValueError("工具名称不存在")
            else:
                return self.reply(404, {"error": "接口不存在"})
            self.reply(200, result)
        except Conflict as exc:
            self.reply(409, {"error": str(exc)})
        except (ValueError, KeyError, TypeError, AttributeError, RecursionError) as exc:
            message = str(exc) if isinstance(exc, ValueError) and not isinstance(exc, json.JSONDecodeError) else "请求结构错误，请检查字段与 JSON 格式"
            self.reply(400, {"error": message})
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            logging.exception("POST failed")
            self.reply(500, {"error": "服务内部错误，请检查本地日志"})


def create_server(port=8765, database=None):
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.store = Store(database or ROOT / ".local" / "demo.sqlite3")
    return server


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AI财务审核 · 合规演示扩展")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--database", type=Path)
    args = parser.parse_args()
    httpd = create_server(args.port, args.database)
    print(f"AI财务审核演示已启动：http://127.0.0.1:{httpd.server_address[1]}\n仅本机访问，按 Ctrl+C 停止。", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
