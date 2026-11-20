# 财务费用审核 V0.1 规格与验收

本项目按2026年10月比赛T1要求建设。本地运行，监听127.0.0.1，无Dify；原件本地保存，审核所需内容通过Codex调用传给模型服务。界面包含开始审核、项目管理、审核中心、运营中心。

## 核心约束

- 以原始ZIP为输入；保留原件，安全解包，忽略系统文件，恢复中文路径，计算SHA-256；同内容去重但保留全部条目。
- PDF文本、扫描PDF、JPG/PNG、HTML静态正文识别。中文OCR优先调用Windows自带OCR，缺失时明确报错；原HTML绝不在浏览器执行。
- 规则来自比赛HTML内嵌65条，分组13/15/22/8/7。风险级别仅复制原文明确指定项，其他标未指定。
- 每条结果只有通过/不通过/待人工判断；每条含事实、依据、真实证据ID或材料缺失说明。技术执行状态与业务判定分开。
- 无模型配置仍可运行真实材料治理、本地OCR和程序规则；语义、视觉及外部核验不可冒充完成。
- 人工确认、字段订正、改判必须留痕；AI原判与人工终判分开。重跑创建独立批次，不覆盖历史。
- 项目汇总：不通过优先，其次待人工判断；全部通过且无技术失败才允许通过建议。P0阻断不得绕过。
- HTML报告自包含关键证据预览，保存报告快照。每次生成对应一个运行及复核版本。
- 知识须发布才参与检索；规则全集不依赖检索。项目证据与制度知识隔离。
- 反馈形成待审核优化候选，不自动改规则、训练或发布知识。

## 数据与接口

Project(id,name,created_at,archived), Run(id,project_id,status,phase,progress,source_hash,rule_version,manifest,documents,evidence,facts,results,events,error), Review(id,run_id,rule_id,before,after,reason,actor,time,evidence_ids), Knowledge(id,title,text,status,version,source), Feedback(id,run_id,rule_id,kind,body,status), Report(id,run_id,created_at,review_count)。项目、审核批次和风险命中分别统计。

HTTP接口：bootstrap、projects、runs、run upload/补证、run review/字段确认/chat/retry/report、rules、knowledge、feedback、operations。写操作需同源会话令牌；附件只允许通过证据ID获取，禁止任意路径读取。

## 必需测试

ZIP路径穿越/链接/解压限额；中文路径与重复哈希；规则数量和三态完整性；不可靠OCR不得产生确定金额拦截；证据引用校验；P0汇总；复核留痕；持久化重启；HTML转义和离线证据；两包独立运行、31唯一业务内容一致；上传、证据跳转、复核、报告的界面验收。

## 版本边界

V0.1为本地单用户版，不宣称提供企业多租户与生产访问控制。Windows本地OCR不保证准确；AI服务未配置时不宣称语义审核已验证。比赛完整交付中的PPT另行制作，本次聚焦可运行系统及验证产物。
