# 图像分类训练参数扩展

## 配置

- ImageNet：`head_learning_rate` 与 `backbone_learning_rate` 分别控制分类头、全部骨干参数。
- DINOv3：`head_learning_rate` 控制分类头；启用 LoRA 才接受 `lora_learning_rate`，仅控制 A/B 参数，原骨干不参与优化器更新。
- 前端初始值：分类头 1e-3、骨干 1e-5、LoRA 1e-4；各组可独立设置，支持科学计数法。服务拒绝非有限值、非数值、非正值、大于 1 或不适用于当前模式的学习率。
- 兼容历史任务：未指定分组学习率时，沿用原 `learning_rate`；规范化后的有效分组学习率写入任务记录，新检查点和训练报告也记录实际值。
- `image_size`：界面提供 224、256、320、384、448、512。后端范围 128–512，ViT 必须为 16 的倍数。
- ImageNet ViT 在载入预训练权重后、初始化优化器前插值绝对位置编码。重载检查点时先恢复对应网格；DINO 使用其动态 RoPE，ResNet 使用卷积空间适配。完整 ONNX 沿用训练分辨率。

## 增强

`head_config.augmentations` 是布尔选项对象，默认全关闭，仅作用于训练集。

| 标识 | 设置 |
|---|---|
| random_resized_crop | 面积 0.7–1.0，宽高比 0.75–4/3，输出所选尺寸 |
| horizontal_flip | 概率 0.5 |
| vertical_flip | 概率 0.5 |
| color_jitter | 亮度/对比度/饱和度 0.2，色相 0.05 |
| random_rotation | ±15° |
| random_erasing | 概率 0.25，面积 0.02–0.15，归一化后填充 0 |

验证、测试及推理使用原确定性 resize/crop/normalize；全部增强关闭时与原预处理完全相同。随机增强的强度本期为固定值，界面负责逐项启停，不包含 MixUp/CutMix。

详情新增训练参数卡，展示分辨率、轮数、批大小、适用的分组学习率、LoRA rank、权重衰减、增强项；未记录的历史字段不伪造为新默认值。

## 测试

- 43 项 Python 测试通过：有效参数组无遗漏/重复、冻结参数不变化、学习率非法值、增强非法选项、每种增强的输出形状和有限值、验证集确定性。
- 真实 ViT-S：DINO 冻结、LoRA r8/r16、ImageNet 全参数，分别在 224/320 下训练 1 轮，320 使用全部增强，保存/重载后导出 FP32/FP16 ONNX，与实际图片预测数值对齐。
- Go 全量测试及 go vet；HTTP 覆盖三个骨干的六档分辨率、非法尺寸拒绝、详情接口保留输入尺寸与学习率；PostgreSQL 集成回归通过。
- 前端 build、training-client 与 training-parameters smoke：模式切换不发送无关学习率、配置数值化、详情格式化通过。
- 浏览器检查：DINO LoRA 学习率切换、ImageNet 骨干学习率展示、增强选项和分辨率布局正常。
- 本地完整队列验收：Toy Shapes 12 张图片，任务 `e995df81-c9dd-4437-ab1e-d3917847da2a`，DINOv3 LoRA r8、320×320、1 epoch、batch 2、分类头 0.001 / LoRA 0.0002、随机裁剪和水平翻转；HTTP → RabbitMQ → worker → MinIO → 任务详情完成，状态 succeeded。产出候选模型 `4fb38518-df8c-4fd0-990e-0449064a0562`，未发布。浏览器实际详情已核对保存的参数。

模型质量和不同增强组合对准确率的收益不属于这些 1 轮工程测试的结论；ResNet 未执行实际训练验收。
