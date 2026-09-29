# pretrained — 对外发布包

本目录是**对外分发用的自包含发布包**，用于上传到 ModelScope（魔搭）与 HuggingFace Hub，以及企业内网离线分发。

## 与 `weights/` 的分工（重要，不要混用）

仓库里有两个名字相近的目录，职责完全不同：

| 目录 | 用途 | 是否纳入 Git | 谁读它 |
|---|---|---|---|
| `weights/pretrained/` | 平台**运行时**读取的上游预训练权重，供 `pretrained-weight-init` 校验并推入 ArtifactStore | ✅ 是（Git LFS 管理） | `docker-compose.yml`、`go/internal/modelcatalog`、`backend/src/finevision/compute/pretrained_weights.py` |
| **`pretrained/`（本目录）** | **只用于对外发布**的打包目录，含权重、许可证与 model card | ⚠️ 仅文本文件纳入 Git，**权重被 `.gitignore` 排除** | 无。平台运行时不读这里 |

**平台运行时不读取本目录**，修改这里不会影响任何服务。

平台可按模型卡的固定 SHA-256 从 ModelScope 获取已发布的蒸馏骨干并缓存在计算节点；此路径不从本目录读取二进制，也不把蒸馏训练产物加入 Git LFS。

### 为什么本目录的权重不进 Git

`.gitattributes` 里的 LFS 规则只覆盖 `weights/pretrained/**` 两个精确路径，**不覆盖本目录**。若本目录的权重被 `git add`，会以普通 Git blob 形式**永久写入历史**，且无法在不重写历史的前提下清除。因此 `.gitignore` 已硬性排除 `pretrained/**` 下的全部二进制权重格式。

上游三个权重通过 APFS clone（`cp -c`）从 `weights/pretrained/` 拷入，是独立副本，但实测**不额外占用磁盘**（`df` 前后差值为 0；`du` 会报 264M 是因为它按分配区间计数，即使块为共享）。

## 目录结构

```
pretrained/
├── README.md                       # 本文件：来源、校验值、许可映射、发布检查清单
├── LICENSES/                       # 全目录共用的许可证原文
│   ├── LICENSE-DINOv3.md
│   ├── LICENSE-Apache-2.0.txt
│   └── LICENSE-BSD-3-Clause-torchvision.txt
├── dinov3/
│   ├── vit_small_patch16_dinov3.lvd1689m.safetensors    ← 平台运行时用
│   ├── vit_base_patch16_dinov3.lvd1689m.safetensors     ← 蒸馏教师，仅本包
│   └── vit_large_patch16_dinov3.lvd1689m.safetensors    ← 仅本包
├── imagenet/
│   ├── vit_small_patch16_224.augreg_in21k_ft_in1k.safetensors
│   └── resnet50.a1_in1k.safetensors
└── inat2021-mini-mobilenetv3-large/          # ← 可独立发布的蒸馏产物包
    ├── README.md                              # model card（平台只认这个名字）
    ├── LICENSES/                              # 包内自带，满足 §1.b.i
    ├── inat2021-mini-mobilenetv3-large-backbone.safetensors
    ├── inat2021-mini-mobilenetv3-large-classifier.safetensors
    ├── class_map.json
    ├── load_model.py
    ├── manifest.json
    ├── results.json
    └── 蒸馏效果.md
```

`inat2021-mini-mobilenetv3-large/` 是**自包含**的：许可证已复制进它的 `LICENSES/`，整目录可直接作为 ModelScope / HuggingFace 仓库上传，无需依赖外层文件。

## 一、上游预训练权重

### 1.1 与平台运行时同步

这三个同时被平台运行时使用（见 `weights/manifest.json`）。校验值必须与 manifest 保持一致；不一致时以 manifest 为准，并修正本目录副本。

| 文件 | 字节 | 上游来源 | 固定 revision | 许可 |
|---|---:|---|---|---|
| `dinov3/vit_small_patch16_dinov3.lvd1689m.safetensors` | 86,362,376 | [timm/vit_small_patch16_dinov3.lvd1689m](https://huggingface.co/timm/vit_small_patch16_dinov3.lvd1689m) | `3bf4720a82ec2066db88137180ff1f83a675cef0` | DINOv3 License |
| `imagenet/vit_small_patch16_224.augreg_in21k_ft_in1k.safetensors` | 88,216,496 | [timm/vit_small_patch16_224.augreg_in21k_ft_in1k](https://huggingface.co/timm/vit_small_patch16_224.augreg_in21k_ft_in1k) | `7e2c55630205e1266030f18370f4c6ed1a514b52` | Apache-2.0 |
| `imagenet/resnet50.a1_in1k.safetensors` | 102,469,840 | [timm/resnet50.a1_in1k](https://huggingface.co/timm/resnet50.a1_in1k) | `767268603ca0cb0bfe326fa87277f19c419566ef` | Apache-2.0 |

### 1.2 DINOv3 家族扩展（仅在本发布包）

为完整覆盖 DINOv3 ViT-S/B/L 家族而加入。**未纳入平台 `weights/manifest.json`**，平台运行时不读取 —— 新增到平台是另一件事，见文末说明。

| 文件 | 字节 | 上游来源 | 固定 revision | 许可 |
|---|---:|---|---|---|
| `dinov3/vit_base_patch16_dinov3.lvd1689m.safetensors` | 342,579,728 | [timm/vit_base_patch16_dinov3.lvd1689m](https://huggingface.co/timm/vit_base_patch16_dinov3.lvd1689m) | `c6a5fb7d12bbd3cf3b0079253141c3332aaed7da` | DINOv3 License |
| `dinov3/vit_large_patch16_dinov3.lvd1689m.safetensors` | 1,212,347,640 | [timm/vit_large_patch16_dinov3.lvd1689m](https://huggingface.co/timm/vit_large_patch16_dinov3.lvd1689m) | `30c1109559f65dea34316b0d4842d35c5771fe11` | DINOv3 License |

> ViT-B/16 即蒸馏产物的**教师模型**（`inat2021-mini-mobilenetv3-large`）。它的权重属于 DINOv3 材料，再分发同样须随附协议副本。

### 全部 SHA-256

```
2a1ec16ae28ffa07bc0ead0241ee7df9fc26451fe6f9f839b7b3afa0a906b040  dinov3/vit_small_patch16_dinov3.lvd1689m.safetensors
1f9ed8a2378d65e24bb710ba522ac9fa7be4e036d7aefb4384ce022833926332  dinov3/vit_base_patch16_dinov3.lvd1689m.safetensors
45172f209c9583c40538afc26b60a07033e6fcc2e8c30228338e6b2e932e7941  dinov3/vit_large_patch16_dinov3.lvd1689m.safetensors
79c03c635cdfd798a364a9d8c4e5c0b7255b975ea2c9616046d4f77ab01435aa  imagenet/vit_small_patch16_224.augreg_in21k_ft_in1k.safetensors
773525d5821de224f8f30c33377b7a795d7863e08522698200d3217d3f2a41bb  imagenet/resnet50.a1_in1k.safetensors
```

校验（Linux 把 `shasum -a 256 -c` 换成 `sha256sum -c`）：

```bash
cd pretrained && shasum -a 256 -c <<'EOF'
2a1ec16ae28ffa07bc0ead0241ee7df9fc26451fe6f9f839b7b3afa0a906b040  dinov3/vit_small_patch16_dinov3.lvd1689m.safetensors
1f9ed8a2378d65e24bb710ba522ac9fa7be4e036d7aefb4384ce022833926332  dinov3/vit_base_patch16_dinov3.lvd1689m.safetensors
45172f209c9583c40538afc26b60a07033e6fcc2e8c30228338e6b2e932e7941  dinov3/vit_large_patch16_dinov3.lvd1689m.safetensors
79c03c635cdfd798a364a9d8c4e5c0b7255b975ea2c9616046d4f77ab01435aa  imagenet/vit_small_patch16_224.augreg_in21k_ft_in1k.safetensors
773525d5821de224f8f30c33377b7a795d7863e08522698200d3217d3f2a41bb  imagenet/resnet50.a1_in1k.safetensors
EOF
```

## 二、蒸馏产物

`inat2021-mini-mobilenetv3-large/` —— DINOv3 ViT-B/16 教师 → MobileNetV3-Large 学生，iNaturalist 2021 mini（500,000 图 / 10,000 类）。完整说明见该目录的 `README.md`。

| 文件 | 字节 | 参数量 | SHA-256 |
|---|---:|---:|---|
| `...-backbone.safetensors` | 12,015,440 | 2,996,398 | `ae3990c5d4655d71fd988f0246dfe5e372ed88e5b9ac78cf18b681d56a7986b9` |
| `...-classifier.safetensors` | 45,733,424 | 11,425,982 | `e83caa8274f80e4aded84753204469a28bfdd2451c50ce0523852d2c940f3bc1` |
| `class_map.json` | 970,777 | — | `499df6d97f2a72d2c9b713da13e30bd295462719c231dcd54e36445ab5844c40` |
| `load_model.py` | 1,855 | — | `37790af5981bae1cd9002b39ee7fbf1be739284645079e4cc0788063f0f12e3e` |

以上四个 SHA-256 已与包内 `manifest.json`、`results.json` 的声明值逐一核对一致。

**关键指标**：iNat 官方 val top-1，仅标签 51.78% → 蒸馏 **58.40%**（+6.62 pp）；教师线性探针 71.38%。

> ⚠️ **域外性能下降**：Stanford Cars 上蒸馏权重 12.13%，低于原始 ImageNet 权重的 23.02%。生物域特化的预期代价，非生物域任务请勿直接采用。

## 许可映射

### DINOv3 系（`LICENSES/LICENSE-DINOv3.md`）

覆盖 `dinov3/vit_small_patch16_dinov3.lvd1689m.safetensors`，以及**由 DINOv3 蒸馏得到的全部衍生权重**（含 `inat2021-mini-mobilenetv3-large/`）。

该许可**明确允许再分发**（§1.a 授予 `use, reproduce, distribute, copy, create derivative works` 权利），但附有强制条件：

- **§1.b.i** — 再分发 DINOv3 材料**及其衍生作品**时，必须以本协议条款分发，并**随附一份协议副本**。
- **§1.b.ii** — 基于 DINOv3 开展的研究成果**发表时必须致谢** DINOv3 的使用。
- §1.b.iii / iv / v — 合规、禁止逆向工程、贸易管制。

> ⚠️ **架构许可 ≠ 权重许可。** 蒸馏权重的**学生架构**是 MobileNetV3（Apache-2.0），但权重本身是 DINOv3 的衍生物，适用 **DINOv3 License**。本仓库 `THIRD_PARTY_NOTICES.md` 第 17 行已载明同一原则：训练、LoRA 合并、量化或格式转换不会消除原始模型的许可要求。

### Apache-2.0 系（`LICENSES/LICENSE-Apache-2.0.txt`）

覆盖两个来自 timm 的 ImageNet 权重。再分发时需保留版权声明、许可原文，并标明是否修改。

### BSD-3-Clause（`LICENSES/LICENSE-BSD-3-Clause-torchvision.txt`）

覆盖蒸馏学生的**初始化权重**来源：torchvision `mobilenet_v3_large IMAGENET1K_V2`。BSD-3-Clause 要求再分发时保留版权声明与免责声明，故一并附上。相对 DINOv3 License 而言它更宽松，最终产物的约束以 DINOv3 License 为准。

### 数据集条款（需自行核对）

蒸馏权重在 **iNaturalist 2021 mini** 上训练。数据集条款不会自动转为权重许可，但 iNaturalist 图像携带多种 CC 许可、iNat2021 亦有自身使用条款。**商用前请自行核对** —— 与 `THIRD_PARTY_NOTICES.md` 第 33 行立场一致：SHA 校验只证明文件身份与完整性，不构成授权证明。

## 发布状态

`inat2021-mini-mobilenetv3-large` 的发布情况：

| 平台 | 状态 |
|---|---|
| **ModelScope** | ✅ 已发布 — [Tianyuqi/inat2021-mini-mobilenetv3-large](https://modelscope.cn/models/Tianyuqi/inat2021-mini-mobilenetv3-large) |
| HuggingFace | ⬜ 暂未发布 |

合规项均已落实（发布时逐条核对）：

- ✅ **随附协议副本** — 包内 `LICENSES/LICENSE-DINOv3.md` 与根目录 `LICENSE`（§1.b.i 强制项，缺此条即违规）
- ✅ **声明正确许可** — 元数据标为 `license: other` + `license_name: dinov3-license`，未误标为 `apache-2.0`
- ✅ **model card 就位** — 包内为 `README.md`
- ✅ **SHA-256** — 已逐一核对并记录在上方表格

> 若日后增补 HuggingFace，需在两侧互相标注发布地址，避免被误认为无关模型。

## 为什么 ViT-B / ViT-L 不接入平台运行时权重

ViT-B 与 ViT-L **只存在于本发布包**，平台运行时读不到。这是**有意的取舍，不是待办事项**：蒸馏实际只用到 ViT-B，而把 ViT-L（1.13 GiB）放进 Git LFS 会把可克隆次数显著压低（见下）。若将来确需在平台内跑蒸馏，按下面 5 处改即可。

### 接入步骤（5 处，其中 2 与 3 必须同步）

1. `weights/pretrained/dinov3/` — 放入文件；现有 `.gitattributes` 规则 `weights/pretrained/dinov3/*.safetensors` 会自动覆盖，无需改
2. `weights/manifest.json` — 新增条目（`preset`、`sha256`、`size_bytes`、`lfs_path`）
3. `backend/src/finevision/compute/pretrained_weights.py` — `MANAGED_WEIGHTS` 增项 + `WEIGHT_ALIASES`，并新增环境变量键
4. `go/internal/modelcatalog/catalog.go` — 目录条目
5. `docker-compose.yml` — `pretrained-weight-init` 的 `promote` 函数增加对应行

> ⚠️ `backend/tests/test_pretrained_weights_manifest.py:16` 断言 `manifest["weights"]` 的 preset 列表等于 `list(MANAGED_WEIGHTS)`，所以 **2 与 3 必须同步修改**，否则测试失败。

### ⚠️ 接入前请先算清 LFS 带宽

平台运行时权重走 Git LFS，而 GitHub Free 的 LFS 带宽是 **10 GiB/月**，按**仓库 owner** 计（不是克隆者，任何人克隆都扣你的额度）。

| 场景 | 单次完整克隆 | 10 GiB/月可支撑 |
|---|---:|---:|
| 现状（3 个权重） | 264.2 MiB | 约 **38** 次 |
| 加入 ViT-B | 590.9 MiB | 约 **17** 次 |
| 加入 ViT-B + ViT-L | **1,747.1 MiB** | 约 **6** 次 |

超额后 **LFS 支持会在该账号上被停用到下月**，影响你名下所有仓库。因此若要接入：

- 只接 ViT-B（蒸馏实际用到），ViT-L 留在发布包里不进 LFS；
- 或按前文「多源拉取」思路，让运行时从模型平台而非 LFS 取权重。



## 相关文档

- [第三方资源与许可证边界](../THIRD_PARTY_NOTICES.md)
- [权重身份清单](../weights/manifest.json)
- [平台运行时权重说明](../weights/README.md)
- [蒸馏产物 model card](inat2021-mini-mobilenetv3-large/README.md)
