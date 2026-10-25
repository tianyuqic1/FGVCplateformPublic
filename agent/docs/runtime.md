# Agent 当前实现与运行

状态：2026-10-25，完整首版与第 15 个测试集错分分析工具均已实现并完成本地验收。以本文和源码为当前行为；架构文档中的 Gateway 独立账本、供应商全局 RPM/TPM、多专家和拆分 Runner 属于后续扩展。

## 用户能做什么

新增 [训练测试集错分分析](test-error-analysis.md)：从已完成训练详情打开，区分统计证据与原因假设，旧报告缺失数据时明确提示。

在侧边栏打开「实验助手」，或在业务页面右下角打开抽屉。输入问题后，助手查询数据集、训练、指标、模型、骨干和 Worker，使用真实结果回答。对话历史保存在服务端，刷新后恢复所选对话。

训练方案先通过平台共享校验，界面展示规范化后的完整参数。只有点击「确认执行」才领取平台签名回执并执行提交；聊天文字中的“确认”不能替代按钮。取消训练、样本推理也采用相同确认机制。方案不能直接修改，拒绝后可用新问题生成新方案。

「停止」停止助手后续分析，不回滚已经提交的业务任务。训练任务需通过平台页面或新的已确认取消工具单独取消。

每个对话可登记训练跟踪，每 30 秒读取训练状态，最长 24 小时。成功、失败、取消后保存结果并结束，用户可点击「分析结果」发起新分析。跟踪不会自动新建实验，也不在等待期间占用模型连接。

## 部署与数据所有权

```mermaid
flowchart LR
  UI[浏览器: 工作区 / 助手抽屉] -->|Cookie + CSRF| CP[Go 控制面 /api/agent]
  CP -->|签名身份: 2 分钟| A[agent-service: API + Runner + Watch]
  A --> AD[(agent-postgres / finevision_agent)]
  A -->|30 分钟受限委托 / 已确认回执| TOOLS[控制面 /api/agent-tools]
  TOOLS --> PD[(平台 PostgreSQL)]
  A -->|messages + tools| GW[LLM Gateway /internal/v2/chat]
  GW --> LLM[已配置供应商]
```

Agent 是独立 Go 模块和镜像，使用独立 PostgreSQL 容器、database、账号和 volume；不访问平台数据库或导入平台 internal 包。平台只引用公开的工具、身份和模型协议。现有 Python 训练、推理和制品服务继续承担业务计算。

Agent 自有表为 conversations、runs、events、watches 和 agent_schema_migrations。启动时使用事务与 advisory lock 执行内嵌、可重复的两份 SQL 迁移。Compose 本地版本使用本库账号管理本库 schema，尚未拆分生产迁移账号与运行账号。

本地启动：

```sh
python3 agent/deploy/init_local.py
docker compose build go-control-plane agent-service migrate frontend
docker compose up -d --no-build frontend go-control-plane go-llm-gateway agent-service outbox-relay python-training-worker python-inference-runtime
```

使用现有的 `.env` LLM 配置，额外需要：

- `FINEVISION_AGENT_DELEGATION_KEY`：至少 32 字节，控制面和 Agent 共用的身份签名密钥。
- `FINEVISION_AGENT_TOOLS_SIGNING_KEY`：至少 32 字节，平台写动作确认签名密钥，仅平台使用。
- `FINEVISION_AGENT_DB_PASSWORD`：Agent 专属数据库密码；本地初始化脚本生成 URL 安全的十六进制随机值。

初始化脚本只补缺失或空的新字段，不覆盖已配置密钥。`.env` 和测试凭证不纳入 Git。Agent 的 8091 和独立数据库端口只在 Compose 网络暴露；浏览器统一访问平台代理。`/health` 检查 Agent 数据库连接，Compose 提供健康检查。

## 身份与执行

平台首先验证当前登录 Cookie、账号状态、角色和 CSRF，然后覆盖传给 Agent 的身份头。Agent 不接收原始 Cookie、CSRF 或供应商密钥。

后台保存签名委托，包含用户 ID、会话哈希、角色和 30 分钟有效期。平台每次工具调用都通过自己的会话库重新验证会话和当前角色，因此退出、禁用、角色变更立即影响后续读取。后台委托仅能访问 `/api/agent-tools/*`，不能访问一般平台 API，也不能签发写操作确认。确认接口使用当次用户请求的短期身份，平台将回执绑定到当前登录会话、用户、工具、参数摘要和固定 operation ID。

授权到期进入 waiting_auth，释放执行槽，刷新登录后点「恢复当前登录授权」。写回执失效时重新展示方案供确认，不悄悄续签。新登录会话也必须重新确认旧会话的写动作。

## 并发、预算和故障处理

| 范围 | 当前限制与处理 |
|---|---|
| 会话 | 一个非终态任务；数据库部分唯一索引兜底 |
| 用户 | 最多 2 个运行任务，8 个未完成任务，8 个活动跟踪 |
| 全局 Agent | 数据库调度最多 8 个运行任务；首个容器 4 个工作协程 |
| 全局排队 | 最多 128 个 queued/running，事务串行准入 |
| 工具 | 单进程最多 16 个工具请求；单轮最多 4 个独立只读调用；写动作单独执行 |
| 单个 Run | 最多 8 轮模型、24 次工具；观测用量 48000 tokens 后停止后续模型派发；单轮最多 4096 输出 tokens |
| 上下文 | 最近 6 次成功问答 + 当前完整工具链，模型请求不超过 512 KiB |
| 租约 | 60 秒，5 秒续租；epoch + CAS 阻止旧 Runner 写入 |
| SSE | 持久事件游标，支持 Last-Event-ID 和 after；每 50 秒正常重连，期间心跳 |
| 模型 Gateway | v2 单实例最多 8 个在途请求，不自动重试供应商请求 |

48000 是根据上次实际用量停止派发的预算阈值，不是供应商扣费的硬上限。Gateway v1 和已有直连标注路径尚未纳入同一 RPM/TPM 账本。多 Gateway 副本需要额外实现供应商级共享准入，当前不能宣称供应商账号全局限额已完成。

不同用户优先取得首个槽，再复用用户剩余槽；数据库串行准入使多进程遵守同一全局/用户限制。输入 request_key 由浏览器生成并在请求失败时保留，重复提交返回同一 Run，键与内容不匹配返回 409。

模型调用前保存 model_pending。若响应丢失或服务重启时状态仍未知，进入 unknown，不自动重复计费请求；用户可检查记录后新建问题。只读工具可在租约恢复后重新查询，事件记录保留实际观测时间。

写工具先保存固定 operation ID 和执行意图。响应丢失时，通过平台 operation 查询已有业务事实，不重新派发未知操作。平台本库的训练与 Outbox、取消事件和推理事件已有各自幂等保护。已知成功结果和本地步骤在一次 Agent checkpoint 里保存；即使旧 Worker 恢复，也无法越过 epoch。

unknown 会释放会话槽，用户可点击「核对平台操作结果」。模型结果 unknown 没有供应商账单/状态查询能力，不提供自动续跑。单次确认仅执行一个动作，首版没有多实验批量确认。

列表查询对模型上下文投影为身份、最新版本和就绪统计，避免把每类明细灌入上下文；完整工具响应仍在证据日志。大结果明确标注截断，助手可用具体版本或分页工具继续查询。Markdown 输出不执行 HTML，不加载模型给出的图片。

## API

浏览器路径为 `/api/agent`，代理到 Agent `/v1`。身份不可由请求体指定。会话、Run、事件、确认与跟踪均按当前用户隔离。

| 方法 | 浏览器路径 | 用途 |
|---|---|---|
| GET / POST | `/api/agent/conversations` | 历史 / 新建 |
| GET | `/api/agent/conversations/{id}` | 最近 100 个 Run |
| POST | `/api/agent/runs` | conversation_id、request_key、input |
| GET | `/api/agent/runs/{id}` | 状态、回答、用量及可确认方案 |
| POST | `/api/agent/runs/{id}/confirm` | operation_id 必须匹配当前预览 |
| POST | `/api/agent/runs/{id}/cancel` | 停止助手任务 / 拒绝方案 |
| POST | `/api/agent/runs/{id}/resume` | 恢复过期后台授权 |
| GET | `/api/agent/runs/{id}/operation` | 核对未知写动作 |
| GET | `/api/agent/runs/{id}/events` | SSE；format=json 返回一页事件 |
| GET | `/api/agent/tools` | 15 个工具的参数规范 |
| GET / POST | `/api/agent/watches` | conversation_id 查询 / 登记 run_id 跟踪 |
| POST | `/api/agent/watches/{id}/cancel` | 停止跟踪 |
| POST | `/api/agent/watches/{id}/resume` | 恢复跟踪授权 |

工具实际契约另见 [tools.md](tools.md)；模型协议在 `api/modelcontract`，签名协议在 `api/delegation`。

## 验证

单元与 PostgreSQL 集成测试：

```sh
cd agent
FINEVISION_AGENT_TEST_DATABASE_URL=postgres://user:pass@127.0.0.1:5432/disposable_db go test -race ./...
go vet ./...
cd ../go
go test -race ./...
go vet ./...
cd ..
uv run pytest -q backend/tests/test_test_error_analysis.py backend/tests/test_training_head.py backend/tests/test_image_training.py
cd frontend
npm run check
npx playwright test e2e/agent.spec.js
```

Agent 集成测试为每例新建独立 schema 并清理；只能指定专用测试数据库。覆盖 20 用户 / 40 个 Run / 两组 Runner 的共享准入、20 次重复请求、12 次并发确认、租约过期、旧 Worker fencing、写结果恢复、取消、Watch 和跨用户隔离。

显式真实模型验收：

```sh
python3 agent/tests/smoke_runtime.py --credentials /private/test-account.json
python3 agent/tests/smoke_write.py --credentials /private/test-account.json --dataset-version TEST_VERSION_UUID --confirm-test-training
cd frontend
FINEVISION_AGENT_LIVE_CREDENTIALS=/private/test-account.json PLAYWRIGHT_BASE_URL=http://127.0.0.1:5173 npx playwright test e2e/agent.live.spec.js
```

凭证文件内容为测试账号 email / password，权限应为 0600，账号需已审核。写验收只接受 1–16 个样本的测试版本，固定 color_stats_v1、单次尝试，并验证规范方案后确认；不覆盖大规模 GPU 训练容量。

2026-10-24 本地真实验收已完成：现有供应商模型两轮调用数据集与 Worker 工具并回答；规范训练方案、重复 3 次确认、创建唯一训练、训练成功、Watch 完成；Playwright 验证历史刷新恢复和训练页助手抽屉。小样本结果用于验证流程，不用于衡量模型质量。
