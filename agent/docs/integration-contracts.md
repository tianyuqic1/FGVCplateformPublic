# Agent 与平台、模型服务的集成契约

更新：2026-10-25。本文记录首版已实现协议；完整用户 API 见 [OpenAPI](../api/openapi.yaml)，运行配置见 [runtime.md](runtime.md)。

## 数据所有权

| 服务 | 存储 | 权威数据 |
|---|---|---|
| 平台控制面 | 平台 PostgreSQL 与 MinIO | 用户、会话、数据、训练、模型、制品、外部操作与 Outbox |
| Agent | 独立 `finevision_agent` 数据库与 volume | 对话、Run、持久消息与方案、事件、Watch |
| LLM Gateway | 现有配置和进程内并发限制 | 供应商调用适配；目前没有请求或配额数据库 |

Agent 不读取平台表，平台不读取 Agent 表。平台 Alembic 迁移 `20261024_0025` 只管理平台自己的外部操作表与索引；Agent 两份 SQL 迁移由 Agent 启动时在自己的库应用。

## 浏览器身份与后台委托

浏览器使用现有登录 Cookie 和 CSRF 请求控制面 `/api/agent/*`，同源代理到 Agent `/v1/*`。平台校验当前会话与角色，清除外部身份头后使用 `FINEVISION_AGENT_DELEGATION_KEY` 签名。Agent 不接收原始 Cookie、CSRF 或供应商密钥。

签名协议位于 `agent/api/delegation`，采用 HMAC-SHA256，包含 actor、role、session 哈希、purpose 和 expires。交互用途有效期 2 分钟，后台 tools 用途 30 分钟。首版没有独立 grant 数据库、JWT 密钥轮换端点或任意平台路径授权。

后台委托只能用于 `/api/agent-tools/*`，不能进入普通业务 API，也不能自行签发写确认。平台每次工具请求重新查询自己的会话存储并核对当前角色，因此退出、禁用和角色变化影响后续调用。到期进入 waiting_auth，由用户恢复当前登录授权；不得把旧 Cookie 当长期后台凭证。

Agent API 在验证签名后按 actor 约束对话、Run、事件和 Watch。业务工具继续受平台既有权限约束；管理员与业务人员可用完整工具，标注员仅有数据集和模型读取子集。

## 工具协议

公开 Schema 唯一来源为 `agent/api/toolcontract`，平台与 Agent 执行相同闭合校验。平台 Go 模块仅引用 Agent 的公开 `api` 包，不导入其 internal 实现。

| 方法与平台路径 | 用途 |
|---|---|
| `GET /api/agent-tools/catalog` | 当前角色的工具目录 |
| `POST /api/agent-tools/invoke/{tool}` | 调用一个工具，输入 arguments |
| `POST /api/agent-tools/approvals/{tool}` | 用户确认后签发写操作回执 |
| `GET /api/agent-tools/operations/{id}` | 查询本人操作及恢复业务结果 |
| `GET /openapi/agent-tools.json` | 管理员可读工具 OpenAPI |

未知参数、非法类型或超限数量直接拒绝。返回带 tool、data、observed_at、evidence，可带 operation_id；错误包含 code、message、unknown。结果裁剪存储 URI、缓存与宿主路径、凭据，避免传入模型。全部 15 个工具的字段见 [tools.md](tools.md)。

`training.analyze_test_errors` 是只读工具。报告字段位于 `training_report.json` 的 `evaluation.error_analysis`，不新增该功能专属数据库表；严格 test 指标与历史训练侧指标分开，历史兼容和证据完整性见 [错分分析](test-error-analysis.md)。

## 写确认与幂等

1. Agent 展示完整方案。训练预览与正式创建共用 prepareTrainingRun 校验和规范化逻辑。
2. 用户点击确认，Agent 使用当前交互身份向平台申请回执。模型不能调用 approvals，也不能以 approved=true 代替确认。
3. 平台用仅自己持有的 `FINEVISION_AGENT_TOOLS_SIGNING_KEY` 签名，绑定用户、当前会话、工具、规范参数摘要与固定 operation ID，有效 15 分钟。
4. 执行请求包含相同 arguments、operation_id 和 approval。平台复核当前会话、签名和参数，按用户与操作 ID 在本库抢占；相同 ID 不同参数返回冲突。
5. 平台登记响应；已完成请求返回保存结果。网络未知先查 operations，不生成另一个 ID 重发。

操作登记与业务提交采用分开的本地事务，不能宣称 Agent、平台操作记录和业务写入原子提交。训练使用稳定任务 ID 与唯一操作索引，推理和取消在权威业务事实保存可信操作键，业务事件和 Outbox 由平台本库保证一致性。恢复查询只读取已有事实，不重新训练或推理。

无法确认业务结果时保留 pending_or_unknown，不能把“尚未查到结果”解释为可安全重发。Agent Run 的 unknown 释放会话槽，界面可核对平台结果；重新登录、参数变化或确认过期后需要重新确认。

## 模型协议

Agent 调用现有 Gateway 的 `POST /internal/v2/chat`，使用现有 `FINEVISION_LLM_INTERNAL_TOKEN`。公开结构位于 `agent/api/modelcontract`：

- 请求为 request_id、messages 和 tools。
- 消息带 role、content、可选 tool_calls 与 tool_call_id。
- 工具定义带名称、描述及参数 Schema。
- 响应带 message、model、input_tokens 和 output_tokens。

供应商适配器将领域工具名称中的点映射为双下划线，返回时映射回内部名称。首版是完整响应加持久 SSE 事件，没有逐 token 供应商流。Gateway v2 单实例最多 8 个在途请求，不自动重试；request_id 是关联标识，不代表供应商已实现持久请求去重或可查账单。

Agent 在派发前保存 model_pending。进程故障或响应丢失进入 unknown，不自动再次计费。跨副本共享 RPM/TPM、持久 Gateway 请求账本、状态查询和取消端点尚未交付。

## 事件、取消与恢复

SSE 使用持久事件 ID，支持 Last-Event-ID / after；代理流每 50 秒重连，前端从游标继续。format=json 可分页读取同一事件，不依赖浏览器维持后台执行。

Agent 停止操作只取消后续助手步骤；已经提交的训练需使用平台取消能力。Watch 轮询不调用模型，终态保存结果后由用户触发分析。租约、epoch、队列、工具批次、权限与故障恢复的当前限制见 [架构说明](architecture.md)。
