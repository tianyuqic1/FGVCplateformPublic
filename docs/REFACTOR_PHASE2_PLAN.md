# FineVision 二期重构实施方案

> 状态：已批准，作为 Phase 2 实施与验收基线
>
> 批准日期：2026-09-08
>
> 范围：Phase 2
>
> 前置条件：Phase 1 当前 route 批次和 Compute Runtime 边界保持不变
>
> 批准的视觉方向：A「科研实验工作台」

本文档定义四项二期实施范围：前端视觉与结构重构、训练曲线、Model Version 管理与比较、ImageNet 系列 ViT-S/ResNet Pretrained Weight。它是代码、接口、数据库、测试和验收的共同基线；未写入本文档的 MLflow 服务、超参数搜索、分布式训练或在线资源监控不属于二期。

## 1. 结论先行

二期采用以下方案：

1. 保留 React + Vite + React Router，重构目录、Design Token、页面信息架构和视觉语言，不更换前端框架。
2. 引入 Apache ECharts，只负责可视化；Training Metric 仍由 FineVision Go Control Plane API 提供。
3. 借鉴 MLflow 的 Run/Metric/Model Registry 交互，但不部署 MLflow Tracking Server，避免出现第二套 Training Run、Artifact 和 Model Registry 事实源。
4. 新增 append-only `training_metric_points`，保存 epoch/step 指标历史；当前 `training_runs.metrics` 继续保存摘要和最后进度。
5. 训练进行时使用带 cursor 的 2 秒增量轮询；终态后停止轮询。二期暂不增加 WebSocket/SSE 基础设施。
6. Model Version 新增独立列表、详情与比较页面；比较必须绑定同一个 Dataset Version 和评估协议，不能只把任意 accuracy 放在一起。
7. 二期批准的 backbone 候选限定为三项：
   - `ViT-S/16 · DINOv3 (LVD-1689M)`，一期已有。
   - `ViT-S/16 · ImageNet-21K → ImageNet-1K`，固定上游 model ID 为 timm `vit_small_patch16_224.augreg_in21k_ft_in1k`。
   - `ResNet-50 · ImageNet-1K`，固定上游 model ID 为 timm `resnet50.a1_in1k`。
8. 所有新增 Pretrained Weight 继续使用 Git LFS 发布、manifest 固定身份、MinIO/S3 运行时 promotion、SHA-256 与大小校验。
9. Trained Model Artifact 采用两层区分：PostgreSQL/Model Registry 按 Dataset Version 管理逻辑归属，MinIO 按 SHA-256 保存不可变物理对象；不能把 MinIO 路径当作模型适用范围。

### 1.1 当前实施状态

| 工作流 | 当前状态 | Phase 2 完成定义 |
| --- | --- | --- |
| A 版视觉方向 | 已完成 | 已迁入生产 Shell、token 与 feature 页面，并完成宽屏/窄屏检查 |
| 前端结构拆分 | 已完成（二期核心 route） | Training 与 Model Version 已迁出单体 `pages.jsx`；其余旧 route 后续按需迁移 |
| Training Metric Point | 已完成 | 数据库、gRPC、Go API、轮询 adapter 和 ECharts 全链路通过 |
| Model Version 管理/比较 | 已完成 | list/detail/compare/action、可比性和 alias 全部通过 |
| ImageNet ViT-S/ResNet-50 | 已完成 | 许可证、LFS、manifest、MinIO promotion、校验和 extractor gate 通过 |
| Trained Model Artifact 两层区分 | 已完成 | Dataset Version scope、推理校验、引用安全 GC 和合同测试通过 |

实现与验收证据记录在 [`REFACTOR_PHASE2_VERIFICATION.md`](REFACTOR_PHASE2_VERIFICATION.md)。

## 2. 为什么不直接接入完整 MLflow

MLflow 值得借鉴的部分：

- Training Run 列表、参数与指标的集中展示。
- 使用 `step + timestamp` 保存指标历史。
- 多 Run 搜索、筛选、排序和可视化比较。
- Model Version 的 tags、aliases 与详情页。
- Dataset-aware comparison，避免跨数据集误比。

FineVision 当前已经拥有 Training Run、Model Version、ArtifactStore、PostgreSQL 和 MinIO。如果再部署 MLflow Tracking Server，会出现两套 ID、两套状态、两套 Artifact registry 和双写失败问题。因此二期只吸收其产品交互模型：

```text
MLflow 的交互经验
  -> FineVision 自有 OpenAPI
  -> Go Control Plane
  -> PostgreSQL Training Metric / Model Version
  -> MinIO ArtifactStore
```

MLflow 官方资料说明其 Run 会记录参数、指标和 Artifact，并支持指标可视化、Run 比较、Dataset-aware 搜索；Model Registry 则使用 aliases 和 tags 管理版本。FineVision 可以在现有领域模型上实现这些能力，无需引入第二个 tracking backend。

参考：

- [MLflow Tracking](https://mlflow.org/docs/latest/ml/tracking/)
- [MLflow Tracking API：step/timestamp metric](https://mlflow.org/docs/latest/ml/tracking/tracking-api)
- [MLflow Model Registry workflow](https://mlflow.org/docs/latest/ml/model-registry/workflow/)

## 3. 当前实现基线

### 3.1 前端

当前前端技术栈可继续使用，但结构需要治理：

- `frontend/src/pages/pages.jsx` 约 3941 行，多个业务页面和大量转换函数集中在一个文件。
- `frontend/src/styles.css` 约 1907 行，缺少明确的 token、组件层与页面层边界。
- 已有训练列表、Training Run 详情、指标卡和 Model 页面原型，但曲线与比较仍是假定式静态布局。
- 现有 client/hook seam 可以保留，并按业务域迁移。

### 3.2 训练指标

Python `torch_linear_adam` 已在每个 epoch 产生：

- `epoch`
- `train_loss`
- `eval_accuracy`

Worker 也已经通过 `ReportProgress` gRPC 上报进度，但 Go repository 目前只把最后一份 progress 合并进 `training_runs.metrics.progress`，历史点会被覆盖。因此二期第一步不是画图，而是建立可靠的 Metric Point 数据合同。

`ridge_linear` 没有 epoch 优化循环，不能伪造 loss/accuracy 曲线。其详情页只显示阶段时间线和最终评估指标。

### 3.3 Model Version

当前 `model_versions` 已包含：

- Dataset / Dataset Version lineage
- Training Run lineage
- candidate/staging/production/archived/failed 状态
- Model、Calibration、Threshold Artifact
- JSONB summary metrics

缺少的是稳定的 Go read/action API、可比性校验、alias、展示所需 backbone metadata，以及独立的版本比较页面。

## 4. 前端风格与结构重构

### 4.1 已批准视觉方向：A「科研实验工作台」

Phase 2 全站采用已确认的 A 版作为统一视觉基线。它不是对旧页面做局部换色，而是同时重构应用 Shell、信息层级、密度、状态表达、图表与表格。

视觉原则：

- 浅灰蓝工作区背景、白色内容 panel、深海军蓝正文，形成安静、可信的科研工具气质。
- 靛蓝是主操作色；青绿色只表示成功、已验证或有效候选；琥珀色表示运行中/待处理；红色只表示失败、完整性风险和不可逆影响。
- 左侧固定主导航，顶部保留当前业务上下文、全局搜索和操作者入口；详情页内容使用“标题与操作—核心指标—主要工作区—辅助信息”的稳定顺序。
- 核心指标使用 tabular number；Run ID、Model Version、SHA-256、Artifact URI 使用等宽字体。
- 采用轻边框和克制阴影，不使用大面积渐变、玻璃拟态、霓虹光效或夸张圆角。
- 桌面端优先服务高信息密度实验工作；窄屏按优先级堆叠内容，表格允许横向滚动，不压缩到不可读。

批准阶段曾使用以下临时原型：

```text
/prototype/phase2-style?variant=A
frontend/src/prototypes/phase2/Phase2VariantA.jsx
frontend/src/prototypes/phase2/phase2-variant-a.css
```

该原型已在 A 版迁入 `design-system` 与对应 `features` 后删除。当前生产页面不依赖
`frontend/src/prototypes`，原 `/prototype/phase2-style` route 也已移除；B「训练指挥舱」和 C
「编辑式数据报告」没有进入生产代码。

#### 4.1.1 基础 token 语义

实现时建立语义 token，不在页面 JSX 中散落品牌色：

| Token 语义 | 用途 |
| --- | --- |
| `surface-canvas` | 应用工作区浅灰蓝背景 |
| `surface-panel` | 卡片、表格、详情内容背景 |
| `border-subtle` | panel、分区、表头与输入边界 |
| `text-primary/secondary/muted` | 标题、正文、辅助说明三级信息 |
| `action-primary` | 创建、提交、打开主流程等唯一主操作 |
| `status-success/running/warning/danger/neutral` | 统一状态语义，不由各页面自定义颜色 |
| `data-series-*` | ECharts 序列颜色，与业务状态色分离 |

间距、字号、圆角和阴影同样必须 token 化。默认内容 panel 使用小到中等圆角；同一视口最多保留一个实心主按钮。

#### 4.1.2 核心页面构图

- Training Runs：左侧/上方为筛选与队列摘要，主体为运行列表；运行中、成功、失败和等待资源必须同时通过文本、图标和颜色表达。
- Training Run Detail：顶部四个核心指标，中部为主曲线工作区，侧栏展示配置、资源与 Artifact 完整性；历史运行列表不得挤压主图到不可读。
- Model Versions：使用数据表为主体，alias、生命周期状态、backbone 与可比范围是第一层信息。
- Model Version Detail：保持 lineage、metrics、configuration、artifacts、audit 的清晰章节，不把所有数据做成等权卡片。
- Model Comparison：沿用 A 的颜色与 Shell，但允许使用更宽画布；先显示可比性结论，再显示指标图表和配置差异。
- Dataset、Inference、Review 等非重点页面只迁移 Shell、token 与基础组件，不在二期追加新产品能力。

### 4.2 前端目录目标

```text
frontend/src/
├── app/
│   ├── router.jsx
│   └── providers.jsx
├── design-system/
│   ├── tokens.css
│   ├── components/
│   └── charts/
├── features/
│   ├── datasets/
│   ├── training/
│   ├── model-versions/
│   ├── inference/
│   ├── review/
│   └── settings/
├── api/
└── main.jsx
```

规则：

- 每个 route page 归属一个业务 feature。
- API DTO 到 view model 的转换放在对应 feature，不放进通用 UI 组件。
- `design-system` 不知道 Dataset、Training Run 或 Model Version。
- ECharts option builder 放在 `design-system/charts` 或具体 feature 的 chart module，不散落在 JSX 中。
- 先无视觉变化拆文件，再替换 token 与组件；每个提交保持页面可运行。

### 4.3 页面信息架构

二期只重点重构：

- Training Runs：创建配置、队列、状态筛选、推荐基线。
- Training Run Detail：运行状态、动态曲线、阶段、配置、Artifact、失败诊断。
- Model Versions：独立列表与筛选。
- Model Version Detail：lineage、评估、Artifact、状态和 alias。
- Model Comparison：多模型指标/配置对比。

其他页面只迁入新 Shell/Token，不增加新产品功能。

## 5. 训练曲线方案

### 5.1 数据模型

新增 append-only `training_metric_points`：

| 字段 | 类型 | 语义 |
| --- | --- | --- |
| `id` | bigint | 单调 cursor |
| `training_run_id` | uuid | 所属 Training Run |
| `attempt_id` | uuid | 指标所属 Job Attempt |
| `execution_epoch` | bigint | fencing epoch |
| `metric_name` | text | 如 `train_loss`、`eval_accuracy` |
| `step` | bigint | epoch/iteration |
| `value` | double precision | 指标值 |
| `recorded_at` | timestamptz | Control Plane 接收时间 |
| `context` | jsonb | phase、split 等有限扩展信息 |

约束：

- 唯一键：`training_run_id + attempt_id + metric_name + step`。
- 重复 gRPC progress 使用 upsert 或 ignore，保证 at-least-once 上报不会生成重复点。
- 过期 `execution_epoch` 继续由 Training Lifecycle fencing 拒绝。
- retry 后的新 attempt 不与旧 attempt 拼接；页面默认展示当前/成功 attempt，并允许查看历史 attempt。
- `training_runs.metrics` 只保存最终摘要与最新 progress，不代替历史点表。

### 5.2 gRPC 与 HTTP 合同

兼容扩展 Training Lifecycle Protobuf：

```text
MetricPoint
  name
  step
  value
  recorded_at
  context

ProgressRequest
  ...现有字段
  repeated MetricPoint metric_points
```

公开 API：

```text
GET /api/training-runs/{run_id}/metrics
    ?attempt_id=
    &metric_name=
    &after_id=
    &limit=
```

响应包含：

- `metric_points`
- `next_cursor`
- `attempts`
- `run_status`
- `poll_after_ms`

前端在 `queued/running/paused` 时每 2 秒使用 cursor 增量拉取；`succeeded/failed/cancelled` 后停止。页面切到后台时降低频率，卸载页面时取消请求。

二期不把每个 epoch 指标写入 Outbox/RabbitMQ，也不使用 Training Dispatch Queue 广播曲线；MQ 继续只负责 Job 派发。

### 5.3 ECharts 展示

ECharts 官方支持异步加载后用 `setOption` 做动态更新，适合 cursor 轮询返回的增量数据。

Training Run Detail 建议提供：

1. `Train Loss / Epoch` 折线图。
2. `Validation Accuracy / Epoch` 折线图。
3. 当前 epoch、best accuracy、latest loss、耗时四个指标卡。
4. attempt selector；重试时旧 attempt 使用弱化颜色，不把两次训练连成一条线。
5. tooltip、legend、data zoom、空状态和“指标尚未上报”说明。

不建议默认把 loss 和 accuracy 放在同一双 Y 轴图中，因为量纲不同且容易制造相关性错觉；可在“叠加查看”开关中提供辅助视图。

参考：[Apache ECharts Dynamic Data](https://echarts.apache.org/handbook/en/how-to/data/dynamic-data/)

## 6. Model Version 管理与比较

### 6.1 管理语义

沿用当前状态：

```text
candidate -> staging -> production -> archived
       \---------------------------> archived
```

- 状态转换由 Go Control Plane 校验并写审计事件。
- `production` 不是“accuracy 最大”的自动结果，必须由操作者明确提升。
- 建议增加 Dataset 作用域内的 `champion` 和 `challenger` alias；alias 是可变指针，Model Version 与 Artifact 本身仍不可变。
- 同一 Dataset 只能有一个 `champion`；更新 alias 使用事务或 CAS。
- 删除默认采用 archive，不能删除仍被 inference、review 或 policy 引用的 Model Version。

### 6.2 展示 metadata

Model Version API 需要稳定返回：

- `model_version_id`、名称、状态、aliases、备注。
- Dataset Version 与 manifest fingerprint。
- Training Run、Job Attempt 与代码/容器版本。
- backbone architecture、pretraining method、pretraining dataset、Pretrained Weight SHA-256。
- input size、feature dimension、parameter count、pooling、head type。
- accuracy、macro F1、coverage、selective risk、calibration/threshold 摘要。
- Feature/Model/Report Artifact 的 SHA-256、大小和验证状态。
- 推理延迟、特征提取耗时等未采集指标必须显示“未采集”，不能显示为 0。

### 6.3 独立页面

`/models`：

- 按 Dataset、Dataset Version、architecture、pretraining、状态筛选。
- 表格列支持排序和列显隐。
- 可勾选 2–5 个 Model Version 进入比较。
- 明确显示 `champion/challenger`、candidate/production 等状态。

`/models/{model_version_id}`：

- Overview、Metrics、Configuration、Artifacts、Lineage、Audit 六个区域。
- 提升/归档操作显示影响范围和确认信息。

`/models/compare`：

- 顶部显示比较作用域和可比性告警。
- 指标矩阵：accuracy、macro F1、coverage、selective risk、参数量、Artifact 大小、耗时。
- grouped bar：accuracy、macro F1、coverage 等相同量纲指标。
- scatter：accuracy vs inference latency；没有 latency 数据时不渲染该图。
- 配置差异表：backbone、pretraining、input、pooling、head、Dataset Version、threshold。
- 不使用雷达图作为默认比较，因为不同量纲归一化会隐藏真实差异。

### 6.4 可比性规则

服务端必须返回 `comparable` 与 warnings：

- Dataset Version 不同：默认拒绝“谁更好”的排名，只允许并列查看。
- split/fingerprint 或 evaluation protocol 不同：显示不可直接比较。
- head type、input size、pooling 不同：允许比较，但必须高亮差异。
- 缺失指标：显示 N/A，不使用 0 补齐。
- accuracy 相同情况下，不自动忽略 macro F1、coverage、selective risk、延迟和模型大小。

建议 API：

```text
GET  /api/model-versions
GET  /api/model-versions/{model_version_id}
POST /api/model-version-comparisons
POST /api/model-versions/{model_version_id}/promote
POST /api/model-versions/{model_version_id}/archive
PUT  /api/model-aliases/{alias}
```

`POST /api/model-version-comparisons` 只计算并返回视图，不创建持久资源；body 接收 2–5 个 ID，由服务端统一做可比性判断。

## 7. ImageNet Pretrained Weight

### 7.1 二期批准清单

| 展示名称 | 稳定 backbone key | 固定上游 model ID | 预训练语义 | 特征维度 |
| --- | --- | --- | --- | --- |
| ViT-S/16 · DINOv3 | `dinov3_vits16_lvd1689m` | `vit_small_patch16_dinov3.lvd1689m` | 自监督 LVD-1689M | 384 |
| ViT-S/16 · ImageNet | `imagenet_vits16_augreg_in21k_ft_in1k` | `vit_small_patch16_224.augreg_in21k_ft_in1k` | ImageNet-21K 预训练、ImageNet-1K fine-tune | 384 |
| ResNet-50 · ImageNet | `imagenet_resnet50_a1_in1k` | `resnet50.a1_in1k` | ImageNet-1K supervised recipe | 2048 |

上游 model card 显示该 ImageNet ViT-S 约 22.1M 参数、224×224 输入；ResNet-50 候选约 25.6M 参数。最终纳入前仍需固定上游 revision、选择唯一 safetensors 文件、记录真实 SHA-256/大小并完成许可证审查。

参考：

- [timm ViT-S ImageNet model card](https://huggingface.co/timm/vit_small_patch16_224.augreg_in21k_ft_in1k)
- [timm ResNet-50 ImageNet model card](https://huggingface.co/timm/resnet50.a1_in1k)

### 7.2 训练语义

三种 backbone 都采用：

- 加载批准的 frozen Pretrained Weight。
- 移除上游 ImageNet classifier。
- 使用各模型自己的、已版本化的 resize/normalization 配置。
- 抽取固定维度 Feature。
- 在相同 FineVision Dataset Version 上训练新的分类 head。

不能直接使用原 ImageNet 1000 类 classifier 对 FineVision 类别推理。不同 backbone 的预处理、pooling 和 feature semantic version 必须进入 Feature cache fingerprint。

### 7.3 通用 extractor

Python Compute Plane 建立配置驱动的 `TimmFeatureExtractor`：

```text
BackboneSpec
  backbone_key
  architecture
  model_name
  weight_descriptor
  input_size
  normalization
  feature_dim
  pooling
  extractor_semantic_version
```

- ViT 使用 CLS/pre-logits 384 维特征。
- ResNet-50 使用 global average pooled 2048 维特征。
- Runtime 禁止联网下载；只加载从 ArtifactStore 校验后 materialize 的本地权重。
- DINOv3 与 ImageNet extractor 共享 Artifact/批处理/设备管理，不强行共享不同的 feature semantics。

### 7.4 Catalog 与选择界面

现有 `/api/model-weights` 从硬编码的 DINOv3 ViT-S 扩展为 manifest-driven catalog。每个选项显示：

- 展示名称、architecture、pretraining method/dataset。
- 参数量、输入尺寸、feature dimension。
- 权重大小、SHA-256 短摘要、availability/verified 状态。
- CPU/GPU 支持与估算显存等级。
- 简短使用建议，但不宣称某模型一定更准确。

训练表单显示为卡片：

```text
ViT-S/16
DINOv3 · LVD-1689M self-supervised
384-dim · verified

ViT-S/16
ImageNet-21K -> 1K supervised
384-dim · verified

ResNet-50
ImageNet-1K supervised
2048-dim · verified
```

Public request 使用稳定 `backbone_key`；旧 `extractor=dinov3_vits` 在迁移期由 Go 映射到 DINOv3 key，不能让前端提交任意 timm model name 或下载 URL。

### 7.5 Trained Model Artifact 两层区分

二期采用“逻辑归属层 + 物理对象层”，两层职责不能混用。

#### 7.5.1 逻辑归属层：PostgreSQL / Model Registry

模型必须按 Dataset Version 区分，而不只按 Dataset 区分。逻辑关系为：

```text
Dataset
  -> Dataset Version
     -> Training Run
        -> Model Version
           -> Artifact Record
```

- `model_versions` 保存 `dataset_id`、`dataset_version_id`、`training_run_id` 和生命周期状态。
- 每个 Artifact Record 保存 `dataset_version_id`、`training_run_id`、`attempt_id`、canonical URI、SHA-256、`size_bytes`、类型和验证时间。
- 同一 Dataset 的两个 Dataset Version 即使名称相同，也属于不同 Exact Scope，不能默认共享 Model Version、阈值或推理策略。
- Dataset、Dataset Version、Model Version 的 ID 使用不可变 ID；名称和 alias 只用于展示与解析，不能参与物理地址拼接或完整性判断。
- Model Version 是前端、API 和推理请求选择模型的入口。客户端不能直接提交 MinIO URI、本地路径或任意 SHA-256 来绕过 Model Registry。

#### 7.5.2 物理对象层：MinIO / S3-compatible ArtifactStore

Trained Model Artifact 继续使用内容寻址，不按数据集复制相同字节：

```text
s3://finevision-artifacts/compute/model/{sha256[0:2]}/{sha256}
s3://finevision-artifacts/compute/model_bundle/{sha256[0:2]}/{sha256}
s3://finevision-artifacts/compute/calibration/{sha256[0:2]}/{sha256}
s3://finevision-artifacts/compute/threshold_strategy/{sha256[0:2]}/{sha256}
s3://finevision-artifacts/compute/report/{sha256[0:2]}/{sha256}
```

这层只回答“这些字节是什么、是否完整”，不回答“它属于哪个数据集、是否可用于本次推理”。同一 SHA-256 可以被不同 Artifact Record 安全引用，但每个逻辑引用仍保留自己的 Dataset Version、Training Run 和 Model Version lineage。

Pretrained Weight 与训练产物分开管理：

```text
s3://finevision-artifacts/pretrained/dinov3/{sha256[0:2]}/{sha256}
s3://finevision-artifacts/pretrained/imagenet/vit-small/{sha256[0:2]}/{sha256}
s3://finevision-artifacts/pretrained/imagenet/resnet-50/{sha256[0:2]}/{sha256}
```

Pretrained Weight 由 Git LFS manifest、上游 revision 和许可证治理；Trained Model Artifact 由 Training Lifecycle、Dataset Version lineage 和 Model Registry 治理，二者不能混用同一 Artifact 类型。

#### 7.5.3 推理加载与完整性检查

推理必须依次执行：

1. 通过 Model Version ID/alias 在 Go Control Plane 解析唯一 Model Version。
2. 校验请求的 Dataset Version 与 Model Version 的 Exact Scope 一致。
3. 从 Artifact Record 获取 canonical URI、SHA-256 和 `size_bytes`，不信任客户端提供的地址或摘要。
4. Inference Runtime 在 cache miss 时下载到临时文件，流式计算 SHA-256 和大小。
5. 两项完全匹配后原子发布到本地 `cache/sha256/{sha256}` 并加载；任一不匹配即 fail closed。
6. 记录 Model Version、Dataset Version、Artifact ID、SHA-256 短摘要和 request ID，日志不得包含永久凭证或原始图片字节。

#### 7.5.4 删除与垃圾回收

- archive/delete Model Version 只改变逻辑状态，不立即删除内容寻址对象。
- 清理器必须确认没有 Artifact Record、Model Version、Inference、Review 或 Policy 引用该 SHA-256 后才能删除物理对象。
- 同一物理对象被多个 Dataset Version 或 Model Version 引用时，删除其中一个逻辑引用不得影响其他引用。
- staging、失败 Attempt 和孤儿对象使用生命周期策略延迟清理，不能与已注册 Artifact 使用同一立即删除规则。

## 8. API 与 schema 变更汇总

### PostgreSQL

- 新增 `training_metric_points`。
- 增加 Model Version 展示/评估上下文字段，或建立一对一 metadata 表，避免继续把所有可查询字段塞进 JSONB。
- 新增 `model_version_aliases`，Dataset 作用域内 alias 唯一。
- 新增 Model Version 状态/alias 审计事件。
- 保持 Artifact Record 到 Dataset Version、Training Run、Job Attempt 和 Model Version 的可查询 lineage；物理 URI 不承担关系建模。
- Alembic 仍是唯一 migration owner。

### Protobuf

- 兼容扩展 `ProgressRequest.metric_points`。
- 不改变既有字段编号和 fencing 语义。

### OpenAPI

- 增加 Metric Point 查询、Model Version list/detail/compare/action 合同。
- 扩展 Model Weight catalog 与 Training Run create 的 `backbone_key`。
- 先更新 source contract 和 contract tests，再生成 Go 类型。

## 9. 小步提交计划

每个提交都应保持主流程可运行：

1. `docs: record phase two decisions and acceptance criteria`
2. `test: freeze training metric and model comparison contracts`
3. `refactor: split frontend routes without visual changes`
4. `refactor: introduce design tokens and shared layout primitives`
5. `feat: add training metric point migration and repository`
6. `feat: persist fenced idempotent metric points from gRPC progress`
7. `feat: expose cursor-based training metric API`
8. `feat: add ECharts training curves and terminal polling behavior`
9. `refactor: generalize pretrained weight manifest and catalog`
10. `feat: add verified ImageNet ViT-S weight and extractor`
11. `feat: add verified ResNet-50 weight and extractor smoke`
12. `feat: add backbone catalog cards to training creation`
13. `feat: expose Model Version list and detail read models`
14. `feat: add Model Version comparison and comparability rules`
15. `feat: add Model Version alias and governed status actions`
16. `feat: build standalone model list, detail and comparison pages`
17. `style: complete the A workbench migration route by route`
18. `test: add phase two integration, accessibility and visual regression gates`
19. `docs: publish phase two verification record and operator notes`

## 10. 测试决策

测试公共行为，不绑定 React/ECharts 内部实现或私有 Go 函数。

### Go

- Metric Point 重复上报幂等。
- 旧 attempt/epoch 上报被 fenced。
- cursor 查询稳定、分页无漏点/重复点。
- Model Version 状态转换与 alias 唯一性。
- 不同 Dataset Version 的比较返回明确 warning。
- 缺失指标保持 N/A，不变成 0。
- OpenAPI request/response contract。

### Python

- `torch_linear_adam` 每个 epoch 上报 loss/accuracy。
- 完整训练集成只测试 ViT-S：DINOv3 ViT-S 与新增 ImageNet ViT-S 使用小型 ImageFolder。
- ResNet-50 只执行权重 SHA/大小、加载和一个小 batch 的 2048 维 Feature smoke，不运行完整训练套件。
- 不允许测试时从公网临时下载权重。
- 不同 preprocess/pooling/weight SHA 生成不同 Feature fingerprint。
- Inference Runtime 必须从 Artifact Descriptor materialize，Dataset Version 不匹配、SHA-256 不匹配或大小不匹配均 fail closed。

### Frontend

- Training chart adapter 正确处理增量点、retry attempt、空数据和终态。
- Model Comparison 正确处理 2–5 个版本、N/A 和不可比较 warning。
- backbone card 提交稳定 `backbone_key`。
- production build、现有 route/client smoke 保持通过。
- 核心页面增加宽屏与窄屏视觉回归，以及基本键盘/颜色对比度检查。

### ArtifactStore

- 同一文件重复上传生成相同内容寻址 URI，不产生可变覆盖。
- 相同物理 SHA-256 可以由不同逻辑 Artifact Record 引用，且各自保留 Dataset Version lineage。
- 删除/归档一个 Model Version 不会删除仍被其他记录引用的物理对象。
- 客户端提交任意 MinIO URI、文件路径或 SHA-256 不能绕过 Model Version 与 Exact Scope 校验。

## 11. 验收标准

- 前端 `pages.jsx` 不再承载全部页面；核心二期页面按 feature 独立维护。
- A「科研实验工作台」被转换为可复用 token、Shell 和组件，不是页面级硬编码换色。
- 生产 route 不依赖 `frontend/src/prototypes`；原型完成视觉回归交接后删除。
- 运行中的 Adam head 每 2 秒内可看到新增 loss/accuracy 点；终态停止轮询。
- retry attempt 曲线不串线，重复上报不产生重复点，过期 Worker 无法写指标。
- Ridge head 不显示虚假 epoch 曲线。
- Model Version 独立页面可筛选、查看详情、选择 2–5 个版本比较。
- 跨 Dataset Version 或评估协议比较有明显警告，N/A 不显示为 0。
- `champion/challenger` alias 与状态变化由 Go 事务控制并留有审计。
- 训练选择器只显示三项批准 backbone，并清楚区分 DINOv3 与 ImageNet 预训练来源。
- 新权重通过 Git LFS、manifest、MinIO promotion、SHA-256/大小和许可证 gate。
- Trained Model Artifact 通过 Dataset Version/Model Version 逻辑归属与 SHA-256 物理内容寻址两层管理；推理同时校验 Exact Scope、SHA-256 和大小。
- MinIO 对象路径不作为数据集隔离或授权依据，引用安全 GC 不会误删共享内容寻址对象。
- 完整训练集成仅运行 ViT-S；ResNet-50 保持轻量加载/Feature smoke。

## 12. 明确不做

- 不部署 MLflow Tracking Server 或 MLflow Model Registry。
- 不引入 TensorBoard、Weights & Biases 或第二套实验事实源。
- 不做 ViT-B、ViT-L、ResNet-101、ConvNeXt、Swin 等其他 backbone。
- 不做 backbone 全量 fine-tune、分布式训练、超参数搜索和自动模型选择。
- 不做实时 GPU/CPU 系统监控大盘。
- 不做自动将最高 accuracy 模型提升为 production。
- 不重构 Dataset、Review、Feedback、Inference 的产品能力，只让它们适配新的 Shell/Token。
- 不改变 Phase 1 的 Go/Python 所有权、RabbitMQ、Outbox、ArtifactStore 和 LLMGateway 决策。

## 13. 已批准决策记录

| 决策 | 结果 | 约束 |
| --- | --- | --- |
| 前端视觉方向 | A「科研实验工作台」 | 全站统一 Shell/token；B、C 不进入生产实现 |
| 前端技术栈 | 保留 React + Vite + React Router | 不在二期迁移到另一套前端框架 |
| 曲线组件 | Apache ECharts | FineVision API 是指标事实源，不部署 MLflow |
| 动态更新 | 2 秒 cursor 增量轮询 | 终态停止；二期不增加 WebSocket/SSE |
| ResNet 规格 | `ResNet-50 · resnet50.a1_in1k` | 二期不加入 ResNet-18/101 |
| ViT 规格 | DINOv3 ViT-S 与 ImageNet ViT-S | 不加入 ViT-B/ViT-L，不做 backbone 全量 fine-tune |
| Model Alias | 加入 `champion/challenger` | Dataset 作用域唯一、人工提升、事务更新、保留审计 |
| Model Registry | FineVision 自有实现 | 不部署 MLflow Tracking Server/Model Registry |
| 训练产物存储 | PostgreSQL 逻辑归属 + MinIO SHA-256 内容寻址 | 按 Dataset Version 隔离适用范围，不按 Dataset 目录重复字节 |

以上决策已获得实施授权。若改变视觉方向、backbone 范围、动态传输方案或 Model Registry 事实源，必须先修改本文并说明迁移与兼容影响；涉及架构边界时另增 ADR。
