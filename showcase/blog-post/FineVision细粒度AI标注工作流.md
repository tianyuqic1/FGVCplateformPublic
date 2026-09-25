---
title: 从一张图片到可发布数据集：FineVision 细粒度 AI 标注工作流的设计与实测
published: 2026-09-14
description: 详解 FineVision 如何编排 VLM、主体定位、SAM2、Retinexformer、Restormer、SwinIR、CLIP 与 Qdrant，在人工确认边界内生成细粒度 Top-10 候选，并用 9,600 个样本-方法案例验证效果、时延和稳定性。
tags:
  - FineVision
  - 细粒度图像分类
  - 多模态大模型
  - CLIP
  - Qdrant
  - SAM2
  - Human-in-the-Loop
category: 项目
draft: false
alias: finevision-annotation-workflow
---

9,600 个样本-方法案例跑完以后，我得到的不是一个“万能 AI 标注器”，而是一条边界更清楚的工程结论：在简单数据集上，直接让视觉大模型看图已经接近上限；在 CUB-200-2011 和退化图像上，主体处理、图像检索与文本原型融合才真正体现价值。

四个方法在四个集合上的合并结果如下：

| 方法 | 有效分类 | Top-1 | Top-5 | Top-10 |
|---|---:|---:|---:|---:|
| A · Direct VLM | 2,400 / 2,400 | 70.04% | 91.21% | 94.17% |
| B · Workflow | 2,399 / 2,400 | 71.25% | 91.79% | 94.71% |
| C · Image RAG | 2,400 / 2,400 | 73.13% | 93.17% | 95.92% |
| D · Image + Text RRF | 2,396 / 2,400 | 74.50% | 94.33% | 96.54% |

这里的 Top-10 是“正确类别是否进入候选前十”，不是模型自报置信度。失败和结果未知都按未命中计入总分母，没有只统计成功请求。

这篇文章完整拆解这条工作流：为什么不把所有网络串行跑一遍，CLIP 的视觉与文本分支如何进入 Qdrant，什么时候允许重试，人工确认以后数据如何进入检索记忆，以及标注结果怎样成为一个新的、可训练的数据集版本。

> 更完整的平台页面、训练与推理契约可以查看 [FineVision 项目展示文档](https://finevisiondoc.azusacat.cn/)。本文集中讲 AI 标注子系统及其测评。

## 一、系统全貌：模型给证据，人给最终标签

![FineVision AI 标注完整工作流](/images/posts/finevision-annotation-workflow/annotation-workflow.png)

![FineVision AI 标注工作区](/images/posts/finevision-annotation-workflow/annotation-ui.png)

页面左侧是项目图片队列，中间保留原图作为人工判断的权威证据，右侧展示 Top-10、完整类别选择和人工确认操作。批量分析只改变任务队列，不会越过人工确认。

工作流分为五层：

1. **控制面**：Go API 接收项目、类别目录、图片和批量任务，PostgreSQL 保存状态、人工标签、Outbox 与发布记录。
2. **图像工具层**：本地 Qwen-VL 定位主体，SAM2 分割并生成主体视图，质量规则按需选择增强网络。
3. **检索记忆层**：CLIP 编码原图、主体图和类别文本原型，Qdrant 保存由人工确认样本构成的向量记忆。
4. **分类决策层**：远程视觉大模型读取受控图片集合、完整类别目录和检索证据，输出结构化 Top-10。
5. **人工闭环层**：人工确认或修改类别；确认结果才有资格写入记忆，并可进入待发布区生成新数据版本。

~~~mermaid
flowchart LR
    U[上传图片与 JSON 类别目录] --> C[Go 控制面]
    C --> P[(PostgreSQL<br/>任务/标签/Outbox)]
    C --> W[Python 标注 Worker]
    W --> Q[主体定位与质量判断]
    Q --> S[SAM2 分割/裁剪]
    Q --> T[按需调用一种增强工具]
    S --> V[多视图集合]
    T --> V
    V --> E[CLIP 图像编码]
    E --> D[(Qdrant<br/>人工确认记忆)]
    V --> M[视觉大模型]
    D --> R[图像近邻候选]
    X[CLIP 文本原型] --> F[RRF 候选融合]
    R --> F
    F --> M
    M --> K[结构化 Top-10]
    K --> H[人工确认/修正]
    H --> P
    P --> O[Memory Outbox]
    O --> D
    H --> B[待发布区]
    B --> N[新数据集或已有数据集新版本]
~~~

最重要的约束只有一句话：**模型生成的是候选和证据，不是真值。**

工作流失败、返回缺类、输出格式错误或者远程请求状态未知时，样本仍然保留人工完成入口。系统不会为了让流程“看起来成功”而自动提交标签。

## 二、输入契约：为什么类别目录只接受 JSON

每个标注项目在创建时冻结一份类别目录。当前只接受 `schema_version=1` 的 JSON：

~~~json
{
  "schema_version": 1,
  "name": "CUB-200 catalog",
  "classes": [
    {"id": "001", "name": "Black-footed Albatross"},
    {"id": "002", "name": "Laysan Albatross"}
  ]
}
~~~

`id` 是接口和存储使用的稳定身份，`name` 用于人读、提示词和文本检索。两者都必须非空且各自唯一；未知字段会被拒绝。

冻结目录解决了三个容易被忽略的问题：

- VLM 不能返回目录之外的自由文本类别。
- CLIP 文本原型、图像记忆和人工下拉框使用同一套类别身份。
- 数据发布时不需要重新猜测字符串映射，避免 `Black footed Albatross`、`Black-footed Albatross` 和目录编号之间发生漂移。

批量分析也不是把“当前页面的 12 张图”循环点击。控制面先冻结任务快照，再逐项排队；翻页或关闭浏览器不会改变这次批处理的成员。

## 三、图像预处理：不是所有图片都跑完所有网络

第一版流程最大的问题是慢。只要把 Qwen-VL、SAM2、低光增强、去模糊、去噪和超分按顺序串起来，任何一张正常图片也会支付完整成本，而且多个恢复网络连续作用容易制造伪影。

最终方案使用“**规则先筛选，模型再选择，单图至多一种增强**”的受限工具策略。

~~~mermaid
flowchart TD
    I[原图] --> A[计算亮度/清晰度/分辨率等质量指标]
    A --> L[Qwen-VL 定位主体]
    L --> S[SAM2 分割并生成主体视图]
    A --> G{质量规则是否允许增强?}
    G -- 否 --> O[保留原图 + 合格主体图]
    G -- 是 --> Z{VLM 从允许清单选择一种工具}
    Z -- lowlight --> R1[Retinexformer]
    Z -- motion/denoise/defocus --> R2[Restormer 对应任务权重]
    Z -- super_resolution --> R3[SwinIR ×2]
    Z -- none/非法选择 --> O
    R1 --> C[输出质量复检]
    R2 --> C
    R3 --> C
    C -- 通过 --> V[原图 + 主体图 + 增强图]
    C -- 不通过 --> O
~~~

### 3.1 主体定位与 SAM2

本地小型 Qwen-VL 只负责定位主要对象，不负责最终类别。它输出候选框，SAM2 根据框生成掩码，并把主体从背景中分离出来。

主体视图必须经过几何与像素来源检查：框不能过小、不能越界，裁剪后的有效区域不能退化成空图；主体图必须能追溯到当前原图的像素摘要。定位或分割失败时回退原图，不伪造主体结果。

### 3.2 质量工具清单

| 工具 | 触发问题 | 在流程中的角色 | 失败处理 |
|---|---|---|---|
| Retinexformer | 低光、暗部细节不足 | 生成补充亮度视图 | 质量复检失败则丢弃 |
| Restormer · motion | 运动模糊 | 恢复轮廓与局部纹理 | 不串联其他恢复模型 |
| Restormer · denoise | 高斯噪声 | 降噪后作为补充证据 | 原图始终保留 |
| Restormer · defocus | 散焦模糊 | 尝试恢复边缘 | 出现伪影时回退 |
| SwinIR ×2 | 原生低分辨率 | 放大局部纹理 | 只提高可读性，不创造真值 |

| 低光输入 | Retinexformer 输出 |
|---|---|
| ![低光增强前](/images/posts/finevision-annotation-workflow/enhance-lowlight-input.png) | ![低光增强后](/images/posts/finevision-annotation-workflow/enhance-lowlight-output.png) |

| 运动模糊输入 | Restormer 输出 |
|---|---|
| ![运动去模糊前](/images/posts/finevision-annotation-workflow/enhance-motion-input.png) | ![运动去模糊后](/images/posts/finevision-annotation-workflow/enhance-motion-output.png) |

工作流不会把增强图写成标注对象。标签始终绑定原图；向量记忆保存原图和通过来源校验的主体图，不保存增强图。这样可以避免恢复网络的伪影被沉淀成“类别特征”。

## 四、CLIP 与 Qdrant：视觉分支和文本分支怎样配合

CLIP 有图像编码器和文本编码器，两侧输出落在同一个向量空间。但“同一个空间”不等于应该把所有向量混在一个无范围集合里。

FineVision 把记忆范围固定为：

~~~text
project_id + catalog_digest + encoder_revision
~~~

项目不同、类别目录变化或编码器版本变化，都会进入不同的检索范围。

### 4.1 图像分支

人工确认后的原图与主体图分别经过 CLIP 图像编码器：

~~~text
original image ─┐
                ├─ CLIP image encoder → normalized vector → Qdrant
subject view  ──┘
~~~

Qdrant point 使用来源 SHA 派生的稳定 UUID。重复投递执行幂等 upsert，不会因为 Worker 重启就把同一张图计成多个参考样本。

查询时同样编码原图和合格主体图，分别检索近邻，再按类别聚合。只有某一类别至少拥有 10 张不同来源、人工确认的参考图时，该类别才具备检索成熟度。派生裁剪不能重复增加成熟度。

### 4.2 文本分支

每个类别生成三种受控模板，而不是只编码一个裸标签：

~~~text
"a photo of a {class_name}"
"a fine-grained image of {class_name}"
"visual characteristics of {class_name}"
~~~

模板通过 CLIP 文本编码器得到类别原型，查询图向量与原型计算相似度，形成文本候选排名。文本原型不进入人工确认图像库，也不会提高“每类参考图数量”。

### 4.3 RRF 融合

图像近邻和文本原型各自保留排名，再用 Reciprocal Rank Fusion 融合：

~~~text
score(label) = Σ 1 / (k + rank_i(label))
~~~

RRF 使用名次而不是直接混合相似度，降低不同分支分数尺度不一致的影响。融合结果是 VLM 的候选提示，不是最终答案；完整类别目录仍随请求提供，避免检索漏召回后把正确类别永久裁掉。

~~~mermaid
flowchart LR
    Q1[查询原图] --> IE[CLIP Image Encoder]
    Q2[查询主体图] --> IE
    IE --> IS[Qdrant 图像近邻]
    H[人工确认参考图] --> HE[CLIP Image Encoder]
    HE --> QD[(Qdrant)]
    QD --> IS
    C[完整 JSON 类别目录] --> TP[三模板文本原型]
    TP --> TE[CLIP Text Encoder]
    TE --> TS[文本类别排名]
    IS --> RRF[RRF 按类别融合]
    TS --> RRF
    RRF --> VLM[视觉大模型候选提示]
    C --> VLM
~~~

## 五、VLM 请求：完整目录、有限图片、结构化返回

发送给最终视觉大模型的内容包括：

- 原图；
- 通过校验的主体图；
- 至多一张增强图；
- 有预算上限的检索参考图；
- 完整类别目录；
- 图像检索与文本原型的融合候选；
- 严格 JSON 输出契约。

返回结果至少包含候选类别、排序和可读说明。控制面收到后再次做目录过滤、去重与补足。自由文本、未知类别、重复类别或字段缺失不能直接进入数据库。

Top-10 不被解释成概率。它只表示模型在当前证据下给出的相对排序。人工可以选择 Top-10 候选，也可以从完整目录里选择一个未被召回的类别。

## 六、重试与故障恢复：未知结果不能盲目重发

本地图像处理和远程模型调用的恢复语义不同。

| 失败位置 | 可恢复动作 | 不能做的事 |
|---|---|---|
| 本地裁剪/增强明确失败 | 在相同输入摘要和配置下重算，或回退原图 | 读取被替换缓存却沿用旧 SHA |
| Provider 明确返回限流/临时错误 | 遵守 `Retry-After`，在预算和次数内退避重试 | 无上限并发重试 |
| 请求已发出但完成状态未知 | 标记 unknown，隔离并保留人工入口 | 自动重发并假设不会重复计费 |
| 远程结果已落盘但业务 ACK 丢失 | 以相同请求 ID 重发 ACK | 重跑整套工作流 |
| Qdrant 写成功但回执丢失 | 相同 point ID 幂等 upsert | 每次生成新 point 并增加成熟度 |

~~~mermaid
stateDiagram-v2
    [*] --> Pending
    Pending --> LocalProcessing
    LocalProcessing --> ReadyForRemote: 视图与摘要校验通过
    LocalProcessing --> ReadyForRemote: 回退原图
    ReadyForRemote --> Dispatched
    Dispatched --> Completed: 收到并校验结构化结果
    Dispatched --> Retryable: 明确限流/临时错误
    Retryable --> Dispatched: 冷却且预算未耗尽
    Dispatched --> Unknown: 已发送但结果不确定
    Unknown --> NeedsHuman
    Completed --> NeedsHuman: 候选不足或契约降级
    Completed --> AwaitingConfirmation
    AwaitingConfirmation --> Confirmed: 人工提交
    NeedsHuman --> Confirmed: 人工独立完成
    Confirmed --> MemoryOutbox
    MemoryOutbox --> Indexed: Qdrant 幂等写入
    Confirmed --> PublicationPool
~~~

远程适配器记录 `request_id`、是否已经 dispatch、失败阶段、HTTP 状态、`Retry-After`、供应商请求 ID 和耗时。连接超时、首包超时、流式中断不能被折叠成一个笼统的 `timeout`。

并发也有上限：请求先进入有界槽位，Writer 和 Reader 用 request ID 多路复用。适配进程重启时，属于旧进程的未决 Future 会被标成 unknown，迟到输出不能完成新请求。

## 七、人工确认以后：记忆入库与数据集发布是两条链

人工点击确认时，PostgreSQL 在同一事务里完成两件事：保存最终标签，写入 Memory Outbox。事务提交后，独立消费者将原图和合格主体图编码并写入 Qdrant。

这样做的原因是 PostgreSQL 和 Qdrant 没有跨系统事务。若确认标签已经提交但向量服务短暂不可用，Outbox 仍可重试；若 Qdrant 写入完成但回执丢失，稳定 point ID 可以安全 upsert。

数据发布则经过待发布区：

~~~text
confirmed samples
  → 全选或人工挑选
  → 新建数据集 / 追加已有数据集
  → 设置 train / val / test 比例
  → 按类别确定性分配
  → 冻结发布预览（成员、去重、split、SHA）
  → 提交
  → 生成新的 Dataset Version
~~~

已有数据集版本不原地改标签、不原地追加文件。追加行为生成下一版本；旧版本仍可复现历史训练。推理回流使用相同机制，但新样本固定进入目标数据集下一版本的训练集，并保留来源 inference event 与人工确认记录。

## 八、测评怎样设计：48 个冻结单元，而不是挑一轮最好成绩

测评矩阵由四个数据集、三个固定随机轮次和四种方法组成：

~~~text
4 datasets × 3 rounds × 4 methods × 200 queries
= 9,600 sample-method cases
~~~

### 8.1 数据集

| 难度 | 数据集 | 查询规模 | 冻结参考库 | 关注点 |
|---|---|---:|---:|---|
| 简单 | ImageNet-100 | 3 × 200，100 类 | 每类 10 张，共 1,000 张 | 通用视觉语义与目录遵循 |
| 中等 | Stanford Cars | 3 × 200，196 类 | 每类 10 张，共 1,960 张 | 车型年份、外观和近邻混淆 |
| 困难 | CUB-200-2011 | 3 × 200，200 类 | 每类 10 张，共 2,000 张 | 鸟类姿态、局部形态和背景干扰 |
| 鲁棒性 | CUB Robustness | 3 × 200，200 类 | 复用 CUB 冻结训练参考库 | 低光、运动模糊、噪声、散焦、低分辨率 |

下面六张是实际进入鲁棒性测评的退化样本，而不是为了文章另选的示意图：

| 运动模糊 | 低光 | 高斯噪声 |
|---|---|---|
| ![运动模糊样本](/images/posts/finevision-annotation-workflow/robust-0001.png) | ![低光样本](/images/posts/finevision-annotation-workflow/robust-0002.png) | ![高斯噪声样本](/images/posts/finevision-annotation-workflow/robust-0067.png) |

| 低分辨率 | 散焦模糊 | 垂直运动模糊 |
|---|---|---|
| ![低分辨率样本](/images/posts/finevision-annotation-workflow/robust-0020.png) | ![散焦模糊样本](/images/posts/finevision-annotation-workflow/robust-0010.png) | ![垂直运动模糊样本](/images/posts/finevision-annotation-workflow/robust-0004.png) |

三组干净查询共 1,800 张且互不重复；鲁棒性集从 CUB 查询确定性派生 600 张退化图。查询和参考按 source ID、文件 SHA-256 与 RGB 像素摘要三重排重。

### 8.2 四种方法

| 方法 | 图像处理 | 图像检索 | 文本分支 |
|---|---|---|---|
| A · Direct VLM | 只发送原图 | 关闭 | 关闭 |
| B · Workflow | 原图 + 合格主体 + 至多一种增强 | 关闭 | 关闭 |
| C · Image RAG | 与 B 相同 | CLIP + 冻结 Qdrant 库 | 关闭 |
| D · Image + Text RRF | 与 C 相同 | 同一冻结图像库 | 三模板类别原型 |

B、C、D 使用同一份主体处理快照；C、D 使用同一参考库和图片预算。每个单元只产生一次正式预测，结果锁定后才加载真值计分。

## 九、完整结果：A/B/C/D × 四个数据集

每行均以 600 张为总分母。`有效分类` 单独衡量调用稳定性，失败仍计入 Top-k 未命中。

| 数据集 | 方法 | 有效分类 | Top-1 | Top-5 | Top-10 | 平均耗时 |
|---|---|---:|---:|---:|---:|---:|
| ImageNet-100 | A · Direct | 600 / 600 | 85.67% | 97.50% | 99.33% | 2.07 s |
| ImageNet-100 | B · Workflow | 600 / 600 | 86.00% | 97.83% | 99.33% | 3.76 s |
| ImageNet-100 | C · Image RAG | 600 / 600 | 83.33% | 97.00% | 98.83% | 5.29 s |
| ImageNet-100 | D · Image + Text | 599 / 600 | 85.50% | 97.83% | 99.00% | 5.08 s |
| Stanford Cars | A · Direct | 600 / 600 | 78.50% | 97.33% | 98.33% | 2.19 s |
| Stanford Cars | B · Workflow | 600 / 600 | 79.00% | 98.17% | 98.83% | 3.38 s |
| Stanford Cars | C · Image RAG | 600 / 600 | 81.00% | 98.67% | 99.67% | 4.84 s |
| Stanford Cars | D · Image + Text | 597 / 600 | 81.33% | 98.33% | 99.00% | 4.70 s |
| CUB-200-2011 | A · Direct | 600 / 600 | 62.33% | 88.83% | 92.50% | 异常批次* |
| CUB-200-2011 | B · Workflow | 599 / 600 | 63.83% | 89.33% | 93.17% | 3.92 s |
| CUB-200-2011 | C · Image RAG | 600 / 600 | 68.83% | 91.67% | 95.33% | 5.19 s |
| CUB-200-2011 | D · Image + Text | 600 / 600 | 69.67% | 93.33% | 95.83% | 6.14 s |
| CUB Robustness | A · Direct | 600 / 600 | 53.67% | 81.17% | 86.50% | 2.30 s |
| CUB Robustness | B · Workflow | 600 / 600 | 56.00% | 81.83% | 87.50% | 4.33 s |
| CUB Robustness | C · Image RAG | 600 / 600 | 59.33% | 85.33% | 89.83% | 5.81 s |
| CUB Robustness | D · Image + Text | 600 / 600 | 61.50% | 87.83% | 92.33% | 5.96 s |
| 四集合并 | A · Direct | 2,400 / 2,400 | 70.04% | 91.21% | 94.17% | — |
| 四集合并 | B · Workflow | 2,399 / 2,400 | 71.25% | 91.79% | 94.71% | — |
| 四集合并 | C · Image RAG | 2,400 / 2,400 | 73.13% | 93.17% | 95.92% | — |
| 四集合并 | D · Image + Text | 2,396 / 2,400 | 74.50% | 94.33% | 96.54% | — |

\* CUB 第一轮 A 方法经历首包超时和延迟队列恢复，锁定时长不能代表正常在线延迟，因此不汇报其聚合耗时。

### 9.1 增量来自哪里

以下比较使用 Top-10 百分点差值：

| 比较 | ImageNet-100 | Cars | CUB | CUB Robustness | 四集合并 |
|---|---:|---:|---:|---:|---:|
| B − A：工具工作流 | 0.00 | +0.50 | +0.67 | +1.00 | +0.54 |
| C − B：图像检索 | -0.50 | +0.84 | +2.16 | +2.33 | +1.21 |
| D − C：文本 RRF | +0.17 | -0.67 | +0.50 | +2.50 | +0.62 |

图像检索是 CUB 与鲁棒性集提升的主要来源；文本分支在退化图上继续提升，但在 Cars 上出现回退。因此文本融合应当可配置，不能因为某一集合有效就全局强制开启。

完整方案 D 相比直接看图 A：

- CUB Top-1 提升 7.34 个百分点，Top-10 提升 3.33 个百分点；
- CUB Robustness Top-1 提升 7.83 个百分点，Top-10 提升 5.83 个百分点；
- ImageNet-100 已接近上限，复杂流程没有带来净收益。

这也是为什么最终系统不把 D 当作所有图片的固定默认路径，而是保留按数据集、质量和成本配置的能力。

## 十、稳定性、工具调用与成本

整个矩阵中，9,595 / 9,600 个案例形成有效分类，完成率为 99.95%。共有 14,102 条完成尝试记录，其中 14 次是结果未知，9 个阶段在明确允许的条件下发生重试。

D 方法的加权平均单张耗时为：

| 数据集 | 平均耗时 |
|---|---:|
| ImageNet-100 | 5.08 s |
| Stanford Cars | 4.70 s |
| CUB-200-2011 | 6.14 s |
| CUB Robustness | 5.96 s |

直接看图通常约 2.0–2.3 秒/张。完整流程增加了本地处理、检索与更多上下文，换取困难样本上的候选召回。

工具层并非每张都执行。以鲁棒性集 D 方法的 600 张为例：

| 路径 | 次数 | 占比 |
|---|---:|---:|
| 运动去模糊 | 38 | 6.33% |
| 低光增强 | 12 | 2.00% |
| 无增强 | 548 | 91.33% |
| 无效工具选择并降级 | 2 | 0.33% |

三种工作流方法在鲁棒性集共实际执行 142 次增强，在三个干净集合计执行 15 次。这证明最终流程是按需门控，而不是把所有网络机械地串起来。

主体处理也保留回退统计。D 方法每个 600 张集合的主体处理回退分别为：ImageNet-100 30 次、Cars 4 次、CUB 5 次、鲁棒性集 19 次。回退样本继续使用原图，并保留在总分母里。

## 十一、如何解释结果：96.54% 不是“任意数据集 100%”

完整方案在 ImageNet-100、Cars、CUB 和 CUB Robustness 三轮合并 Top-10 分别是 99.00%、99.00%、95.83% 和 92.33%。

这支持三个结论：

1. **工作流对困难与退化图像有价值。** 图像检索和文本原型能补充单次 VLM 观察不到的类间证据。
2. **复杂度必须按场景支付。** 简单集上直接看图已经很强，完整流程不一定更好。
3. **目前不能声称任意高难度细粒度数据集稳定达到 97% 或 100%。** CUB 与鲁棒性集仍有明确差距，人工确认不是装饰，而是系统正确性的一部分。

下一轮最值得做的不是继续增加远程调用次数，而是提高 CUB 图像参考召回、对退化类型做配对置信区间、为 Cars 单独校准文本融合权重，并把 CLIP 编码器和常用本地模型保持常驻以降低热路径时延。

## 十二、最终取舍

这套工作流真正解决的不是“让一个大模型多看几张图”，而是把不同能力放进可审计的边界：

- Qwen-VL 负责定位，不负责最终类别；
- SAM2 负责主体视图，不覆盖原图；
- 增强网络按需调用，且最多一种；
- CLIP 图像分支检索人工确认记忆；
- CLIP 文本分支提供类别语义原型；
- Qdrant 保存向量与来源元数据，不保存最终业务真值；
- VLM 给出受目录约束的 Top-10；
- 人工确认产生标签；
- Outbox 保证确认结果最终进入记忆；
- 待发布区把确认样本冻结为新的数据集版本。

如果只看一张流程图，它像一条很重的 Agent 链路。但从实际调用统计看，大多数图片并不运行增强网络；从结果看，检索增强主要服务于困难样本；从故障语义看，远程未知状态不会被自动重试放大。复杂度只有在能被路由、记录和回退时才值得引入。

这也是 FineVision 对 AI 标注的最终定义：**模型尽可能提供有用候选，系统尽可能保存完整证据，人对最终标签负责。**
