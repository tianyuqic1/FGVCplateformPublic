# FineVision Phase 2 验收记录

> 验收日期：2026-09-08
>
> 分支：`codex/phase2-refactor`
>
> 结论：Phase 2 约定范围已实现并通过自动化、真实基础设施和页面响应式验收

## 1. 交付范围

- A「科研实验工作台」已迁入生产 Design Token、Shell、组件和 feature 页面；临时 A/B/C 原型与
  `/prototype/phase2-style` route 已删除。
- `torch_linear_adam` 逐 epoch 上报 append-only Metric Point；Go 提供 cursor API，前端使用
  ECharts 动态展示 loss/accuracy，并在终态停止轮询。
- Model Version 提供独立列表、详情、2–5 项比较、candidate/staging/production/archive 状态、
  Dataset-scoped `champion/challenger` alias 和审计事件。
- 训练选择器只提供 DINOv3 ViT-S、ImageNet ViT-S、ImageNet ResNet-50 三项稳定 backbone。
- 新增两份 ImageNet 权重及许可证，通过 Git LFS、manifest 与 MinIO promotion 管理。
- Trained Model Artifact 使用 Dataset Version/Training Run/Model Version 逻辑 scope 与 SHA-256
  物理内容寻址两层模型；Inference Runtime 与引用安全 GC 均 fail closed。

## 2. 数据库与接口

Alembic migration head：

```text
20260908_0012 (head)
```

本次新增或加固：

- `training_metric_points`
- `model_version_aliases`
- `model_version_events`
- Model Version backbone/evaluation metadata
- `GET /api/training-runs/{run_id}/metrics`
- Model Version list/detail/compare/promote/archive/alias API
- gRPC `ProgressRequest.metric_points`

真实 PostgreSQL adapter 验证了 Metric Point 写入/读取、Model Version 创建、两段式 promotion、
alias 和 Artifact 引用保护。重复 Metric Point 按 attempt/name/step 幂等，stale attempt 被 fencing
拒绝。

## 3. 权重与 MinIO 验收

| Stable key | SHA-256 | 大小（bytes） | MinIO key 前缀 |
| --- | --- | ---: | --- |
| `dinov3_vits16_lvd1689m` | `2a1ec16ae28ffa07bc0ead0241ee7df9fc26451fe6f9f839b7b3afa0a906b040` | 86,362,376 | `pretrained/dinov3/2a/` |
| `imagenet_vits16_augreg_in21k_ft_in1k` | `79c03c635cdfd798a364a9d8c4e5c0b7255b975ea2c9616046d4f77ab01435aa` | 88,216,496 | `pretrained/imagenet/vit-small/79/` |
| `imagenet_resnet50_a1_in1k` | `773525d5821de224f8f30c33377b7a795d7863e08522698200d3217d3f2a41bb` | 102,469,840 | `pretrained/imagenet/resnet-50/77/` |

`docker compose run --rm pretrained-weight-init` 实际上传三份权重，并完成源文件 SHA/大小、远端对象
SHA/大小读回校验。实时 `GET /api/model-weights` 返回三项且全部 `cached=true`。

## 4. 自动化结果

### Go

```bash
FINEVISION_TEST_GO_DATABASE_URL=... \
FINEVISION_TEST_S3_ENDPOINT=http://localhost:9000 \
FINEVISION_TEST_RABBITMQ_URL=... \
go test -count=1 ./...
go test -race ./internal/training ./internal/outbox ./internal/httpapi
go vet ./...
```

结果：全部通过。测试包含真实 PostgreSQL、MinIO 和 RabbitMQ adapter。

### Python

```bash
uv run --group dev pytest -q
uv run --extra dinov3 --group dev pytest -q \
  backend/tests/test_dinov3_vits_training_integration.py \
  backend/tests/test_phase2_backbone_integration.py
```

结果：全量 `54 passed, 37 skipped`；显式模型验收 `3 passed`。其中：

- DINOv3 ViT-S：小型 ImageFolder 完整 Feature + head 链路。
- ImageNet ViT-S：小型 ImageFolder 完整 Feature + head 链路。
- ImageNet ResNet-50：权重加载与单 batch 2048 维 Feature smoke。
- 默认跳过项是需要显式外部数据库或其他可选基础设施的历史集成套件，不包含上述 Phase 2 模型 gate。

### Frontend

```bash
npm run build
npm run smoke:api-client
npm run smoke:jobs-client
npm run smoke:training-client
npm run smoke:phase2-client
npm run smoke:inference-client
npm run smoke:abstention-client
npm run smoke:review-client
npm run smoke:llm-client
npm run smoke:routes
```

结果：全部通过。Vite production build 将 React、ECharts 和 zrender 拆分为独立 chunk；Training、
Training Detail、Model list/detail/compare 等生产 route 均返回 200。

## 5. 运行态与视觉验收

Compose 实际重建后，下列服务均常驻运行：

- Go Control Plane
- Go Outbox Relay
- Go LLM Gateway
- Python Training Worker
- Python Inference Runtime
- React/Vite Frontend
- PostgreSQL、RabbitMQ、MinIO

运行态检查：

- `GET http://localhost:8001/api/health` 返回 `status=ok`。
- `GET /api/model-weights` 返回且仅返回三项批准 backbone。
- `GET /api/model-versions` 能读取数据库中的版本、lineage、alias、Artifact 和审计事件。
- Python Training Worker 启动保持常驻；共享 store import 不再隐式启动迁移期 FastAPI。

页面实际以 1440×1000 和 620×1000 两个 viewport 截图检查：

- 宽屏使用固定侧栏、四列指标和左右训练工作区。
- 窄屏切换为图标导航、两列指标和纵向 panel，无横向页面溢出。
- 状态不只依赖颜色，同时使用文字、图标和边框；可聚焦表格行支持 Enter 打开。

本地入口：

```text
Frontend             http://localhost:5173
Go Control Plane     http://localhost:8001
MinIO Console        http://localhost:9001
RabbitMQ Management  http://localhost:15672
```

## 6. 已知边界

- Phase 2 不部署 MLflow、TensorBoard 或第二套 tracking/registry 服务。
- 旧数据库中二期 migration 之前创建的 Model Version 可能缺少 protocol fingerprint；比较时会明确
  标为不可比较，不会与新版本混排排名。
- 训练曲线采用 2 秒 cursor 轮询，不包含 WebSocket/SSE。
- 二期不支持 ViT-B/L、ResNet-18/101、其他 backbone、全量 backbone fine-tune、分布式训练或
  自动以 accuracy 提升 production。
- `npm ci` 当前报告上游依赖审计告警；本次验收没有执行自动破坏性版本升级，production build 与
  功能 smoke 均通过。依赖升级应独立评估并提交。
