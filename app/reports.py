"""Portable exports of the actual persisted run; all user strings are escaped."""
import csv
import html
import io

STATUS = {"cross": "交叉风险待研判", "clue": "单维待核验线索", "clear": "未命中", "incomplete": "核验不完整"}
REVIEW = {"pending": "待核验", "confirmed": "人工确认风险", "dismissed": "已排除", "resolved": "整改关闭"}


def latest_reviews(run):
    return {r["result_id"]: r for r in run["reviews"]}


def safe_cell(value):
    s = str(value)
    return "'" + s if s.lstrip().startswith(("=", "+", "-", "@")) or s.startswith(("\t", "\r", "\n")) else s


def csv_report(run):
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(["业务单号", "业务名称", "单位", "供应商", "金额", "模型", "配置版本", "分值", "等级", "初筛状态", "命中维度", "人工研判", "复核人", "复核依据"])
    reviews = latest_reviews(run)
    for result in run["results"]:
        event, review = result["event"], reviews.get(result["id"], {})
        writer.writerow([safe_cell(v) for v in [event["id"], event["name"], event["unit"], event["supplier"], event["amount"], result["model_name"], run["version"], result["score"], result["level"], STATUS[result["status"]], "/".join(result["dimensions"]), REVIEW.get(review.get("status"), "未研判"), review.get("actor", ""), review.get("note", "")]])
    return ("\ufeff" + output.getvalue()).encode("utf-8")


def html_report(run):
    esc = lambda x: html.escape(str(x), quote=True)
    parts = []
    reviews = latest_reviews(run)
    for r in run["results"]:
        event, review = r["event"], reviews.get(r["id"], {})
        checks = "".join(f"<tr><td>{esc(c['name'])}</td><td>{'命中' if c['hit'] else '未命中' if c['evaluated'] else '未评估'}</td><td>{esc(c['explanation'])}</td></tr>" for c in r["checks"])
        parts.append(f"<section><h2>{esc(event['id'])} · {esc(event['name'])}</h2><p>{esc(event['unit'])} / {esc(event['supplier'])} / ¥{event['amount']:,.2f}</p><p>模型：{esc(r['model_name'])} · {esc(STATUS[r['status']])} · {esc(r['level'])} · {r['score']} 分</p><table><thead><tr><th>规则</th><th>结果</th><th>事实与计算依据</th></tr></thead><tbody>{checks}</tbody></table><p>人工研判：{esc(REVIEW.get(review.get('status'), '未研判'))}；复核人：{esc(review.get('actor', '—'))}；依据：{esc(review.get('note', '—'))}</p></section>")
    return ("<!doctype html><html lang='zh-CN'><meta charset='utf-8'><title>合规筛查研判报告</title><style>body{font:15px/1.7 system-ui,sans-serif;color:#17243c;max-width:1000px;margin:40px auto;padding:0 24px}h1{font-size:30px}h2{font-size:20px}table{border-collapse:collapse;width:100%}td,th{border:1px solid #dbe1eb;padding:10px;text-align:left}td{overflow-wrap:anywhere}section{margin:28px 0;break-inside:avoid}aside{padding:16px;background:#f1f5f9} @media print{body{margin:0;max-width:none;font-size:11pt}section{break-inside:auto}tr{break-inside:avoid}h2{break-after:avoid}@page{size:A4;margin:18mm}}</style><h1>业财智能合规 · 筛查研判报告</h1>"
            + f"<p>运行 {esc(run['id'])} · 配置 v{run['version']} · UTC {esc(run['created'])}</p><aside>数据来源：{esc(run['dataset']['source'])}。本报告为演示筛查与人工研判记录，不代表审计定论。单维命中不直接定性为风险；统计筛查未进行生产准确率评测。可通过浏览器打印保存为 PDF。</aside>" + "".join(parts) + "</html>").encode("utf-8")
