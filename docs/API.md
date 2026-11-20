# 集成本机API v0.5

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

POST `/api/compliance/capture` 接受 `{row,source_run,evidence_ids,actor,note,revision,replace_demo}`。row为完整业务字段，revision使用dataset.revision。首次替换内置虚构数据必须明确replace_demo=true；后续追加时拒绝重复业务单号。原件引用必须存在且来源运行已完成材料处理。记录来源、摘要和确认依据。导入业务中的provenance引用也必须通过同样的来源检查。

原接口 `/api/upload`、`/api/runs/{id}`、`/api/runs/{id}/report`、原件页图片/下载接口保持原有路径和会话要求。证据深链接为 `/#run/{id}/evidence/{evidence_id}`。
