# FineVision 部署配置清单

> 状态：当前 Phase 1/Phase 2 开发环境配置基线
>
> 更新日期：2026-09-08

本文档回答“部署或启动 FineVision 前需要配置什么”。本地开发可以使用仓库默认值；测试、预发布和生产环境必须更换所有密码、内部 token、域名和持久化策略。

## 1. 最小启动清单

在启动前确认：

- Docker Engine 与 Docker Compose 可用。
- Git LFS 已安装，并已拉取仓库中的批准预训练权重。
- 根目录存在未提交的 `.env`，且至少配置数据库、RabbitMQ、MinIO 和内部 LLM token。
- 需要外部 LLM Assistance 时，配置 provider 地址、模型和 API key；不使用时可不配置 API key。
- 需要 GPU 时，宿主机已安装 NVIDIA 驱动与 NVIDIA Container Toolkit，并使用 GPU Compose overlay。
- PostgreSQL、RabbitMQ 与 MinIO 使用持久卷，磁盘空间和备份位置已经确认。

本地初始化：

```bash
git lfs install
git lfs pull
cp .env.example .env
docker compose config
docker compose up -d --build
```

GPU 环境：

```bash
docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d --build
```

## 2. 必须配置的环境变量

### 2.1 PostgreSQL

| 变量 | 用途 | 本地示例 | 生产要求 |
| --- | --- | --- | --- |
| `DATABASE_URL` | Alembic migration 使用的 SQLAlchemy URL | `postgresql+psycopg://...` | 使用专用 migration 账号或受控发布账号 |
| `FINEVISION_DATABASE_URL` | Go Control Plane/Outbox 使用的 pgx URL | `postgres://...` | 不得使用默认密码；启用 TLS |
| `FINEVISION_TEST_DATABASE_URL` | 集成测试数据库 | 独立 `finevision_test` 数据库 | 不能指向生产库 |

生产建议至少拆分三个角色：

- `finevision_migrate`：只由发布流程临时使用，可执行 schema migration。
- `finevision_control`：Go Control Plane 与 Outbox Relay 使用，拥有业务表所需读写权限。
- Python Compute Plane：不分配业务数据库凭据；只通过 gRPC 与 ArtifactStore 工作。

还需要确认：

- PostgreSQL 版本与 Compose 固定版本兼容。
- 自动备份、PITR/归档、保留周期和恢复演练。
- 连接数上限、Go pgx pool 大小、statement timeout。
- Alembic 在应用服务启动前完成，且同一时刻只有一个 migration 执行器。

### 2.2 MinIO / S3-compatible ArtifactStore

| 变量 | 用途 | 本地示例 | 生产要求 |
| --- | --- | --- | --- |
| `FINEVISION_S3_ENDPOINT` | S3-compatible endpoint | `http://localhost:9000` | 使用内部 HTTPS 地址 |
| `FINEVISION_S3_REGION` | S3 region | `us-east-1` | 与实际对象存储一致 |
| `FINEVISION_S3_ACCESS_KEY` | ArtifactStore access key | 本地开发账号 | 使用独立 service account |
| `FINEVISION_S3_SECRET_KEY` | ArtifactStore secret key | 本地开发密码 | 从 secret manager 注入，不写入 Git |
| `FINEVISION_ARTIFACT_BUCKET` | Feature、模型、报告和权重对象 | `finevision-artifacts` | 私有、启用 versioning |

当前 Compose 会创建三个私有 bucket：

- `finevision-datasets`：Dataset Version 的图片对象与 manifest。
- `finevision-artifacts`：Feature、训练模型、报告、Model Bundle 和批准预训练权重。
- `finevision-uploads`：尚未完成 ingest 的上传对象。

MinIO/S3 上线前必须确认：

- bucket 默认私有，禁止 anonymous read/write。
- 开启 versioning；已发布 Dataset Version 和 Artifact Object 不允许原地覆盖。
- 上传临时对象和无主 staging 对象配置 lifecycle 清理策略。
- 配置服务端加密、磁盘冗余、容量告警、备份与恢复演练。
- Control Plane、Training Worker、Inference Runtime 使用最小权限 service account。
- 前端不持有永久 access key；需要浏览器上传/下载时只使用短期 presigned URL。
- 不能使用 ETag 代替 SHA-256。每个 ready Artifact 必须同时保存并验证 `sha256` 与 `size_bytes`。
- 生产部署固定并审查 MinIO-compatible 发行版、镜像版本、升级策略和许可证；业务代码只依赖 S3-compatible interface。

### 2.3 RabbitMQ

| 配置 | 当前开发值 | 生产要求 |
| --- | --- | --- |
| `FINEVISION_RABBITMQ_URL` | `amqp://...@localhost:5672/` | 独立账号、vhost、TLS，凭据由 secret manager 注入 |
| Exchange | `finevision.training.v1` | durable |
| Queue | `finevision.training.v1` | durable；生产建议 quorum queue |
| Routing key | `training.ready` | 与 Outbox Relay 保持一致 |
| DLX | `finevision.training.dlx.v1` | durable |
| DLQ | `finevision.training.dlq.v1` | 配置告警与人工 reconciliation |

还需要确认：

- Training Worker 使用 `prefetch=1`。
- 消息 delivery mode 为 persistent，Outbox publisher 启用 mandatory routing 和 confirm。
- RabbitMQ 数据目录使用持久卷，并配置容量、水位、queue depth 和 consumer 告警。
- DLQ 只处理 poison message；正常训练失败和重试状态必须写回 PostgreSQL。

### 2.4 Control Plane 与内部通信

| 变量 | 用途 | 建议 |
| --- | --- | --- |
| `FINEVISION_HTTP_ADDRESS` | Go 公开 HTTP 监听地址 | 容器内 `:8000` |
| `FINEVISION_GRPC_ADDRESS` | Training Lifecycle 内部 gRPC | 容器内 `:9000`，不要向公网暴露 |
| `FINEVISION_CONTROL_PLANE_GRPC` | Python Worker 访问 Go gRPC | Compose 内 `go-control-plane:9000` |
| `FINEVISION_LEASE_TTL` | Job Attempt lease | 当前 `150s`；应结合 heartbeat 和故障测试调整 |
| `FINEVISION_INFERENCE_GRPC_ADDRESS` | Python Inference Runtime 监听地址 | Compose 内 `[::]:9100` |
| `FINEVISION_ARTIFACT_CACHE_DIR` | 按 SHA-256 命名的本地缓存 | 使用独立持久卷并监控容量 |

生产环境应通过内部网络策略限制 gRPC 端口，只允许对应服务互访。若部署环境不提供可信的容器网络身份，需要增加 mTLS 或短期 service credential。

### 2.5 外部 LLM（可选）

只有 `go-llm-gateway` 可以读取以下配置：

| 变量 | 用途 |
| --- | --- |
| `FINEVISION_LLM_PROVIDER` | provider 显示名称 |
| `FINEVISION_LLM_BASE_URL` | OpenAI-compatible provider base URL |
| `FINEVISION_LLM_MODEL` | 服务端允许使用的模型 |
| `FINEVISION_LLM_API_KEY` | provider API key |
| `FINEVISION_LLM_INTERNAL_TOKEN` | Control Plane 调用 LLMGateway 的内部认证 token |
| `FINEVISION_LLM_GATEWAY_URL` | Control Plane 访问 Gateway 的内部 URL |

要求：

- API key 与内部 token 不得进入前端 bundle、数据库、Job payload 或日志。
- 公开请求不能覆盖 provider、base URL、model 或 credential。
- Gateway 配置出站超时、有限重试、限流和用量告警。
- 不使用 LLM 时可以不配置 provider API key，但相关接口应返回明确的“未配置”状态。

### 2.6 计算资源

CPU 环境：

- `FINEVISION_COMPUTE_DEVICE=cpu`
- `FINEVISION_LINEAR_HEAD_DEVICE=cpu`

GPU 环境：

- `FINEVISION_COMPUTE_DEVICE=cuda`
- `FINEVISION_LINEAR_HEAD_DEVICE=cuda`
- 安装与容器 CUDA runtime 兼容的 NVIDIA 驱动。
- 安装 NVIDIA Container Toolkit，并先用基础 CUDA 容器验证 GPU 可见性。
- 为 Worker 设置显存、并发数和队列路由策略；一期/二期单 Worker 保持 `prefetch=1`。

可选计算参数：

- `FINEVISION_FEATURE_BATCH_SIZE`（Compose 默认 `8`）
- Feature 提取 batch size、输入尺寸、linear head batch size 在 Training Run 配置中记录，而不是作为不可审计的全局默认值覆盖。

## 3. Git LFS 与预训练权重

必须配置或确认：

- 开发机、CI 和发布机均安装 Git LFS。
- GitHub 仓库有足够的 LFS storage/bandwidth 配额。
- clone 后执行 `git lfs pull`，不能只得到 pointer 文件。
- `weights/manifest.json` 中的 upstream revision、license、SHA-256、大小和 LFS path 与实际对象一致。
- 新权重进入公开仓库前完成再分发许可证审查。
- Weight Promotion 先校验 LFS 文件，再上传 MinIO/S3 content-addressed key，并流式读回校验。
- Python Runtime 不执行 `git lfs pull`；运行时只从 ArtifactStore materialize 已批准权重。

二期固定检查以下三项，不能用 mutable upstream alias 代替 manifest 身份：

| `backbone_key` | LFS 文件 | 运行时 MinIO 前缀 |
| --- | --- | --- |
| `dinov3_vits16_lvd1689m` | `weights/pretrained/dinov3/vit_small_patch16_dinov3.lvd1689m.safetensors` | `pretrained/dinov3/{sha-prefix}/{sha}` |
| `imagenet_vits16_augreg_in21k_ft_in1k` | `weights/pretrained/imagenet/vit_small_patch16_224.augreg_in21k_ft_in1k.safetensors` | `pretrained/imagenet/vit-small/{sha-prefix}/{sha}` |
| `imagenet_resnet50_a1_in1k` | `weights/pretrained/imagenet/resnet50.a1_in1k.safetensors` | `pretrained/imagenet/resnet-50/{sha-prefix}/{sha}` |

## 4. 网络与端口

本地默认端口：

| 服务 | 端口 | 是否建议公网开放 |
| --- | --- | --- |
| Frontend | `5173` | 生产应由反向代理/静态站点提供 |
| Go Control Plane | `8001 -> 8000` | 只通过 HTTPS gateway 暴露 |
| PostgreSQL | `5432` | 否 |
| RabbitMQ AMQP | `5672` | 否 |
| RabbitMQ Management | `15672` | 否，仅运维网络 |
| MinIO S3 | `9000` | 否，仅内部服务或受控 gateway |
| MinIO Console | `9001` | 否，仅运维网络 |
| Training Lifecycle gRPC | `9000`（容器内部） | 否 |
| Inference Runtime gRPC | `9100`（容器内部） | 否 |

生产环境需要配置：TLS 证书、反向代理、允许来源、上传体积限制、请求超时、内部 DNS 和网络策略。

## 5. 持久化与容量

需要规划的持久数据：

- PostgreSQL 数据与 WAL/备份。
- RabbitMQ durable queue 数据。
- MinIO/S3 Dataset 和 Artifact Object。
- Compute SHA-256 cache；该缓存可以重建，但删除前要确保 canonical Artifact 可用。
- GitHub LFS 对象和配额。

容量估算至少包含：Dataset 原图、Dataset Version 增量、Feature 矩阵、每个 Model Version 的模型/报告、预训练权重、对象 versioning 放大、缓存副本和备份副本。

## 6. 上线前验收

- `docker compose config` 无错误。
- Alembic migration 能在空库升级到 head，并有备份/恢复方案。
- Go health/readiness、Python Inference Runtime health 均通过。
- PostgreSQL、RabbitMQ、MinIO 不再使用仓库默认密码。
- 三个 bucket 私有并启用 versioning。
- 三份受管 LFS 权重、MinIO 对象和本地 SHA cache 的 SHA-256/大小一致。
- Outbox → RabbitMQ → Worker → gRPC Complete 链路通过。
- 篡改 Artifact 或 cache 后推理/训练能够 fail closed。
- LLM key 只存在于 LLMGateway。
- 日志不包含 secret、完整 prompt、原始图片字节或本地绝对路径。
- 数据库、对象存储和 RabbitMQ 的监控、告警和备份已启用。
