# 本机API v1

服务：`http://127.0.0.1:8765`。所有请求只允许匹配本机Host；POST要求`Content-Type: application/json`和`X-Demo-Request: 1`。浏览器Origin必须匹配本机地址，无CORS开放。

| 方法 / 路径 | 输入 / 输出 |
|---|---|
| GET /health | 健康状态 |
| GET /api/state | 当前草稿、修订号、发布版、业务数据、发布/运行/审计记录 |
| POST /api/config | `{config,revision}`；保存草稿，revision须与当前一致 |
| POST /api/publish | `{revision,note}`；兼容检查通过且有变化才发布 |
| POST /api/import | `{rows,source,revision}`；使用dataset.revision，替换工作数据 |
| POST /api/run | `{}`；按发布版对工作数据筛查并保存快照 |
| GET /api/run?id=ID | 运行、配置/业务/结果快照、复核历史 |
| POST /api/review | `{run_id,result_id,status,actor,note}` |
| GET /api/export/config | 已发布本体和模型JSON |
| GET /api/export/data | 当前业务JSON数组 |
| GET /api/export/csv?run=ID | UTF-8 BOM CSV台账，防公式注入 |
| GET /api/export/html?run=ID | 可打印HTML报告，文本转义 |
| GET /api/export/json?run=ID | 完整运行归档 |
| GET /api/tools | 工具说明 |
| POST /api/tools/call | `{name,filters?,source?,target?}` |

工具name：`query`、`metrics`、`graph`、`path`。filters支持unit、business_type、supplier精确匹配。path需要图节点ID，先调用graph获得，不拼接猜测。

复核status：pending、confirmed、dismissed、resolved。单维/不完整结果不能confirmed；resolved的前一状态须为confirmed。无命中记录无需研判。原初筛记录不可被复核操作覆盖。

错误：400输入不合法，403来源校验失败，404资源不存在，409修订冲突。冲突时重新GET状态并由用户核对变化，不能静默覆盖。

```bash
curl http://127.0.0.1:8765/api/tools/call \
  -H 'Content-Type: application/json' \
  -H 'X-Demo-Request: 1' \
  --data '{"name":"metrics","filters":{}}'
```

仅为单用户本机联调接口，不提供测试账号或生产鉴权。没有外部通信、影像上传接口或任意文件路径参数。
