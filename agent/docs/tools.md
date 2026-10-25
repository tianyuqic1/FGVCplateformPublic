# Agent 工具层使用说明

完整对话服务已接入这些工具，当前运行方式见 [runtime.md](runtime.md)。后台调用使用受限签名委托；CLI 仍可使用请求级会话。

原两批加错分分析共 15 个工具、独立 Go 模块、平台 HTTP 客户端、批次调度、平台工具入口、用户确认和持久结果对账已实现。可通过 CLI 使用，不依赖 LLM、Eino 或完整 Agent 对话服务。

CLI 适配器使用请求级登录会话和 CSRF，不把原始凭据写入模型或数据库。完整 Agent 服务则使用平台签名、30 分钟有效的受限后台委托，原始 Cookie 不离开控制面；确认使用当次浏览器请求的短期身份。对话代理和持久 Runner 已实现，细节见运行说明。

## 1 工具清单

| 名称 | 参数概要 | 行为 |
|---|---|---|
| `datasets.list` | q、status、limit、offset | 查询数据集，默认每页 20、最多 100 |
| `datasets.get_version` | dataset_version_id | 类别、划分、数量、血缘与 readiness；不下载样本 |
| `training.list_runs` | q、status、dataset_id、backbone_id、分页 | 查询历史训练 |
| `training.get_run` | run_id | 配置、状态和错误摘要 |
| `training.analyze_test_errors` | run_id、max_confusion_pairs、max_examples | 只读最终测试错分证据；后两项各为 1–20；[详情](test-error-analysis.md) |
| `training.get_metrics` | run_id、metric_name、attempt_id、after_id、limit | 指标游标查询，默认 100 点、最多 1000 |
| `models.list_versions` | 数据集、版本、架构、预训练、状态、分页 | 查询模型版本 |
| `models.get_version` | model_version_id | 模型指标、来源、产物元数据与发布状态 |
| `models.compare` | model_version_ids，2–5 个不同 ID | 复用同一数据集和版本的比较规则 |
| `catalog.list_backbones` | 空对象 | 读取受管骨干及训练策略 |
| `workers.get_capacity` | kind、status、device、q、分页 | 按角色裁剪的可用性与负载；不预留硬件 |
| `training.validate_plan` | 数据版本、骨干、图像尺寸、训练参数 | 共用平台创建校验，返回规范参数和摘要，无训练副作用 |
| `training.submit_plan` | 已确认的规范训练参数 | 写工具，每个已确认操作只创建一个训练 |
| `training.cancel_run` | run_id | 写工具，取消训练并保存可恢复事件 |
| `inference.run` | 数据版本、模型、sample_id、可选部署、top_k、evidence_k | 写工具，每次一个已登记样本 |

输入 Schema 以 `api/toolcontract` 为单一来源，Agent 与平台执行相同的闭合参数检查。未知字段、越界分页、重复比较 ID、无效嵌套类型和多份 JSON 均拒绝。训练方式、数据 readiness、模型状态与比较范围由平台业务规则最终检查。

推理输入只接受指定数据版本的 sample_id，不接受任意 URL、宿主机路径或文件夹；批量上限为 1。由于推理会创建事件并可能进入复核，按写操作处理。

## 2 模块和 HTTP 接口

| 模块 | 职责 |
|---|---|
| `agent/api/toolcontract/` | 公开工具定义、Schema、校验和 OpenAPI 生成 |
| `agent/internal/tools/` | 注册、超时、可信控制参数和有界执行 |
| `agent/internal/adapters/platform/` | 请求级会话、HTTP、确认及结果查询 |
| `agent/cmd/agent-tools/` | 独立 CLI |
| `go/internal/httpapi/agent_tools.go` | 平台入口、签发确认、调用现有业务处理与结果封装 |
| `go/internal/agenttools/` | 平台 receipt 与操作存储协议 |
| `go/internal/adapters/postgres/agent_tool_operations.go` | 平台持久抢占、响应与业务结果找回 |

Agent 独立模块使用 pgx 和 UUID；公开协议包只依赖标准库。平台通过本地 module replace 引用公开 api/toolcontract、api/delegation 和 api/modelcontract；Docker 构建复制这些公开目录，不引用 Agent internal 包，也不共享服务运行状态。

| 路径 | 用途 |
|---|---|
| `GET /api/agent-tools/catalog` | 当前角色可用工具和 Schema |
| `POST /api/agent-tools/invoke/{tool}` | 调用工具；读取也需 CSRF |
| `POST /api/agent-tools/approvals/{tool}` | 可信用户确认，三个写工具可用，receipt 有效 15 分钟 |
| `GET /api/agent-tools/operations/{id}` | 查询本人操作，并尝试找回已持久化业务结果 |
| `GET /openapi/agent-tools.json` | 管理员可读 OpenAPI 3.1，已加入 Swagger 分组 |

管理员和业务人员可调用全部工具；标注员仅有数据集与模型读取子集。原资源接口继续执行既有可见性，未新增全平台用户级隔离。

请求包含 arguments；写请求还需 operation_id 和 approval，后两者属于执行控制，不在模型参数 Schema 中。成功结果包含 tool、data、observed_at、evidence 和可选 operation_id。存储 URI、缓存目录、宿主机路径和凭据字段会被裁剪。错误包含 code、message、unknown。

## 3 写操作配置和确认

先应用平台 Alembic 迁移 `20261024_0025`，再设置独立随机 `FINEVISION_AGENT_TOOLS_SIGNING_KEY`，至少 32 字节。Compose 已向控制面传递该变量；默认留空，确认和写调用关闭，查询与无副作用校验可用。

迁移只增加平台所有的外部操作表与恢复索引，不创建 Agent 会话数据库。生产使用 PostgreSQL 操作存储，内存存储仅用于测试或显式临时开发。

执行顺序：

1. 调用 validate_plan 得到规范训练参数；取消与推理则展示明确目标和输入。
2. UI 显示参数、范围与影响，用户确认后由可信控制层请求 approvals；该接口不能注册为模型工具。
3. 使用平台返回的精确 arguments、operation_id 和 receipt 执行。训练 receipt 绑定展开默认值后的参数，不绑定原先缺省的输入。
4. 重试复用 operation_id，明确新动作使用新 UUID；用户、会话、工具、参数或 ID 变化，旧 receipt 失效。
5. 超时或结果不明先查 operations，不盲目生成新 ID 重试。

approved=true 不构成授权。确认依赖真实登录和 CSRF，执行时再次校验当前会话、签名与参数摘要。Agent UI 已实现规范方案展示、确认执行、拒绝、过期授权恢复与结果核对。

## 4 命令行使用

在 agent/ 下，无需网络即可查看工具和接口：

```bash
go run ./cmd/agent-tools list
go run ./cmd/agent-tools openapi
```

使用平台现有登录获取 Cookie 和 CSRF，写入仅本人可读的 `/tmp/finevision-session.json`，权限 0600，JSON 结构为 `{"cookie":"平台会话 Cookie","csrf":"平台返回的 CSRF"}`。不把凭据放入命令行参数或仓库。

```bash
go run ./cmd/agent-tools invoke \
  --platform http://localhost:8001 \
  --session-file /tmp/finevision-session.json <<'JSON'
{"tool":"datasets.list","arguments":{"limit":10}}
JSON
```

写操作由用户核对后使用 `approve --confirm --operation-id 本次动作的UUID`，stdin 仍是 tool 和 arguments；将标准输出保存为 0600 的 approval 文件，建议先执行 `umask 077`。执行时传 `--approval-file`，stdin 使用确认结果中的精确 arguments。CLI 不会静默替换另一组参数。

查询不明动作使用 `operation --operation-id 本次动作的UUID`，同时提供 platform 和 session-file。approval 文件有过期时间，不可作为长期后台凭据。

## 5 并发和恢复

Registry 有进程内执行槽和每批并行上限，完整服务配置为 16 和 4，CLI 默认 8 和 4。每批最多 24 个调用，整批先校验；纯读取有界并行、按 call ID 保序，一个读取失败保留其他结果。带写操作的批次只能包含一个动作。

平台按用户与 operation ID 在 PostgreSQL 抢占写操作，同键不同工具或参数冲突，同键完成请求返回保存结果。训练另用稳定任务 ID 与唯一操作索引，重启或响应保存失败不创建第二个任务。

业务提交与响应登记分两次本地事务。训练、推理、取消在其权威业务事实中保存可信操作键；对账只查既有事实，不重新执行计算。若崩溃发生在业务持久化前，操作可能仍 running，outcome 为 pending_or_unknown，保持隔离。completed 表示响应已保存，成功与否由 outcome 和 http_status 表示，服务端不明确错误保守记为 unknown。

会话串行和持久 Runner 调度现由 Agent 本库的事务、租约与唯一索引实现；本地工具 semaphore 只控制单容器内工具并发。供应商级跨 Gateway 副本配额仍为后续扩展。

## 6 验证

```bash
# agent/ 下
go test -race ./...

# go/ 下
go test -race ./internal/httpapi ./internal/training ./internal/agenttools ./internal/auth

# go/ 下，仅使用已迁移的专用测试库
FINEVISION_TEST_GO_DATABASE_URL=测试库连接 go test -race ./internal/adapters/postgres
```

覆盖 15 个工具入口、真实 CLI 跨模块调用、权限与 CSRF、跨会话确认、参数篡改、并发去重、响应保存失败、真实 PostgreSQL 抢占、稳定训练 ID 与恢复。推理计算使用受控替身验证工具路径，不声称已验证真实 GPU 或生产数据推理。
