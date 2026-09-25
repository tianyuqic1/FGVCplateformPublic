# FineVision 统一观测系统

FineVision 将日志、指标、调用链和业务审计组合成一个独立的运维入口。日志不展示在业务工作台中，开发与运维人员通过专用 URL 查看，也不需要分别登录 Loki、Prometheus、Tempo、Grafana。

## 架构与边界

```text
浏览器 http://localhost:9400
        │
        ▼
observability-console (:9400)
   ├── Loki：结构化运行日志、request_id / job_id 查询
   ├── Prometheus：运行指标、队列指标和活动告警
   ├── Tempo：按 trace_id 读取同步与异步调用链
   └── PostgreSQL：训练、标注、部署的追加式业务审计
```

`observability-console` 是独立 Go 进程，同时提供专用 Web UI 和受控查询 API，只做聚合、字段规范化和业务分类，不替代底层存储引擎，也不进入 FineVision 主站路由。Loki、Prometheus、Tempo 任一不可用时不会影响训练、推理、标注或发布主链路。Grafana 保留为容器网络内的高级排障工具，但不再是产品入口。

四类信号各司其职：

- PostgreSQL 事件表保存训练、标注和部署的最终业务事实，可用于恢复与审计。
- JSON stdout 保存进程、请求、重试和异常；Docker 单容器保留 `10 MiB × 5`。
- Prometheus 保存低基数运行指标与告警，不写入 `job_id`、`request_id` 或图片级结果。
- Tempo 保存同步 Trace 和 RabbitMQ 异步 Span Link；日志中的 `trace_id` 可在侧边抽屉查看调用链。
- 部署构建的 `build.log` 作为带 SHA-256、大小和谱系的 `deployment_log` Artifact 保存到 MinIO。

## 启动与入口

默认业务栈只输出结构化日志和 `/metrics`，不依赖观测后端：

```bash
docker compose up -d
```

启用完整本地观测栈：

```bash
FINEVISION_OBSERVABILITY_ENABLED=true \
docker compose -f docker-compose.yml -f compose.observability.yml \
  --profile observability up -d --build
```

| 入口 | 地址 | 面向对象 |
|---|---|---|
| 独立日志与观测控制台 | <http://localhost:9400> | 开发与运维 |
| 聚合服务健康检查 | <http://localhost:9400/health> | 启动探针与联调 |
| Prometheus | <http://localhost:9090> | 高级运维 |
| Alloy 管理页 | <http://localhost:12345> | 高级运维 |

Grafana 不映射宿主机端口，只在 Compose 网络中供高级运维或数据源验收使用；内部账号由 `FINEVISION_GRAFANA_USER` 和 `FINEVISION_GRAFANA_PASSWORD` 配置。生产环境仍须使用独立密钥和受控访问。

### 可选 Token 门禁

本地默认不设置 Token，并且控制台只绑定 `127.0.0.1`。需要访问保护时，在未提交的 `.env` 中配置：

```bash
FINEVISION_OBSERVABILITY_TOKEN=请替换为足够长的随机令牌
```

启用后，页面、静态资源和 `/api/observability/*` 均需要认证，`/health` 与 `/metrics` 保持为容器探针接口。浏览器首次访问会显示令牌输入页；也可以使用 `http://localhost:9400/?token=...`，验证后服务立即移除 URL 参数，并写入 8 小时、`HttpOnly`、`SameSite=Strict` 的会话 Cookie。自动化客户端可发送 `Authorization: Bearer <token>`。

## 页面能力

- **运行总览**：核心后端状态、请求速率、5xx 比例、Outbox、Worker、LLM、Artifact 和死信指标，以及训练/标注/部署队列状态。
- **分类日志**：按控制面、训练、推理、模型部署、AI 标注、大模型和硬件节点分类；支持级别、结果、时间和关键词筛选，并采用游标式分页。
- **业务时间线**：输入训练任务、标注任务或部署任务 ID，合并 PostgreSQL 审计事实与 Loki 运行日志。
- **告警中心**：展示 Prometheus 当前活动告警。
- **Trace 抽屉**：从日志或时间线直接读取对应 Tempo Trace，不离开平台。

## 定位方式

1. 浏览器报错先复制 `request_id`（界面显示为“诊断编号”）。
2. 进入“分类日志”，搜索 `request_id`、`job_id`、`attempt_id`、`deployment_id` 或 `task_id`。
3. 日志存在 `trace_id` 时直接打开 Trace 抽屉。
4. 对训练、标注和部署，进入“业务时间线”查看审计事实和运行日志的统一时间序列。
5. Loki/Tempo 已清理时，PostgreSQL 审计事实仍保留；页面会标记为部分结果。

聚合 API 只接受固定业务实体类型、最长 160 字符搜索文本、最长 7 天时间范围和最多 200 条日志；不会把任意 LogQL、PromQL 或 SQL 暴露给浏览器。

## 指标与首批告警

- HTTP 请求量、状态、P95/P99 延迟。
- Worker 工作成功/失败、耗时、在途任务、心跳年龄。
- Outbox pending、最老事件年龄、发布失败。
- RabbitMQ、PostgreSQL 和 MinIO 自身指标。
- LLM 成功、429、5xx、失败、延迟与输入/输出 Token。
- Artifact 完整性校验失败。

预置规则包括 API 5xx 比例持续超过 2%、Runtime 不可达、Outbox 最老事件超过 60 秒、RabbitMQ 死信、Worker 连续失败、Artifact 校验失败，以及 LLM Provider 连续异常或 unknown outcome。接入邮件、Webhook 等外部通知时再增加 Alertmanager，避免把通知凭据写入仓库。

## 日志安全与保留

日志禁止保存 Authorization、API Key、数据库口令、图片 data URL、完整 Prompt、Provider 响应或权重内容。敏感键统一替换为 `[REDACTED]`，普通长字段最多保留 4 KiB，异常最多保留 16 KiB。

- Loki、Tempo 开发保留 7 天；Prometheus 保留 15 天。
- 独立控制台、Prometheus、Alloy 仅绑定 `127.0.0.1`；Grafana 不暴露宿主机端口。
- 本地 Alloy 只读 Docker Socket；生产部署应改用受限 Socket Proxy、journald 或节点日志采集。
- 删除 Loki/Tempo/Prometheus 卷不会删除业务状态、数据集、模型或审计记录。

## 迁移与自检

```bash
uv run alembic upgrade head
docker compose -f docker-compose.yml -f compose.observability.yml --profile observability config --quiet
cd go && go test ./...
cd .. && uv run pytest -q
npm --prefix frontend run build
scripts/smoke-demo.sh --contracts-only
```

迁移 `20260920_0022` 增加异步 Trace Context，以及标注和部署的追加式事件表。迁移前请按常规流程备份 PostgreSQL。
