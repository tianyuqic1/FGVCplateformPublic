# FineVision Agent 服务

Agent 是 FineVision 的独立实验助手服务。它通过对话帮助用户了解数据、诊断训练、准备实验、提交已确认的训练，并跟踪结果。

当前状态：完整首版已实现，包括对话工作区、全局抽屉、持久化队列、模型与 15 个工具循环、确认执行、SSE、训练跟踪和独立数据库容器。启动、限制和验收见 [当前实现与运行](docs/runtime.md)。

## 已确定的架构

- 一个独立 `agent-service` Docker 容器，首期一个 Go 进程同时运行 API 和后台 Runner。
- Agent 拥有独立的 `finevision_agent` 数据库与账号，当前 Compose 使用独立 PostgreSQL 容器与 volume。
- Agent 通过平台 HTTP API 查询资源与提交任务，不访问平台业务表，不导入控制面内部 Go 包。
- 浏览器继续使用平台登录和同源入口；平台代理为 Agent 提供可信身份，后台调用使用受限委托。
- 模型调用经过现有 LLM Gateway；供应商凭据和全局模型配额属于 Gateway。
- 训练、推理和模型产物继续由平台现有计算服务负责。
- 后续可将 Agent API 与 Runner 拆成两个容器，共用 Agent 自己的数据库和同一套契约。

## 文档入口

| 文档 | 内容 |
|---|---|
| [工具层使用说明](docs/tools.md) | 当前 15 个工具、HTTP 与 CLI、确认、并发和结果恢复 |
| [测试集错分分析](docs/test-error-analysis.md) | 第 15 个工具、证据范围、历史报告兼容和验证 |
| [架构与并发设计](docs/architecture.md) | 产品能力、组件、工具、数据模型、并发、状态机、恢复、公共 API 与验收 |
| [平台与模型服务集成契约](docs/integration-contracts.md) | 身份与委托、平台 API、幂等训练提交、跨服务取消、Gateway 协议和数据所有权 |
| [实施计划](docs/implementation-plan.md) | 从契约到只读 Agent、训练提交、跟踪的实施顺序及完成标准 |
| [公共 API 目录](api/README.md) | API 契约归属、路由入口和后续 OpenAPI 生成规则 |
| [数据库迁移目录](migrations/README.md) | 独立数据库、账号、迁移及保留策略 |
| [部署目录](deploy/README.md) | 容器进程、网络、配置、健康检查和未来扩容 |
| [验收目录](tests/README.md) | 契约、并发、权限、断线和故障恢复测试范围 |

## 目录职责

```text
agent/
├── cmd/agent-service/        服务入口和依赖装配
├── cmd/agent-tools/          已实现的工具 CLI
├── internal/
│   ├── service/             会话、Run、方案与预算的持久状态
│   ├── storage/             独立 PostgreSQL、事务准入与检查点
│   ├── httpapi/             对话 API、确认、SSE 与跟踪
│   ├── runner/              领取、调度、租约、恢复与 Watch
│   ├── tools/               工具规范、白名单与有界调度
│   └── adapters/            平台 HTTP 与 LLM Gateway 客户端
├── api/                     Agent 对外接口契约
├── migrations/              Agent 专属数据库 SQL 迁移
├── deploy/                  镜像、本地密钥初始化与部署说明
├── tests/                   集成、并发及恢复验收
└── docs/                    设计与实施说明
```

独立 go.mod/go.sum 固定 pgx 与 UUID 依赖。当前使用显式、可检查点化的工具循环，不依赖 Eino 执行内存；平台仅引用 Agent 的公开协议目录。

会话状态在 internal/service，持久化在 internal/storage，执行在 internal/runner，HTTP 在 internal/httpapi。现有平台 `annotation-agent` 是专用标注模型适配器，与这里的通用 Agent 服务不同。

## 第一版功能范围

已交付训练诊断、数据版本查询、模型对比和实验方案预览，以及确认后的训练提交与用户授权的结果跟踪。并发规则为同会话串行、不同会话有界并发、独立读取可并行、写动作逐项执行。

平台同源代理、短期身份、受限后台委托和 Gateway v2 chat 已实现。供应商级共享配额账本、多实验批量确认和多专家仍为后续扩展；准确边界以运行说明为准。

新增工具：[训练测试集错分分析](docs/test-error-analysis.md)。
