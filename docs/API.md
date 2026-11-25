# 集成本机API v0.7

服务：`http://127.0.0.1:8765`。所有请求只允许匹配本机Host；POST要求`Content-Type: application/json`和`X-Session-Token: 当前会话令牌`（先GET `/api/bootstrap` 获取token）。浏览器Origin必须匹配本机地址，无CORS开放。

| 方法 / 路径 | 输入 / 输出 |
|---|---|
| GET /health | 健康状态 |
| GET /api/compliance/state | 当前草稿、修订号、发布版、业务数据、发布/运行/审计记录 |
| POST /api/compliance/config | `{config,revision}`；保存草稿，revision须与当前一致 |
| POST /api/compliance/publish | `{revision,note}`；兼容检查通过且有变化才发布 |
| POST /api/compliance/import | `{rows,source,revision}`；使用dataset.revision，替换工作数据 |
| POST /api/compliance/run | `{}`；按发布版对工作数据筛查并保存快照 |
| GET /api/compliance/run?id=ID | 运行、配置/业务/结果快照、复核历史 |
| POST /api/compliance/review | `{run_id,result_id,status,actor,note}` |
| GET /api/compliance/export/config | 已发布本体和模型JSON |
| GET /api/compliance/export/data | 当前业务JSON数组 |
| GET /api/compliance/export/csv?run=ID | UTF-8 BOM CSV台账，防公式注入 |
| GET /api/compliance/export/html?run=ID | 可打印HTML报告，文本转义 |
| GET /api/compliance/export/json?run=ID | 完整运行归档 |
| GET /api/compliance/tools | 工具说明 |
| POST /api/compliance/tools/call | `{name,filters?,source?,target?}` |

工具name：`query`、`metrics`、`graph`、`path`。filters支持unit、business_type、supplier精确匹配。path需要图节点ID，先调用graph获得，不拼接猜测。

复核status：pending、confirmed、dismissed、resolved。单维/不完整结果不能confirmed；resolved的前一状态须为confirmed。无命中记录无需研判。原初筛记录不可被复核操作覆盖。

错误：400输入不合法，403来源校验失败，404资源不存在，409修订冲突。冲突时重新GET状态并由用户核对变化，不能静默覆盖。

```bash
curl http://127.0.0.1:8765/api/compliance/tools/call \
  -H 'Content-Type: application/json' \
  -H 'X-Session-Token: <从 /api/bootstrap 获取的 token>' \
  --data '{"name":"metrics","filters":{}}'
```

仅为单用户本机联调接口，不提供测试账号或生产鉴权。新增合规模块不调用外部模型；原材料上传、影像、模型审核接口保留。

## 原件事实桥接

POST `/api/compliance/capture` 接受 `{row,source_run,evidence_ids,actor,note,revision,replace_demo}`。row为完整业务字段，revision使用dataset.revision。首次替换内置虚构数据必须明确replace_demo=true；后续追加时拒绝重复业务单号。原件引用必须存在且来源运行已完成材料处理。记录来源、摘要和确认依据。逐字段引用保存在 row.provenance.field_evidence，关系引用保存在 relation_evidence，evidence_index 保存原文位置。验收为 null 表示尚待核实。导入业务中的provenance引用也必须通过同样的来源检查。

原接口 `/api/upload`、`/api/runs/{id}`、`/api/runs/{id}/report`、原件页图片/下载接口保持原有路径和会话要求。证据深链接为 `/#run/{id}/evidence/{evidence_id}`。

## 统计模型 API（v0.7）

同一会话令牌与同源校验。前缀 `/api/compliance/lab`：

- GET `/state`：草稿、发布模型、版本、历史数据和最近评测。
- POST `/save`：`revision, models`；POST `/publish`：`revision, note`。
- POST `/import`：`revision, source, rows`，替换当前历史数据。
- POST `/run`：`model_id, start, end`（YYYY-MM，最多12个月）。
- GET `/run?id=…`、`/export?id=…`：历史评测及快照。
- GET `/data`：当前历史数据 JSON。
- POST `/label`：`run_id, result_id, label`（bool）、`actor, note`。

过期修订返回409；参数错误400；未知评测404。统计模型评测结果不自动改写三维风险结果或正式审核结论。

## v0.7 知识、诊断与图谱来源

- GET `/api/knowledge/chunks?id=…`：原文与切片（稳定内容摘要、字符起止、版本、索引）。
- POST `/api/knowledge/chunks`：`id, revision, config:{size,overlap,mode}`；size 100—2000，overlap小于size一半；mode为paragraph/fixed。
- POST `/api/knowledge/recall`：`query, top_k, method`，method支持bm25/tfidf/hybrid；仅召回已发布知识，保留匹配词和分数；TF-IDF不宣称为语义Embedding。
- GET/POST `/api/model/diagnostic`：GET仅检查模式/可执行文件，POST另调用`codex login status`；不读取密钥、不返回原始登录日志、不触发推理。
- GET/POST `/api/compliance/graph-data`：读取或导入独立来源图谱；POST为`revision,source,rows`。历史修订保存在数据库中。
- GET `/api/compliance/graph-template`：带明确来源标识的JSON演示模板。
- POST `/api/compliance/graph-scenario`：`revision`，将当前独立图谱切换为内置演示场景。

图谱每条记录必须包含id/name/supplier/bank_account/source_kind/source_note。source_kind为source（材料待核实）、synthetic（演示生成）或anonymized（匿名化）；amount允许null，person缺失时不构造虚假共同经办人。原业务记录与原审核结论保持独立。


## 多场景审核与监管（v0.8）

- `GET /api/schemes` 返回可用于新项目的发布版；`GET /api/schemes/manage`、`GET /api/schemes/{id}` 提供草稿、修订号、试跑结果和版本历史。
- `POST /api/schemes/{id}/save` 传 `revision,draft`，`/trial` 可传带预期的 `samples`，`/publish` 传 `revision,note`。服务校验修订号及当前样本的实际输出；变更的启用项须受试跑覆盖。
- `POST /api/upload` 支持原 ZIP，也可传单文件字节并设置 URL 编码的 `X-Filename`。新项目可传 `X-Scheme-Id`，已有项目传 `X-Project-Id`，可选 `X-Business-Id`。补证、纠错和重试从原审核发起，继承业务标识、项目和方案版本。历史缺省方案为财务 T1 v1。
- 知识管理和测试召回支持 `scheme_id`，GET 使用查询参数，POST 使用 JSON。费用、合同和载具分开检索；真实审核只使用方案范围内发布的知识并冻结引用。
- `GET /api/supervision/state` 返回当前组织、口径、指标、贡献流水及核查事项。模板下载为 `GET /api/supervision/template`，来源记录在 `GET /api/supervision/evidence?id=...`。
- `POST /api/supervision/import` 导入流水；`/assign` 派发核查；`/transition` 记录反馈、退回、整改、排除、复验。反馈可附 `run_id,evidence_ids` 引用审核证据。缺组织标识的项目须由经办人明确确认归属。
- 以 `--summary-only` 启动的入口开放 `/`、`/health` 和 `/api/supervision/summary`。只返回聚合指标，拒绝全部明细与原件访问。业务端的写操作仍须有效的本机会话与同源请求。

定向合同核验在上传时附带 `X-Supervision-Task` 核查单号。服务校验任务状态、项目组织和方案，并在运行中保存 `supervision_task` 问题与付款流水号。补证和重试继承此引用；责任人选择证据后提交核验反馈。
