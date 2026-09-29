---
license: other
license_name: dinov3-license
license_link: https://github.com/facebookresearch/dinov3/blob/main/LICENSE.md
library_name: timm
pipeline_tag: image-classification
tags:
  - knowledge-distillation
  - fine-grained-classification
  - inaturalist
  - mobilenetv3
---

# iNat2021-mini · DINOv3 ViT-B → MobileNetV3-Large

把 **DINOv3 ViT-B/16** 教师蒸馏进 **MobileNetV3-Large**，得到骨干仅 **2.97 M** 参数、可在 224×224 上高速推理的细粒度分类权重。

> 发布到 HuggingFace / ModelScope 时，请把本文件重命名为 **`README.md`**，并连同 `LICENSES/` 一起上传。

## ⚠️ 许可声明（请勿修改）

本权重是 **DINOv3 的衍生作品**，适用 **DINOv3 License**。

- **架构许可 ≠ 权重许可**：MobileNetV3 架构代码为 Apache-2.0，但本权重由 DINOv3 蒸馏而来，许可义务由教师模型传递。
- 依据 DINOv3 License **§1.b.i**，再分发本权重（及其衍生作品）时**必须随附协议副本**。本目录已包含 [`LICENSES/LICENSE-DINOv3.md`](LICENSES/LICENSE-DINOv3.md)，**请勿移除**。
- 依据 **§1.b.ii**，基于本权重发表研究成果时须致谢 DINOv3（见下方「致谢」）。
- 另受 §1.b.iii / iv / v 约束：合规使用、禁止逆向工程、遵守贸易管制。

元数据须标为 `license: other` + `license_name: dinov3-license`，**不要标为 `apache-2.0`**。

学生权重以 **torchvision** `MobileNetV3-Large IMAGENET1K_V2` 为初始化，故同时附带 **BSD-3-Clause** 署名（[`LICENSES/LICENSE-BSD-3-Clause-torchvision.txt`](LICENSES/LICENSE-BSD-3-Clause-torchvision.txt)）。

## 模型详情

| 项 | 值 |
|---|---|
| 架构 | MobileNetV3-Large（torchvision） |
| 教师 | `vit_base_patch16_dinov3.lvd1689m`（DINOv3 ViT-B/16，LVD-1689M） |
| 教师用法 | 冻结骨干 + 在 iNat 上训练的 **10,000 类线性探针头** |
| 学生初始化 | torchvision `mobilenet_v3_large IMAGENET1K_V2` |
| 学生骨干参数 | **2,996,398**（2.97 M） |
| 类别数 | **10,000** |
| 输入 | 224×224 RGB，短边 resize 224 bicubic → center crop 224 |
| 归一化 | mean `(0.485, 0.456, 0.406)`，std `(0.229, 0.224, 0.225)` |
| 精度 | FP32 |
| 训练数据 | iNaturalist 2021 **mini** 训练集（`train_mini`，500,000 图 / 10,000 类） |

## 文件

| 文件 | 字节 | 参数量 | SHA-256 |
|---|---:|---:|---|
| `inat2021-mini-mobilenetv3-large-backbone.safetensors` | 12,015,440 | 2,996,398 | `ae3990c5d4655d71fd988f0246dfe5e372ed88e5b9ac78cf18b681d56a7986b9` |
| `inat2021-mini-mobilenetv3-large-classifier.safetensors` | 45,733,424 | 11,425,982 | `e83caa8274f80e4aded84753204469a28bfdd2451c50ce0523852d2c940f3bc1` |
| `class_map.json` | 970,777 | — | `499df6d97f2a72d2c9b713da13e30bd295462719c231dcd54e36445ab5844c40` |
| `load_model.py` | 1,855 | — | `37790af5981bae1cd9002b39ee7fbf1be739284645079e4cc0788063f0f12e3e` |

### 两个权重文件是「两种部署方式」，不是互补的两半

- **`backbone`** 只含 `features.*`，输出 **960 维**池化嵌入 —— 用于冻结特征抽取（线性探针、检索）。
- **`classifier`** 含完整 `stem.*` + `rest.*` + `proj.*`（960→768）+ `norm.*` + `head.*`（768→10,000），是**自包含**的推理模型。

`stem` + `rest` 参数量合计 **2,996,398**，与 backbone 完全相等 —— 即两文件**都包含同一份卷积特征提取器**，卷积权重在包内重复了一次。若只需其中一种用法，可只分发对应文件。

## 训练详情

| 项 | 值 |
|---|---|
| 方式 | 知识蒸馏（离线教师缓存 + 学生训练） |
| run_id | `mini_kd_e20` |
| 损失 | `0.5 × CE(label_smoothing=0.1) + 0.3 × T²·KL`，温度 **T = 3** |
| 增强 | 两个固定视角逐 epoch 交替 |
| epoch | 20（交付 checkpoint 为 **epoch 18**） |
| batch / 梯度累积 | 128 / 2 |
| 教师缓存指纹 | `7ea96083801a772d` |

对照臂 `inat_ce`（同一流程去掉蒸馏项）用于隔离蒸馏增益。

## 评测结果

### iNaturalist 2021 官方 val（100,000 张）

| 模型 | top-1 |
|---|---:|
| 仅标签（`inat_ce`，best，epoch 20） | 51.78% |
| **蒸馏（`kd`，best，epoch 18）** | **58.40%** |
| 增益 | **+6.62 pp** |
| 教师线性探针（参考上界） | 71.38%（top-5 87.76%） |

### 下游细粒度迁移（冻结骨干 + 闭式 ridge 线性头，每类 5 张，3 seed 均值）

| 数据集 | ImageNet 初始化 | **本蒸馏权重** | DINOv3 ViT-S |
|---|---:|---:|---:|
| CUB-200（鸟，200 类） | 52.77% ±0.87 | **68.73% ±0.26** | 73.39% ±0.64 |
| NABirds（鸟，555 类） | 38.05% ±0.19 | **53.82% ±0.23** | 60.21% ±0.14 |
| Oxford Flowers-102（102 类） | 79.21% ±0.58 | **89.46% ±0.05** | 98.51% ±0.20 |
| Stanford Cars（196 类，域外） | 23.02% ±0.18 | 12.13% ±0.04 | — |

> **域外性能下降**：Stanford Cars 上蒸馏权重（12.13%）明显低于原始 ImageNet 权重（23.02%）。这是生物域特化的预期代价，**用于车辆等非生物域任务时请勿直接采用**。

Flowers-102 全量微调（解冻全部参数，20 epoch，3 seed）：每类 5 张 **84.64% ±0.38**，官方 train 全量 **92.56% ±0.06**。

### 推理性能

RTX 3090、bf16 autocast、仅骨干前向、输入已在 GPU（含 Python 调度与 GPU 同步）：

| 项目 | 本权重骨干 | DINOv3 ViT-S/16 |
|---|---:|---:|
| batch 1 p50 | 1.62 ms | 2.87 ms |
| batch 1 p95 | 1.66 ms | 2.92 ms |
| batch 32 吞吐 | 7,152 图/s | 2,342 图/s |
| 推理峰值显存 | 200.26 MiB | 219.47 MiB |

> 时序数据取自本包 `results.json` 的 `inference` 段。同目录 `蒸馏效果.md` 表 4 另有一组来自独立基准（`bench4.py`）的数字（1.69 ms / 7,234 图/s），两者不一致，发布前请统一。
> DINOv3 ViT-S 一列为 **随机初始化**权重的架构级基准（`results.json` 中 `vit_weights` 已注明），仅反映延迟与吞吐，不代表精度。

## 用法

```bash
pip install torch torchvision safetensors
```

```python
from load_model import load_classifier, load_backbone, preprocess
import json

# 完整推理（10,000 类）
model = load_classifier("inat2021-mini-mobilenetv3-large-classifier.safetensors")
class_map = json.load(open("class_map.json"))          # 长度 10000
logits = model(preprocess()(pil_image).unsqueeze(0))
name = class_map[logits.argmax(-1).item()]["scientific_name"]

# 仅骨干：960 维嵌入
backbone = load_backbone("inat2021-mini-mobilenetv3-large-backbone.safetensors")
embedding = backbone(preprocess()(pil_image).unsqueeze(0))   # (1, 960)
```

`class_map.json` 为 10,000 条 `{"index", "category_id", "scientific_name"}` 记录，logits 下标即 `index`。

## 数据集条款提示

本权重在 **iNaturalist 2021 mini** 上训练。数据集条款不会自动转为权重许可，但 iNaturalist 图像本身携带多种 CC 许可、iNat2021 亦有自身使用条款。**商用或再分发前请自行核对数据集条款** —— 与本仓库 `THIRD_PARTY_NOTICES.md` 第 33 行的立场一致：SHA 校验只证明文件身份与完整性，不构成授权证明。

## 致谢

依据 DINOv3 License §1.b.ii，使用或发表基于本权重的研究成果时须致谢 DINOv3：

> This work uses weights distilled from DINOv3 (Meta Platforms, Inc.), made available under the DINOv3 License. The DINOv3 materials and any derivative works are subject to the DINOv3 License.

> 本工作使用了由 DINOv3（Meta Platforms, Inc.）蒸馏得到的权重，该材料以 DINOv3 License 提供；DINOv3 材料及其衍生物均须遵守该协议。

学生初始化权重来自 torchvision（BSD-3-Clause，Copyright (c) Soumith Chintala 2016）。

## 发布地址

| 平台 | 地址 |
|---|---|
| **ModelScope** | [Tianyuqi/inat2021-mini-mobilenetv3-large](https://modelscope.cn/models/Tianyuqi/inat2021-mini-mobilenetv3-large) |
| HuggingFace | 暂未发布 |

## 合规状态

本包已按 DINOv3 License 的强制条款发布，使用者可据此确认再分发义务已履行：

- ✅ **随附协议副本** — 包内 [`LICENSES/LICENSE-DINOv3.md`](LICENSES/LICENSE-DINOv3.md) 与根目录 `LICENSE`（§1.b.i 强制项）
- ✅ **许可声明** — 仓库元数据标为 `dinov3-license`，未误标为 Apache-2.0 / MIT
- ✅ **致谢** — 上方含 DINOv3 致谢段落（§1.b.ii）
- ✅ **第三方署名** — [`LICENSES/LICENSE-BSD-3-Clause-torchvision.txt`](LICENSES/LICENSE-BSD-3-Clause-torchvision.txt)，覆盖学生初始化所用的 torchvision 权重
