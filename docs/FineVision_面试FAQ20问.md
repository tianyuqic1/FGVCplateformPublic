# FineVision 面试 FAQ 20 问

> 适用场景：项目答辩、实习/校招/社招面试、简历项目追问。  
> 回答口径：突出 FineVision 不是单点分类 demo，而是一个围绕数据集版本、模型版本、推理拒识、人工复核和反馈闭环构建的视觉模型控制平面。

| 序号 | 面试官高频问题 | 考察点 | 参考回答 |
|---:|---|---|---|
| 1 | 这个项目一句话怎么介绍？ | 是否能讲清项目定位 | FineVision 是一个面向细粒度图像分类任务的可审计模型工作台。它把数据集导入、DINOv3 特征提取、轻量分类头训练、置信度校准、阈值拒识、推理、人审复核、反馈池和策略更新串成闭环。它不是只回答“这张图是什么”，而是回答“这个结果来自哪个数据集版本、哪个模型版本、是否可信、是否需要人工复核、后续如何改进”。 |
| 2 | 为什么要做成平台，而不是普通图像分类 demo？ | 产品理解和工程抽象能力 | 普通分类 demo 只展示预测结果，但真实业务还需要模型血缘、数据版本、阈值策略、低置信度处理、人审反馈和审计能力。FineVision 的重点是把模型训练和推理变成可追踪、可复核、可持续优化的流程，所以更像一个轻量 MLOps 控制平面。 |
| 3 | 项目的整体架构是什么？ | 系统设计能力 | 项目采用前后端分离加 worker 的架构。前端是 React/Vite 工作台；后端是 FastAPI 控制平面；PostgreSQL 保存元数据和状态；ml-worker 执行数据导入、特征提取、训练、校准和阈值生成；artifact storage 保存图片、特征、模型权重和报告等大文件。API 负责调度和管理，worker 负责重计算。 |
| 4 | 每个 Docker 容器分别负责什么？ | 部署结构和服务边界 | `frontend` 负责前端页面；`api` 负责 FastAPI 控制平面和接口；`ml-worker` 负责机器学习长任务；`postgres` 保存结构化元数据；`migrate` 执行 Alembic 数据库迁移；`adminer` 用于开发阶段查看数据库。GPU compose 配置会给 api 和 worker 挂载 NVIDIA GPU，主要用于 DINOv3 特征提取和线性头训练加速。 |
| 5 | 为什么 API 和 worker 要拆开？ | 异步任务和稳定性设计 | 特征提取、训练、校准和批量推理都属于耗时任务，如果放在 HTTP 请求里，会导致请求阻塞、超时和服务不稳定。API 只负责创建 job、管理状态和返回结果；worker 轮询数据库任务并执行重计算。这样可以让控制平面保持响应稳定，也方便后续扩展多个 worker。 |
| 6 | 为什么使用 PostgreSQL？里面存什么？ | 数据持久化设计 | PostgreSQL 用来保存控制平面的元数据、生命周期状态和审计关系，例如 datasets、dataset_versions、jobs、training_runs、model_versions、inference_events、review_items、feedback_items 和 abstention_policy_versions。图片、features.npz、模型权重和报告不直接放数据库，而是作为 artifact 文件保存，数据库只登记 URI、类型、状态和关联关系。 |
| 7 | 什么是 dataset_version？为什么重要？ | 数据血缘和可复现性 | dataset_version 是某个数据集的不可变快照。模型训练、特征缓存、推理事件、人审反馈和阈值策略都绑定到 dataset_version_id。这样做可以保证模型结果可追溯：我们能知道某个模型到底基于哪一版数据训练，后续数据新增或标签修正也不会污染历史记录。 |
| 8 | 为什么选择 DINOv3 + 轻量分类头？ | 算法路线取舍 | DINOv3 提供强视觉表征，MVP 阶段使用 frozen backbone 提取特征，再训练线性分类头。这样训练成本低、迭代快、特征 artifact 可以复用，也更容易做版本管理。端到端 fine-tuning 上限可能更高，但训练成本、工程复杂度和可复现难度都更大，所以不是当前 MVP 的首选。 |
| 9 | 训练流程具体是什么？ | ML pipeline 理解 | 训练流程大致是：导入 ImageFolder 数据集，生成 manifest 和 dataset_version；用 DINOv3 提取 CLS token 特征；训练 torch linear Adam 分类头；在验证集/测试集上评估 accuracy、macro F1 等指标；做 temperature scaling 校准；生成 threshold sweep 和 threshold strategy；最后注册 model_version 和相关 artifacts。 |
| 10 | 什么是 calibration？为什么要做置信度校准？ | 对模型置信度可靠性的理解 | softmax 分数不一定等于真实可信度，模型可能过度自信或过度保守。FineVision 用 temperature scaling 对 logits 做校准，让 confidence 更接近真实准确率。因为后续弃权策略依赖 confidence，如果置信度不可靠，自动放行和人工复核的边界就会不稳定。 |
| 11 | 一个数据集刚训练好后，弃权策略是怎么来的？ | 阈值策略生成逻辑 | 刚训练好的模型会基于验证集生成初始 threshold_strategy。流程是：先做置信度校准，然后在验证集上尝试多个 confidence threshold，计算每个阈值下的 coverage、selective risk 和 review cost；最后选择满足 target selective risk 的候选中 coverage 最高的阈值。系统还会估计 margin_threshold，用于过滤 top1/top2 差距太小的样本。 |
| 12 | Selective risk 是什么意思？ | 核心指标理解 | Selective risk 是“自动接受样本里的错误率”。公式是：自动接受但预测错误的数量 / 自动接受的总数量。它不是全量错误率，而是模型决定自己有把握自动处理的那一部分的错误率。FineVision 的目标是在 selective risk 不超过目标值的情况下，提高自动覆盖率，减少人工复核成本。 |
| 13 | Auto coverage 和 review rate 分别是什么？ | 指标解释能力 | Auto coverage 是模型自动接受的样本占全部样本的比例。Review rate 是需要人工复核的比例，通常约等于 1 - auto coverage。比如 auto coverage 为 87%，说明 100 张图里大约 87 张模型能自动处理，剩下 13 张进入人工复核。 |
| 14 | 什么是 abstain 和 reject_ood？ | 拒识机制理解 | FineVision 的推理决策有三类：`accept` 表示模型足够确定，自动接受；`abstain` 表示置信度或 margin 不够，进入人工复核；`reject_ood` 表示疑似分布外样本，也进入复核或 OOD 处理。这样模型不会对不确定或明显不属于当前类别空间的图片强行给答案。 |
| 15 | OOD recall 和 OOD accept rate 怎么理解？ | OOD 风险控制 | OOD recall 表示真实 OOD 样本中有多少被系统成功拦截。OOD accept rate 表示 OOD 样本中有多少被错误自动放行。理想状态是 OOD recall 高，OOD accept rate 低。对业务来说，OOD 被高置信度误判成正常类别是很危险的，所以 FineVision 单独关注这个指标。 |
| 16 | 人工复核在系统里起什么作用？ | Human-in-the-loop 设计 | 人工复核用于处理模型低置信度、低 margin、OOD 候选、坏图或类别争议样本。复核员看到模型 top-k、置信度、阈值原因、图片上下文和 LLM 辅助建议后，给出最终结论。这个结论会进入 feedback pool，而不是直接覆盖训练集。 |
| 17 | 为什么反馈不能直接写回训练集？ | 数据质量和反馈治理 | 因为人工复核结果不全都是可训练样本。有些是 corrected_label，可以作为训练候选；有些是 OOD，应该进入 OOD stress pool；有些是 bad_image，应该排除；有些是 taxonomy_dispute，说明类别定义需要治理。直接全部写回训练集会污染数据，所以 FineVision 使用 typed feedback outcome 和 destination 做分流。 |
| 18 | LLM 在项目里负责什么？为什么不能替代人工？ | AI 辅助边界和安全性 | LLM 在 FineVision 中是 advisory-only。它可以辅助生成 dataset card、解释类别差异、给复核员提供观察建议，但不能设置最终标签、提交复核、调整阈值、激活策略或修改模型版本。原因是 LLM 输出不稳定，可能幻觉，且难以严格回放。最终标签和策略变更必须由人或可审计规则控制。 |
| 19 | 运行一段时间后，新的在线弃权策略怎么产生？ | 反馈驱动优化 | 初始 threshold_strategy 来自验证集；运行后系统会积累 inference_events、review_items 和 feedback_items。在线策略会基于同一个 dataset_version + model_version 下的反馈样本，回放不同阈值组合，计算 coverage、selective risk、review cost 和决策差异，然后生成 abstention_policy_version。新策略默认先进入 shadow mode，不直接影响真实推理，需要人工审核后才能激活。 |
| 20 | 这个项目的最大亮点和不足分别是什么？ | 项目总结和反思能力 | 最大亮点是把视觉分类从“预测一个类别”扩展成了可审计的模型运营闭环，包括数据版本、模型版本、artifact、校准、拒识、人审、反馈和策略版本化。不足是当前仍是 MVP：高吞吐批量推理还应进一步下沉到 worker；生产级权限、发布审批、分布式训练、向量检索服务和更完整的浏览器 E2E 还没有完全展开。 |

## 面试总括话术

如果面试官让你总结，可以这样回答：

> FineVision 的核心不是单纯提升分类准确率，而是让视觉模型在真实业务里可用、可控、可审计。它通过数据集版本和模型版本保证血缘可追踪，通过 calibration 和 selective risk 控制自动放行风险，通过 abstention 和 OOD 检测把不确定样本交给人工，通过 feedback pool 支持后续策略优化。整个架构上，前端负责操作，API 负责控制平面，worker 负责重计算，PostgreSQL 负责元数据，artifact storage 负责大文件。

