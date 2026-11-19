# 原财务审核系统接入约定

## 当前阻碍

本次可下载压缩包约23MiB，实际为PPT构建资料、启动器和Git对象。检查所有可达与悬空tree后，未发现应用源码、requirements或原测试。启动器引用`outputs/financial-audit/start.ps1`，但整个financial-audit目录不在包内。因此没有覆盖、替换或声称修改原代码。

请补充 `outputs/financial-audit` 源码目录，保留应用、前端、规则、schema、tests、requirements、README、启动脚本和`.env.example`。去掉 `.venv`、模型权重、缓存、大型运行资料、真实密钥和业务原件。原来的参考对话只用来理解产品设计，历史测试数量不算本版本验证结果。

## 建议接入路径

1. 先按原README运行旧版回归，确认上传、OCR、65条审核、证据、StartLux、报告和安全检查仍成立。
2. 保留原四入口“开始审核、项目管理、审核中心、运营中心”。风险模型与本体配置挂在审核中心，风险研判和图谱挂在运营中心。独立演示模块的导航仅为临时载体。
3. 将原项目识别且经校验的事实转换成`validate_events()`要求的字段。业务ID与凭证ID保持稳定；未知金额/日期/验收状态不能补造默认值。缺失数据应进入待复核队列，在字段补齐后再做本版本的完整筛查。
4. 将原证据引用（文件ID、页码、区域框、置信度）扩展进持久化的evidence schema。当前代码只保存结构化字段和document编号，不具备坐标定位。
5. 复用`domain.evaluate()`作为纯计算服务，业务层负责快照、版本与审计。原应用如果已有数据库，不应各自维护两套人工结论，应迁移本扩展Store到现有事务层。
6. 原AI审核与独立决策模型仍负责各自语义任务。本版本规则/统计引擎没有模型调用，不替代OCR、主模型或StartLux。原单据审核结论和跨业务风险状态分开显示。
7. 接入原PDF生成器，将不可变run与review数据写入报告；不通过聊天文字直接改判。
8. 接入现有认证、权限、字段脱敏、API网关与部署流程后，才评估远程服务开放。当前绑定127.0.0.1的服务不作为生产服务暴露。

## 可复用接口

- `app.domain.validate_config(config)`：验证本体和模型。
- `app.domain.impact(old,new)`：检查发布差异及兼容性。
- `app.domain.validate_events(rows,ontology)`：应用来源映射并校验业务数据。
- `app.domain.evaluate(events,config)`：确定性风险结果。
- `app.domain.build_graph(events)` / `graph_path(graph,source,target)`：关系联查。
- `app.store.Store`：独立演示持久化参考实现。

完整HTTP约定见API.md。集成后应新增原材料→识别事实→本体映射→风险结果→原件追溯的端到端测试，再交付“原系统微调版”。
