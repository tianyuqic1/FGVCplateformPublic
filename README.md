# FineVision

[![CI](https://github.com/tianyuqic1/FGVCplateformPublic/actions/workflows/ci.yml/badge.svg)](https://github.com/tianyuqic1/FGVCplateformPublic/actions/workflows/ci.yml)
[![Code License: MIT](https://img.shields.io/badge/Code%20License-MIT-blue.svg)](LICENSE)

FineVision 是一个面向细粒度图像分类的全流程工程平台，覆盖数据集版本管理、可配置训练、模型发布、多后端推理、在线弃权、人工复核与 AI 辅助标注。系统以 **Go 控制面 + Python 计算面** 为核心，通过 PostgreSQL、RabbitMQ 和 MinIO 把业务状态、长任务和二进制产物解耦。

> 当前仓库以本地开发和单机 Docker Compose 为主要运行形态。工作台现有本地账号、三角色授权与会话保护；公网部署前仍需配置 HTTPS、密钥管理、对象权限、备份与网络边界，不能直接使用示例口令。

## 核心能力

- **实验助手 Agent**：对话查询与诊断、15 个受控工具、规范方案确认执行、持久任务与 SSE、训练跟踪；独立 Docker 和数据库。[运行与验收](agent/docs/runtime.md)。

- **Worker 状态**：独立实例心跳、分页筛选、共享负载与任务关联；管理员查看完整诊断，业务人员只读查看本人训练任务，标注员仅查看 AI 服务提示。[部署与状态含义](docs/worker-status.md)。
- **数据资产**：ImageFolder 导入、后台校验、样本预览、不可变版本、数据血缘与训练集扩充。
- **训练策略**：DINOv3 ViT-S 冻结骨干或 LoRA R8/R16；ImageNet ViT-S、ResNet-50 全量训练；iNat 蒸馏 MobileNetV3-Large 默认冻结骨干，也可全量微调；独立学习率、输入尺寸和常用数据增强。
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
      ├── Agent Service（对话 API + Runner + Watch）── Agent PostgreSQL
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

需要 Bash、Docker Engine 与 Docker Compose 2.30+；启用 CUDA 另外需要 NVIDIA 驱动与 Container Toolkit。部署不要求宿主机安装 Node、Go 或 Python。

~~~bash
GIT_LFS_SKIP_SMUDGE=1 git clone git@github.com:tianyuqic1/FGVCplateformPublic.git
cd FGVCplateformPublic
./deploy.sh --dry-run                    # 预览，不修改配置或服务
./deploy.sh                             # 平台 + Agent，不启动本机训练/推理
~~~

首次自动创建 `.env` 并补齐 Agent 空白密钥，已有值保留。默认工作台 <http://localhost:5173>、API <http://localhost:8001>；Agent 在侧边栏「实验助手」，真实模型回答需先配置 `.env` 中的供应商信息。

按需增加功能：

~~~bash
./deploy.sh --with-worker                         # 本机 CPU 训练与推理
./deploy.sh --with-models                         # 下载校验预训练权重，并启用本机计算
./deploy.sh --with-worker --with-models --device cuda
./deploy.sh --with-models --models dinov3-vits,inat-mobilenetv3
./deploy.sh --with-worker --with-observability
~~~

所选权重准备失败不会报告部署成功。已有成功部署会保存功能组合，后续省略开关不会自动关闭 Worker 或切回 CPU。

~~~bash
./deploy.sh status
./deploy.sh logs agent-service --tail 100
./deploy.sh doctor
./deploy.sh restart
./deploy.sh stop                                  # 保留数据库和模型缓存
~~~

首次管理员初始化命令在部署成功时输出，密码安全交互输入。账号角色与注册审核见 [用户认证](docs/auth.md)。旧 `scripts/demo-up.sh` 保留兼容并转发为本机 Worker 部署。

完整 [部署指南](docs/deployment/README.md) 包含参数、配置、模型准备、远程 Worker、升级与排障。AI 标注、TensorRT/Ascend 仍使用各自专题入口，尚未并入统一脚本。当前前端是 Vite 开发容器，统一入口不代表生产 HTTPS 或多节点编排已经完成。

## 实验助手 Agent

对话查询数据与训练、比较模型、生成规范实验方案，确认后提交或取消训练、执行单样本推理；工作区和页面抽屉共用持久历史，支持 SSE、停止分析、授权恢复与训练跟踪。

已完成训练详情提供「分析测试集错分」，可分析独立测试指标、易混类别和有限错分样本，区分事实与原因假设。工具不读取原图，缺乏独立测试证据的旧训练明确不可用，不自动重训。详见 [错分分析](agent/docs/test-error-analysis.md)。

Agent 拥有独立 Docker 服务、数据库、账号与 volume。默认 4 个 Runner，数据库限制全局 8 个运行任务、每用户 2 个、同会话串行；每轮最多 4 个只读工具并发，写动作逐项确认。[运行与组件说明](agent/README.md)。

## 配置

配置模板为 [.env.example](.env.example)，本地实际值只写入被忽略的 `.env` 或自定义配置文件。`--env-file`、功能开关、端口与配置优先级统一见 [部署配置](docs/deployment/configuration.md)。供应商凭据仅提供给 Gateway，不写入前端、日志或提交记录。

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

部署可用 `./deploy.sh models list` 查看，`./deploy.sh models pull` 按需准备；Git LFS 仍可手动使用。权重来源、revision、许可证、SHA-256 和大小统一登记在
[权重清单](weights/manifest.json)。训练产物、数据集、特征缓存和测评原图不进入 Git，统一写入 MinIO。

`pretrained/` 是独立的对外发布包，内含上述权重的发布副本、DINOv3 ViT-B/L 家族，以及蒸馏产物的 model card 与许可证。该目录的权重二进制同样被 Git 忽略，只把文本纳入版本管理；平台运行时不读取它。详见
[对外发布包说明](pretrained/README.md)。

## 蒸馏权重

平台产出的蒸馏权重不进入 Git，作为独立模型对外发布。当前已发布一份：

**iNat2021-mini · DINOv3 ViT-B/16 → MobileNetV3-Large** —— 冻结的 DINOv3 ViT-B/16 作教师（配 10,000 类线性探针头），蒸馏进 MobileNetV3-Large；训练数据为 iNaturalist 2021 mini，50 万图 / 10,000 类。

| 指标 | 结果 |
|---|---:|
| iNat 官方 val top-1 · 仅标签 | 51.78% |
| iNat 官方 val top-1 · 蒸馏 | **58.40%** |
| 教师线性探针（参考上界） | 71.38% |
| 骨干参数量 | 2.97M |
| 冻结特征线性探针 · CUB-200 / NABirds / Flowers-102 | 68.73% / 53.82% / 89.46% |

冻结探针的 5-shot 数字指分类头每类使用 5 张；ridge 正则系数另用训练池中的少量标注图选择，详见模型卡的测评协议说明。

发布地址：[ModelScope](https://modelscope.cn/models/Tianyuqi/inat2021-mini-mobilenetv3-large)。模型卡、蒸馏配置与完整对照表见
[pretrained/inat2021-mini-mobilenetv3-large](pretrained/inat2021-mini-mobilenetv3-large/README.md)。

平台选择该骨干时，从 ModelScope 下载发布包中的 **backbone** 文件，以固定 SHA-256 和大小校验后缓存在计算节点；离线部署可把文件挂载到计算容器，并在容器内设置 `FINEVISION_INAT_MOBILENETV3_WEIGHT` 指向它。该蒸馏产物不纳入 Git LFS，运行时不读取 `pretrained/` 发布目录。训练新数据集时创建新的分类头，默认冻结 960 维骨干；需要时可选择全量微调。

域外迁移（Stanford Cars）降至 12.13%，低于原始 ImageNet 初始化权重的 23.02% —— 生物域特化的预期代价，非生物域任务不宜直接采用。

该权重是 DINOv3 的衍生作品，适用 DINOv3 License，**不是** MIT 或 Apache-2.0，再分发须随附协议副本；学生权重以 torchvision MobileNetV3-Large 初始化为起点，另附 BSD-3-Clause 署名。详见 [第三方资源与许可证边界](THIRD_PARTY_NOTICES.md)。

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
npm --prefix frontend run check
(cd frontend && npx playwright install chromium)
npm --prefix frontend run test:e2e
scripts/smoke-demo.sh --contracts-only
~~~

权重完整性（对照 `weights/manifest.json` 校验存在性、Git LFS 指针、大小与 SHA-256）：

~~~bash
scripts/check-weights.sh --strict
~~~

展示文档：

~~~bash
python3 showcase/build.py
python3 showcase/check.py
python3 -m http.server 5180 --directory showcase/site
~~~

部署入口回归（隔离目录与 Docker 替身，不修改业务服务）：

~~~bash
python3 -m unittest discover -s deploy/tests -v
~~~

真实部署验收使用独立项目、随机端口和临时数据卷，结束后只清理该测试项目；应用镜像已构建时运行：

~~~bash
python3 deploy/tests/smoke_live.py --no-build --models all
~~~

去掉 `--no-build` 会执行镜像构建；测试会准备所选模型，不提交训练或调用付费模型。验收范围见 [部署验证记录](docs/deployment/validation.md)。

GitHub Actions 会分别执行 Go 测试、Python 测试、前端构建/契约测试和展示文档完整性检查；其中 Python 测试包含权重清单的存在性、大小与 SHA-256 校验。

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
├── deploy.sh       统一部署命令入口
├── deploy/         配置/模型辅助程序与部署测试
├── scripts/        兼容入口、契约检查与维护脚本
├── pretrained/     对外发布的模型包（权重、许可证与 model card）
└── weights/        Git LFS 预训练权重及身份清单
~~~

## 文档导航

- [文档总目录](docs/README.md)
- [统一部署、模型准备与升级](docs/deployment/README.md)
- [Agent 服务与工具](agent/README.md)
- [AI 标注平台](docs/annotation-platform.md)
- [标注发布与推理回流](docs/annotation-dataset-publication.md)
- [数据集后台导入](docs/dataset-import.md)
- [数据集样本预览](docs/dataset-preview-api.md)
- [多后端推理部署](docs/inference-deployments.md)
- [硬件监控](docs/hardware-monitoring.md)
- [Worker 状态、权限与独立部署](docs/worker-status.md)
- [用户认证与权限管理](docs/auth.md)
- [日志、指标、追踪与业务审计](docs/observability.md)
- [展示文档构建说明](showcase/README.md)
- [对外发布包与蒸馏权重](pretrained/README.md)

## 安全与发布边界

- .env、数据集、上传文件、运行日志、Qdrant 本地库和训练产物均被 Git 忽略。`pretrained/` 下的权重二进制与 ModelScope CLI 上传缓存同样被忽略，该目录只把许可证、model card 等文本纳入版本管理。
- 公开仓库不包含测评原图、私有真值、DeepSeek/OpenAI Key 或本地数据库。
- 默认 Compose 密码及服务令牌仅供本机开发；生产环境必须替换并完善 TLS、集中密钥管理、审计、限流与备份。当前已有三角色 RBAC，OIDC/SSO 尚未实现，可按企业统一登录需求接入。
- TensorRT engine 与 Ascend OM 绑定硬件和运行时 profile，不能跨设备直接复用。

## 许可证

本项目由 Kaiwen Chen 原创的平台代码及配套原创文档采用 [MIT License](LICENSE)，版权署名为 `Copyright (c) 2026 Kaiwen Chen`。允许使用、修改、分发及商业使用；分发时须保留版权与许可声明，软件按原样提供，不附带担保。

**MIT 不覆盖第三方资源。** 模型权重、外部数据集、截图中的第三方图片、依赖库和容器镜像继续遵循各自的许可证。DINOv3 权重保留 DINOv3 License；仓库登记的两份 timm ImageNet 权重保留 Apache-2.0。训练、LoRA 合并或格式转换不会自动移除上游许可要求 —— 由 DINOv3 蒸馏得到的权重同样适用 DINOv3 License，不因学生架构换成 MobileNetV3 而改变。

详见 [第三方资源与许可证边界](THIRD_PARTY_NOTICES.md) 和 [权重身份清单](weights/manifest.json)。
