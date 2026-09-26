# FineVision

[![CI](https://github.com/tianyuqic1/FGVCplateformPublic/actions/workflows/ci.yml/badge.svg)](https://github.com/tianyuqic1/FGVCplateformPublic/actions/workflows/ci.yml)

FineVision 是一个面向细粒度图像分类的全流程工程平台，覆盖数据集版本管理、可配置训练、模型发布、多后端推理、在线弃权、人工复核与 AI 辅助标注。系统以 **Go 控制面 + Python 计算面** 为核心，通过 PostgreSQL、RabbitMQ 和 MinIO 把业务状态、长任务和二进制产物解耦。

> 当前仓库以本地开发和单机 Docker Compose 为主要运行形态。工作台现有本地账号、三角色授权与会话保护；公网部署前仍需配置 HTTPS、密钥管理、对象权限、备份与网络边界，不能直接使用示例口令。

## 核心能力

- **数据资产**：ImageFolder 导入、后台校验、样本预览、不可变版本、数据血缘与训练集扩充。
- **训练策略**：DINOv3 ViT-S 冻结骨干或 LoRA R8/R16；ImageNet ViT-S、ResNet-50 全量训练；独立学习率、输入尺寸和常用数据增强。
- **模型注册表**：按数据集及数据版本聚合；语义版本、指标对比、发布门禁和产物完整性校验。
- **多后端推理**：完整 PT、ONNX FP32/FP16、TensorRT，以及面向华为 Ascend 的独立 Worker 接口。
- **选择性推理**：置信度、margin、能量分数与类别阈值组合；低置信样本进入人工复核。
- **AI 标注**：Qwen 主体定位、SAM2 分割、受限图像增强、CLIP/Qdrant 图像记忆、文本原型 RRF、视觉大模型 Top-10 和人工确认。
- **闭环发布**：已确认标注进入待发布区，可创建新数据集或追加到既有数据集的新版本；推理反馈只进入下一版本训练集。
- **工程可观测性**：统一 JSON 日志、诊断编号、Prometheus 告警、Loki 检索、Tempo 跨 HTTP/gRPC/RabbitMQ 链路，以及可恢复的 PostgreSQL 审计事件。

## 系统架构

~~~
React / Vite
      │ HTTP / JSON / multipart
      ▼
Go Control Plane ───── Go LLM Gateway / Eino ───── Multimodal Provider
      │
      ├── PostgreSQL：状态、血缘、事件、策略与 Outbox
      ├── RabbitMQ：训练与部署任务
      └── MinIO：数据集、预训练权重、模型和报告
              ▲
              │ verified artifact descriptors
Python Compute Services
      ├── Training Worker
      ├── Artifact / Export Worker
      ├── Inference Runtime（ORT / TensorRT / Ascend）
      └── Annotation Runtime（VLM / SAM2 / CLIP / Qdrant）
~~~

控制面只保存业务状态和对象描述符；大文件写入 MinIO 后必须校验 SHA-256 与大小才能登记。RabbitMQ 提供至少一次投递，PostgreSQL 中的 job、attempt 和 generation 才是权威执行状态。

## 快速启动

### 环境要求

- Docker Engine 与 Docker Compose v2
- Git LFS
- 可选：NVIDIA Container Toolkit（GPU 训练或 TensorRT）
- 可选：Python 3.11+、uv、Go 1.26、Node.js 22+（宿主机开发）

### 获取代码和权重

~~~bash
git clone git@github.com:tianyuqic1/FGVCplateformPublic.git
cd FGVCplateformPublic
git lfs pull
cp .env.example .env
~~~

.env.example 提供可启动的本地开发默认值。接入外部视觉大模型时，只在被 Git 忽略的 .env 中填写 FINEVISION_LLM_API_KEY，不要把密钥写入前端、日志、测试快照或提交记录。

### 启动 CPU 开发栈

~~~bash
scripts/demo-up.sh
~~~

常用入口：

| 服务 | 地址 |
|---|---|
| Web 工作台 | <http://localhost:5173> |
| Go API | <http://localhost:8001/api/health> |
| Swagger API 文档 | <http://localhost:8001/swagger/> |
| OpenAPI YAML | <http://localhost:8001/openapi/finevision.yaml> |
| 用户与权限 API | <http://localhost:8001/openapi/auth.yaml> |
| MinIO Console | <http://localhost:9001> |
| RabbitMQ Console | <http://localhost:15672> |
| 展示文档 | <http://localhost:5180> |
| 独立日志与观测控制台 | <http://localhost:9400> |
| 观测服务健康检查 | <http://localhost:9400/health> |

Swagger 与 OpenAPI 契约现在仅管理员登录后可查看；匿名访问返回 401。工作台与 API 应经同源反向代理提供服务，便于会话 Cookie 与 CSRF 校验正常工作。

启用 NVIDIA GPU：

~~~bash
scripts/demo-up.sh --gpu
~~~

仅启动训练 Worker 的 CDI GPU 覆盖：

~~~bash
docker compose -f docker-compose.yml -f compose.training-gpu.yml up -d python-training-worker
~~~

多后端推理与硬件要求见 [推理部署文档](docs/inference-deployments.md)。

### 首次登录与用户管理

数据库迁移完成后，先创建首位管理员。管理员密码从终端安全读取，不放在命令行或 `.env` 中。使用 Docker Compose 时，先启动服务，再执行：

~~~bash
docker compose run --rm --no-deps --entrypoint /usr/local/bin/finevision-admin go-control-plane --email admin@example.com --name 平台管理员
~~~

也可在宿主机配置 `FINEVISION_DATABASE_URL` 后，于 `go/` 目录执行 `go run ./cmd/finevision-admin --email admin@example.com --name 平台管理员`。此命令可重置指定管理员密码并撤销该账号旧会话，应限制为运维人员执行。随后访问工作台登录页。普通用户可自行申请注册，但在管理员审核前不能登录；管理员在**用户管理**页分配角色并启用。权限矩阵、会话机制和操作说明见[用户认证与权限文档](docs/auth.md)。

可选启用集中日志、指标与 Trace：

~~~bash
FINEVISION_OBSERVABILITY_ENABLED=true \
docker compose -f docker-compose.yml -f compose.observability.yml \
  --profile observability up -d
~~~

运行日志不出现在业务工作台中，日常排障使用独立的日志与观测控制台，无需分别登录 Loki、Prometheus、Tempo 或 Grafana。设置 `FINEVISION_OBSERVABILITY_TOKEN` 后，控制台页面和查询 API 必须通过 Token 验证；Grafana 仅作为容器网络内的高级运维工具保留。字段、查询、告警、保留和生产安全边界见 [可观测性操作手册](docs/observability.md)。

## 配置清单

| 配置 | 是否必需 | 用途 |
|---|---|---|
| DATABASE_URL / FINEVISION_DATABASE_URL | 是 | Alembic 与 Go 控制面数据库连接 |
| FINEVISION_ENVIRONMENT | 本地开发建议设置 `local` | `local` 允许 HTTP 本地会话 Cookie；其他值要求 HTTPS Secure Cookie |
| FINEVISION_S3_* | 是 | MinIO/S3 endpoint、区域和访问凭据 |
| FINEVISION_ARTIFACT_BUCKET | 是 | 模型与报告对象桶 |
| FINEVISION_RABBITMQ_URL | 是 | 训练、发布和部署消息 |
| FINEVISION_LLM_INTERNAL_TOKEN | 使用 LLM 时 | 控制面到 LLM Gateway 的内部鉴权 |
| FINEVISION_LLM_API_KEY | 使用远程 VLM 时 | 仅注入 Go LLM Gateway |
| FINEVISION_*_DEVICE | 否 | CPU/CUDA 计算设备选择 |
| FINEVISION_DEPLOYMENT_TOKEN | 硬件 Worker 时 | 部署 Worker 与控制面的内部鉴权 |
| FINEVISION_TENSORRT_* / FINEVISION_ASCEND_* | 对应后端时 | 硬件目标地址与 profile |
| FINEVISION_LOG_LEVEL / FINEVISION_LOG_FORMAT | 否 | 结构化日志级别与格式 |
| FINEVISION_OBSERVABILITY_ENABLED | 否 | 启用 OTLP Trace 导出；默认关闭 |
| FINEVISION_OBSERVABILITY_TOKEN | 否 | 独立日志控制台访问令牌；本地默认关闭门禁 |
| OTEL_EXPORTER_OTLP_ENDPOINT / OTEL_TRACES_SAMPLER_ARG | 否 | Alloy 地址与成功链路采样率 |
| FINEVISION_GRAFANA_USER / PASSWORD | 高级运维时 | 内部 Grafana 管理账号；普通用户不需要 |

完整示例见 [.env.example](.env.example)。

## 数据集格式

支持带显式划分的 ImageFolder：

~~~
dataset/
├── train/
│   ├── class-a/*.jpg
│   └── class-b/*.jpg
├── val/
│   ├── class-a/*.jpg
│   └── class-b/*.jpg
└── test/
    ├── class-a/*.jpg
    └── class-b/*.jpg
~~~

也支持只有类别目录的 ImageFolder，由平台按配置生成确定性划分。仓库内的
[toy-shapes 示例数据集](data/examples/toy-shapes-imagefolder)
可用于不依赖真实数据集的导入与链路测试。

## 预训练权重

仓库只用 Git LFS 保存允许再分发且身份固定的预训练权重：

- DINOv3 ViT-S
- ImageNet ViT-S
- ImageNet ResNet-50

权重来源、revision、许可证、SHA-256 和大小统一登记在
[权重清单](weights/manifest.json)。训练产物、数据集、特征缓存和测评原图不进入 Git，统一写入 MinIO。

## AI 标注测评

冻结测评矩阵包含 ImageNet-100、Stanford Cars、CUB-200-2011 和 CUB 鲁棒性集，每个集合三轮、每轮 200 张，比较 Direct VLM、受限工作流、图像检索和图文 RRF 四种方法，共完成 48/48 个实验单元、9,600 个样本-方法案例。

三轮全分母 Top-10 汇总：

| 数据集 | Direct VLM | 完整方案 |
|---|---:|---:|
| ImageNet-100 | 99.33% | 99.00% |
| Stanford Cars | 98.33% | 99.00% |
| CUB-200-2011 | 92.50% | 95.83% |
| CUB Robustness | 86.50% | 92.33% |

详细协议、消融、工具调用、稳定性和解释边界见本地展示文档第 4 章。结果不构成对任意数据分布的准确率保证。

## 开发与验证

Python：

~~~bash
uv sync --frozen --group dev --extra dinov3
uv run pytest -q
~~~

Go：

~~~bash
cd go
go test ./...
~~~

前端：

~~~bash
npm --prefix frontend ci
npm --prefix frontend run build
scripts/smoke-demo.sh --contracts-only
~~~

展示文档：

~~~bash
python3 showcase/build.py
python3 showcase/check.py
python3 -m http.server 5180 --directory showcase/site
~~~

GitHub Actions 会分别执行 Go 测试、Python 测试、前端构建/契约测试和展示文档完整性检查。

## 目录结构

~~~
.
├── frontend/       React/Vite 工作台
├── go/             Go 控制面、LLM Gateway、Outbox 与领域逻辑
├── backend/        Python 计算、训练、模型导出与推理运行时
├── annotation/     AI 标注编排与本地工具运行时
├── data/examples/  可公开的最小 ImageFolder 示例
├── docs/           专题设计与操作说明
├── reports/        选择性推理的离线评估产物
├── showcase/       单页项目展示文档
├── scripts/        启动、契约检查与维护脚本
└── weights/        Git LFS 预训练权重及身份清单
~~~

## 文档导航

- [AI 标注平台](docs/annotation-platform.md)
- [标注发布与推理回流](docs/annotation-dataset-publication.md)
- [数据集后台导入](docs/dataset-import.md)
- [数据集样本预览](docs/dataset-preview-api.md)
- [多后端推理部署](docs/inference-deployments.md)
- [硬件监控](docs/hardware-monitoring.md)
- [日志、指标、追踪与业务审计](docs/observability.md)
- [展示文档构建说明](showcase/README.md)

## 安全与发布边界

- .env、数据集、上传文件、运行日志、Qdrant 本地库和训练产物均被 Git 忽略。
- 公开仓库不包含测评原图、私有真值、DeepSeek/OpenAI Key 或本地数据库。
- 默认 Compose 密码仅供本机开发；生产环境必须替换并接入 OIDC/RBAC、集中密钥管理、审计、限流与备份。
- TensorRT engine 与 Ascend OM 绑定硬件和运行时 profile，不能跨设备直接复用。
