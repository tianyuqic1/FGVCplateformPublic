# 可观测系统本地验收记录

验收日期：2026-09-25
范围：结构化日志、指标、告警、Trace、异步上下文、审计迁移、独立日志与观测控制台和 Compose 配置。

## 自动化回归

| 检查 | 结果 |
|---|---|
| `cd go && go test ./...` | 通过 |
| `uv run pytest -q` | 97 passed、24 skipped；仅现有 ONNX deprecation warning |
| `npm --prefix frontend run build` | 通过 |
| `npm --prefix frontend run smoke:diagnostics-client` | 通过 |
| 独立控制台静态页面与 Token 门禁测试 | 通过 |
| 主 Compose、可观测 Compose、推理 Compose 静态解析 | 通过 |
| Prometheus 配置和 7 条告警规则 | `promtool` 通过 |
| Loki、Tempo、Alloy 配置校验 | 通过 |
| Grafana Dashboard JSON 校验 | 通过 |
| Alembic 全量升级、0022 降级、再次升级 | 临时 PostgreSQL 通过 |

跳过的 Python 用例由可选硬件/模型依赖控制，不属于日志系统失败。

## 真实组件联调

使用固定版本的 Alloy、Loki、Prometheus、Tempo、Grafana 和 `observability-console` 启动真实容器进行回查：

- Loki、Prometheus、Tempo、PostgreSQL 和聚合服务 readiness/health 均通过。
- Docker 容器生成 JSON 探针日志后，可从 Loki `query_range` 按 `service` 与 `request_id` 查询到原始事件。
- Python SDK 生成 OTLP Trace，经 Alloy 转发后，可按 Trace ID 从 Tempo 查询到 `service.name=observability-acceptance` 和 `acceptance.trace` Span。
- Grafana 中 Prometheus、Loki、Tempo 三个数据源健康状态均为 `OK`。
- Grafana 已加载包含 13 个核心面板的 `FineVision · 系统可观测性` Dashboard。
- Prometheus 已加载全部规则；隔离验收没有启动业务栈，因此 `RuntimeTargetDown` 触发符合预期，其余规则健康。
- `GET /api/observability/overview` 返回 4 个组件、8 个聚合指标及训练/标注/部署/Outbox 队列摘要。
- 独立控制台直接读取同源聚合 API；分类日志可按业务模块筛选并使用游标加载更早记录。
- 主业务工作台已移除日志路由、导航和代理；Grafana 已取消宿主机端口映射，内部 Dashboard 仍可供高级运维验收。
- 可选 Token 门禁覆盖页面、静态资源和查询 API；健康检查与 Prometheus 抓取端点保持可用。
- 隔离容器实测：未授权页面和 API 返回 `401`，`/health` 返回 `200`，Bearer API 返回 `200`，`?token=` 登录返回 `303` 并换取会话 Cookie，Cookie 后续访问返回 `200`。

## 安全与降级

- Go 和 Python 均覆盖 Authorization、API Key、数据库 URL、图片 data URL、Prompt、嵌套对象和长字段脱敏测试。
- Prometheus 标签测试确认不包含 `job_id`、`request_id` 等业务 UUID。
- OTLP Exporter 或 Python metrics 监听初始化失败时返回降级模式，不中断业务进程。
- 外部 HTTP Trace 使用 public endpoint 语义：外来 Trace 只作为 Link，不直接成为内部父 Trace。
- Alloy 不采集自身监控容器；首次启用丢弃安装前历史日志，并持久化 Docker 读取位置，避免递归和历史回放风暴。
- 前端统一 API 错误会显示诊断编号，并通过全局错误条提供“复制编号”操作。
- 聚合 API 只允许固定实体类型、最长 7 天范围、最多 200 条日志和转义后的 160 字符搜索文本，不向浏览器暴露 LogQL、PromQL 或 SQL。

## 运维说明

本地独立控制台入口为 `http://localhost:9400`，健康检查为 `http://localhost:9400/health`。Prometheus `http://localhost:9090` 与 Alloy `http://localhost:12345` 只用于高级运维，Grafana 仅在容器网络内可用。生产上线前仍须完成公网 TLS、身份系统/RBAC、独立凭据、Alertmanager 通知接收方和受限 Docker 日志采集方式配置。
