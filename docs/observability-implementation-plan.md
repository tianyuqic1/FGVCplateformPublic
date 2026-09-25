# FineVision 日志与可观测性实施计划

状态：已实施并完成本地验收（2026-09-20）
范围：本地 Docker Compose 优先，保留扩展到多计算节点的能力
目标：建立可查询、可关联、可告警且不影响训练与推理主链的运行诊断系统

## 1. 背景

FineVision 已经具备三类零散能力：

- Go 使用 `slog`、Python 使用标准 `logging` 输出运行信息；Docker 使用默认日志驱动保存标准输出。
- PostgreSQL 已保存训练任务、Attempt、Artifact、模型发布、推理事件、人工复核和标注状态等业务事实。
- 部署构建会生成节点本地 `build.log`，硬件监控会将 24 小时快照写入 PostgreSQL。

当前缺少统一日志契约、跨模块关联、集中检索、运行指标、告警和分布式追踪。一个浏览器请求经过 Go、Outbox、RabbitMQ、Python Worker、MinIO 后，无法用同一个诊断编号还原完整过程。

## 2. 目标

1. 所有 Go 和 Python 长驻进程输出统一 JSON 日志。
2. 用户看到的错误包含可复制的 `request_id`，运维人员可用它定位服务端记录。
3. 可按 `job_id`、`training_run_id`、`attempt_id`、`model_version_id`、`dataset_version_id` 查询跨模块日志。
4. 保留 PostgreSQL 作为业务审计与状态恢复的唯一事实源。
5. 建立 HTTP、gRPC、RabbitMQ、Worker、PostgreSQL、MinIO 和 LLM 的运行指标与告警。
6. 建立 HTTP → Outbox → RabbitMQ → Worker → Artifact 的分布式追踪。
7. 可观测后端不可用时，训练、推理、标注和发布仍能继续运行。
8. 日志不包含密钥、图片内容、权重内容、完整 Prompt 或 Provider 响应。

## 3. 非目标

- 不用 Loki 代替 `job_events`、训练曲线、模型发布事件或标注状态。
- 不把训练每个 batch 的损失值写成 INFO 日志或 Prometheus 高基数时间序列。
- 不重复建设日志、指标与 Trace 存储引擎；FineVision 通过独立聚合服务提供受控查询和统一产品入口。
- 不在首阶段一次性启用完整 Trace；先统一日志契约和关联字段。
- 不要求本地开发必须启动可观测栈；默认主业务 Compose 仍可独立运行。
- 不采集图片、模型权重、环境变量全集、进程命令行或用户上传内容。

## 4. 总体架构

```text
浏览器
  │ request_id / traceparent
  ▼
Go 控制面 ── PostgreSQL 业务审计
  │  │
  │  ├── Outbox ── RabbitMQ ── Python 训练/部署 Worker
  │  ├── gRPC ──────────────── Python 推理/Artifact Runtime
  │  └── HTTP ──────────────── LLM Gateway / 标注 Agent
  │                                  │
  └──────────────────────────────────┴── MinIO

运行日志：JSON stdout ── Grafana Alloy ── Loki
运行指标：/metrics ───── Prometheus ───── Grafana
调用链路：OTLP ───────── Grafana Alloy ── Tempo
业务事实：领域事件 ───── PostgreSQL ───── FineVision UI
诊断文件：build.log 等 ─ MinIO ───────── FineVision UI
```

## 5. 四类信号的职责

| 信号 | 事实源 | 保存内容 | 不保存内容 |
|---|---|---|---|
| 运行日志 | Loki | 启停、请求、异常、重试、依赖调用、关键阶段 | 业务最终状态、原图、权重、密钥 |
| 业务审计 | PostgreSQL | 状态转换、actor、reason、Attempt、发布与确认 | 堆栈、高频心跳、调试文本 |
| 运行指标 | Prometheus | 请求量、错误率、延迟、积压、心跳年龄、资源状态 | job_id、run_id、图片级结果 |
| 调用链路 | Tempo | 同步调用因果关系、异步 Span Link、阶段耗时 | 长期业务历史、完整训练曲线 |

## 6. 统一日志契约

### 6.1 基础字段

每条运行日志必须包含：

| 字段 | 说明 |
|---|---|
| `timestamp` | UTC RFC3339Nano |
| `level` | `DEBUG/INFO/WARN/ERROR` |
| `service` | 稳定模块名，例如 `go-control-plane` |
| `service_version` | 构建版本或 Git SHA |
| `environment` | `local/test/staging/production` |
| `event` | 稳定、机器可读的事件名 |
| `message` | 面向人的简短描述 |
| `request_id` | HTTP/操作诊断编号，可空 |
| `trace_id`、`span_id` | 启用 Trace 后自动注入，可空 |
| `outcome` | `success/failed/retry/unknown`，适用时填写 |
| `duration_ms` | 操作耗时，适用时填写 |

### 6.2 领域关联字段

根据场景附加，不要求所有日志同时包含：

- 训练：`job_id`、`training_run_id`、`attempt_id`、`execution_epoch`、`worker_id`。
- 数据集：`dataset_id`、`dataset_version_id`、`import_job_id`。
- 模型：`model_version_id`、`deployment_id`、`runtime`、`precision`。
- 推理：`inference_run_id`、`model_version_id`、`runtime`、`precision`。
- 标注：`annotation_project_id`、`annotation_task_id`、`workflow_stage`、`provider_request_id`。
- 消息：`message_id`、`exchange`、`queue`、`routing_key`、`delivery_attempt`。

这些高基数字段只作为 JSON 字段或 Loki Structured Metadata，不作为 Loki 索引标签或 Prometheus 标签。

### 6.3 Loki 低基数标签

仅使用：

- `service`
- `environment`
- `level`
- `runtime`
- `route`
- `outcome`

### 6.4 脱敏与截断

统一 Redaction 必须拦截：

- `Authorization`、Cookie、API Key、数据库口令、S3 Secret。
- 图片 data URL、Multipart 内容、模型响应原文、完整 Prompt。
- 上传文件的用户绝对路径、环境变量全集。
- 大于 4 KiB 的单字段；异常堆栈允许单独配置上限，总日志事件默认不超过 16 KiB。

## 7. 模块设计

### 7.1 Go 运行遥测模块

新增 `go/internal/observability`，集中隐藏：

- `slog` JSON Handler、日志级别和基础资源字段。
- 从 `context.Context` 注入 Request、Trace 与领域字段。
- Chi HTTP 访问日志、状态码捕获、响应字节、超时和 panic 记录。
- gRPC Client/Server Interceptor。
- OpenTelemetry Provider 初始化、导出和优雅关闭。
- Prometheus Handler 与进程基础指标。
- Redaction、字段截断和健康检查采样。

领域模块不得直接依赖 Loki、Tempo 或 Grafana。

### 7.2 Python 运行遥测模块

新增 `backend/src/finevision/observability`，集中隐藏：

- 标准库 `logging` JSON Formatter。
- `contextvars` 关联字段。
- gRPC Client/Server Interceptor。
- Worker 任务 Scope 与异常分类。
- Prometheus 指标注册和本地监听。
- OpenTelemetry OTLP 导出。
- Redaction 与字段截断。

标注目录的独立 Worker 复用相同字段契约；无法直接共享包时，提供兼容 Adapter，不另定义第二套字段名。

### 7.3 RabbitMQ 关联

- 保持现有消息 JSON Body 契约稳定。
- Outbox 持久化独立 Trace Context，Relay 将 `traceparent/tracestate` 写入 AMQP Headers。
- Consumer 为每次 Attempt 建立新 Trace，并用 Span Link 关联生产端，不建立持续数小时的单一 Span。
- Publisher/Consumer 统一记录发布、确认、拒绝、重入队和 DLQ 指标。

### 7.4 前端诊断编号

- 统一 HTTP Client 读取响应头和错误体中的 `request_id`。
- 错误提示提供“复制诊断编号”，不展示内部堆栈。
- 训练、推理、部署和标注均通过聚合服务按业务 ID 和时间范围生成受控时间线，不向浏览器暴露底层查询语言。

## 8. 分阶段实施

### 阶段 0：确定契约和安全规则

目标：先固定字段、级别、保留和安全规则，不改变运行行为。

工作项：

1. 固化本文件中的日志字段、事件命名和禁止字段。
2. 定义稳定的模块名和环境名。
3. 定义 `DEBUG/INFO/WARN/ERROR` 使用规则。
4. 定义事件大小、堆栈大小和采样规则。
5. 建立测试密钥、测试图片 data URL 和测试 Prompt，用于脱敏回归。

验收：

- 字段契约经 Go、Python、前端共同使用。
- 安全测试可明确判断一条日志是否违规。
- 不增加运行依赖，不影响现有 Compose。

### 阶段 1：可靠的结构化日志

目标：即使没有 Loki，也能通过 `docker compose logs` 获得统一、可关联的 JSON 日志。

工作项：

1. 实现 Go 运行遥测模块并接入控制面、Outbox Relay、Deployment Relay、LLM Gateway。
2. 为 Chi 增加统一访问日志：route template、method、status、duration、response bytes、request_id。
3. 将 `X-Request-ID` 回传给客户端；忽略或重建不可信外部 Trace Context。
4. 实现 Python 运行遥测模块并接入训练、推理、Artifact、部署、硬件采集 Worker。
5. 为标注 Worker 增加同契约 Adapter；保留宿主文件日志作为过渡。
6. 补齐训练 Worker 的领取、Claim、Attempt、Artifact、Complete 和失败记录。
7. 补齐推理、Artifact Runtime 的启动、加载、校验和失败记录。
8. 前端统一展示与复制 `request_id`。
9. 为 Compose 服务配置 Docker 日志轮转，默认 `10 MiB × 5`。
10. 增加 `FINEVISION_LOG_LEVEL`、`FINEVISION_LOG_FORMAT`、`FINEVISION_ENVIRONMENT`。

验收：

- 所有长驻进程输出可解析 JSON。
- 一个失败请求可以通过 `request_id` 找到对应访问日志和错误日志。
- 一个训练任务可以通过 `job_id/attempt_id` 找到 Go 与 Python 记录。
- 日志中搜索不到测试密钥、Authorization、图片 data URL 和完整 Prompt。
- 日志模块初始化或输出失败不影响业务结果。

### 阶段 2：集中日志检索

目标：支持跨容器检索、过滤和保留策略。

工作项：

1. 新增独立 `compose.observability.yml` 和 `observability` Profile。
2. 接入 Grafana Alloy、Loki、Grafana，并使用固定版本而非 `latest`。
3. Alloy 采集 Docker stdout；过渡期同时采集 `annotation/runtime/worker.log`。
4. Loki 仅索引低基数字段，高基数字段使用 Structured Metadata 或查询时解析。
5. 开发环境 Loki 保留 7 天；部署环境默认 30 天。
6. 配置 Grafana Data Source、Explore、基础日志 Dashboard。
7. 为 `request_id/job_id/attempt_id` 提供预置查询。
8. 验证 Loki/Alloy 停止时主业务继续工作。

安全约束：

- 本地可以只读挂载 Docker Socket；正式部署优先使用受限 Socket Proxy、journald 或日志文件采集。
- Loki 使用独立数据卷或独立 S3 Bucket，不与 `finevision-artifacts` 共用生命周期。
- Grafana 默认仅绑定本地端口；公开访问前必须接入鉴权与 TLS。

验收：

- Grafana 能跨 Go/Python 模块查询同一个 `job_id`。
- 删除 Loki 数据不会破坏 PostgreSQL 任务历史与恢复。
- 超过保留周期的日志自动清理。
- Loki 标签基数符合预算，不包含业务 UUID。

### 阶段 3：运行指标和告警

目标：由系统主动发现故障，而不是等待用户报告。

工作项：

1. 为 Go HTTP/gRPC 接入请求量、错误率、延迟和在途数量。
2. 为 Python Runtime/Worker 接入任务数、失败类型、阶段耗时和心跳年龄。
3. 启用 RabbitMQ Prometheus 插件；接入 PostgreSQL、MinIO 运行指标。
4. 增加 Outbox pending 数、oldest age、发布失败与 DLQ 指标。
5. 增加训练 queue age、Attempt 状态、Lease 过期和 Worker 在线指标。
6. 增加推理按 `runtime/precision/outcome` 聚合的延迟和错误率。
7. 增加标注 pending、unknown、failed、memory outbox lag、Provider latency 和工具调用指标。
8. 增加 LLM 429、5xx、unknown outcome、Token 和延迟指标。
9. 配置 Grafana Dashboard 与告警规则。

首批告警：

- API 5xx 比例连续 5 分钟超过 2%。
- Outbox 最老未发布事件超过 60 秒。
- RabbitMQ DLQ 出现消息。
- Worker 心跳超过 60 秒。
- 训练 Lease 过期或 Attempt 连续失败。
- Artifact SHA/大小校验失败。
- LLM 出现 unknown outcome 或连续 429/5xx。
- MinIO/PostgreSQL 不可用。

验收：

- 人为停止 Worker 后 60 秒内产生告警。
- 人为阻断 RabbitMQ 后能看到队列和 Outbox 积压。
- 指标中不存在 `job_id/run_id/request_id/user_id` 等高基数标签。
- 训练曲线仍只由 PostgreSQL/Artifact 提供，不进入 Prometheus。

### 阶段 4：分布式追踪

目标：在 Grafana 中还原跨 Go、RabbitMQ、Python 和 MinIO 的因果链。

工作项：

1. 接入 Tempo，Alloy 接收 OTLP。
2. Go 增加 HTTP、gRPC、PostgreSQL、S3 和 LLM Client Trace。
3. Python 增加 gRPC、S3、模型加载和关键训练/推理阶段 Trace。
4. 为 Outbox 增加持久 Trace Context，Relay 注入 AMQP Headers。
5. Consumer 为每个 Attempt 创建新 Trace，并用 Span Link 关联消息生产 Trace。
6. 日志自动附加 `trace_id/span_id`，Grafana 支持日志跳转 Trace、Trace 跳转日志。
7. 配置采样：普通成功请求低比例；错误、LLM、Artifact 校验和部署构建保留 100%。
8. 外部 Provider 默认不传播内部 Trace Context，只保存安全的 Provider Request ID。

验收：

- 创建训练后能看到 HTTP → Outbox → RabbitMQ → Claim → Attempt → Artifact → Complete。
- 推理请求能看到模型解析、Artifact 读取、Runtime 调用和事件落库。
- 标注能看到工具选择、增强、检索、VLM和人工确认前的运行阶段。
- 长训练不会形成一个数小时的大 Span。
- Tempo不可用时主业务继续运行。

### 阶段 5：业务审计补全与平台运维入口

目标：用户在 FineVision 中同时看到业务事实与必要技术细节，Grafana 仅保留为内部高级运维工具。

工作项：

1. 丰富 `job_events.payload`，记录 Attempt、Worker、Request、actor 与状态变化。
2. 增加 append-only 的标注任务事件和模型部署事件时间线。
3. 构建结束时将 `build.log` 作为诊断 Artifact 上传 MinIO，登记 SHA、大小和谱系。
4. 新增独立日志与观测控制台：运行总览、分类日志、业务时间线、活动告警和 Trace 抽屉；不进入业务工作台导航与路由。
5. 独立 `observability-console` 进程聚合 Loki、Prometheus、Tempo 和 PostgreSQL，不再维护控制面诊断接口或 Grafana 深链接。
6. 统一业务错误、运行日志和前端诊断编号的关联规则。

验收：

- Loki清空后，训练和标注的业务时间线仍完整。
- Worker节点清理后，历史部署构建日志仍可从MinIO下载并验证。
- 用户无需接触内部堆栈即可复制诊断编号交给运维定位。

## 9. 建议的细粒度提交顺序

每个提交都必须保持主业务可启动、现有测试可运行：

1. `docs: define observability signals and logging contract`
2. `test: add shared redaction and log-contract fixtures`
3. `feat(go): add structured logging bootstrap`
4. `feat(go): add request correlation and HTTP access logs`
5. `feat(go): add gRPC observability interceptors`
6. `feat(python): add JSON logging and context propagation`
7. `feat(training): instrument worker lifecycle without batch noise`
8. `feat(inference): instrument runtime and artifact verification`
9. `feat(annotation): align worker logging with platform contract`
10. `feat(frontend): expose copyable diagnostic request id`
11. `ops: bound Docker log size and retention`
12. `ops: add opt-in Alloy Loki Grafana profile`
13. `ops: provision log data source and query dashboards`
14. `feat(metrics): instrument Go HTTP gRPC and outbox`
15. `feat(metrics): instrument Python workers and runtimes`
16. `ops: add Prometheus exporters dashboards and alerts`
17. `feat(tracing): instrument synchronous Go and Python calls`
18. `feat(tracing): persist and propagate async trace context`
19. `ops: add Tempo and log-trace correlation`
20. `feat(audit): enrich job events and add missing timelines`
21. `feat(artifacts): publish deployment build diagnostics`
22. `feat(frontend): add system diagnostics and deep links`
23. `test: add end-to-end observability acceptance suite`

## 10. 测试计划

### 单元测试

- Go/Python JSON日志字段、级别、时间格式和异常类别。
- Authorization、API Key、数据库URL、图片 data URL、Prompt 的脱敏。
- 最大字段与事件截断。
- HTTP/gRPC Request Context 传播。
- AMQP Headers 注入、提取与 Span Link。
- Route Template 而非原始URL，防止高基数。
- 健康检查与高频Heartbeat采样。
- `contextvars` 在线程与Worker任务之间不串数据。
- 遥测初始化、导出失败不改变业务返回值。

### 集成测试

- 浏览器请求 → Go → PostgreSQL/gRPC → Python Worker 可按诊断编号查回。
- 创建训练 → Outbox → RabbitMQ → Claim → Attempt → Complete 完整关联。
- 推理 → Deployment → Artifact → Runtime → Inference Event 完整关联。
- 标注 → Provider Request ID → 工具 → 检索 → 人工确认完整关联。
- Loki、Prometheus、Tempo分别不可用时业务继续运行。
- RabbitMQ断连、MinIO校验失败、PostgreSQL异常、Worker崩溃和Lease过期。

### 安全与容量测试

- 日志中不可搜索到预置测试密钥和图片内容。
- 使用大量不同 `job_id/request_id` 验证Loki与Prometheus标签基数不增长。
- 验证日志磁盘上限和Loki保留清理。
- 验证错误堆栈、Provider错误和上传异常不会产生超大日志事件。

### 性能验收

- 关闭可观测导出时，日志模块不明显增加请求延迟。
- 开启日志与指标后，HTTP P95延迟增幅目标不超过5%。
- 日志发送不在业务请求中同步等待远端后端。
- 高频训练Heartbeat、Epoch和Batch不会造成日志洪峰。

## 11. 运维与配置

建议新增配置：

```text
FINEVISION_ENVIRONMENT=local
FINEVISION_LOG_LEVEL=INFO
FINEVISION_LOG_FORMAT=json
FINEVISION_OBSERVABILITY_ENABLED=false
OTEL_EXPORTER_OTLP_ENDPOINT=http://alloy:4317
OTEL_TRACES_SAMPLER=parentbased_traceidratio
OTEL_TRACES_SAMPLER_ARG=0.1
```

默认策略：

- 不启动 `observability` Profile 时，仅输出结构化stdout并使用Docker轮转。
- 启动Profile后才发送到Loki、Prometheus和Tempo。
- 开发环境 Grafana 仅在容器网络内可用；独立运维入口统一由 `observability-console` 提供。
- 生产环境必须设置鉴权、TLS、独立持久卷和备份/生命周期策略。

## 12. 完成定义

全部满足以下条件才视为日志系统建设完成：

1. 一个 `request_id` 能定位单次同步请求的跨模块日志。
2. 一个 `job_id` 能定位全部Attempt和相关Go/Python日志。
3. 一次训练、推理、部署或标注失败能在5分钟内确定失败模块和阶段。
4. Outbox、DLQ、Worker离线、Artifact校验失败能够主动告警。
5. Loki、Prometheus、Tempo任一不可用都不影响业务正确性。
6. 删除运行日志后，PostgreSQL业务审计和任务恢复仍然完整。
7. 日志中不存在密钥、图片内容、权重内容和完整Prompt。
8. 平台可从日志跳转 Trace，并按业务 ID 合并业务审计与运行日志。
9. 所有新增模块都有单元测试、故障注入测试和端到端验收记录。

## 13. 本次落地范围

阶段 0–5 已在本地 Compose 形态落地：统一 JSON 日志、诊断编号、Loki、Prometheus、Tempo、异步 Trace Context、追加式审计事件、部署诊断 Artifact，以及带可选 Token 门禁的独立日志与观测控制台。主业务工作台不展示运行日志，Grafana 只作为内部高级工具保留。生产部署仍须按第 11 节替换默认口令、Docker Socket 访问方式、TLS、通知通道和保留周期。

完整验收结果见 `docs/observability-acceptance.md`。
