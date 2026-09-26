# 第三方资源与许可证边界

本仓库由 Kaiwen Chen 原创的平台代码及配套原创文档采用根目录的 [MIT License](LICENSE)。已有独立许可证、版权声明或明确来源的第三方内容不因进入本仓库而改为 MIT；其原有声明必须保留。

本文是主要第三方资源的来源与授权边界索引，不是全部传递依赖的许可证审计或再分发许可保证。打包、部署、商用或再分发前，应针对实际使用的版本、权重 revision 和容器镜像核对上游条款。

## 1. 仓库登记的预训练权重

权重身份以 [weights/manifest.json](weights/manifest.json) 中的固定 revision、SHA-256、大小及 LFS 路径为准。本次增加 MIT 许可证不修改该清单或下列许可证。

| 资源 | 来源 | 现有许可证 | 仓库内许可证副本 |
|---|---|---|---|
| DINOv3 ViT-S/16 · LVD-1689M | [timm/vit_small_patch16_dinov3.lvd1689m](https://huggingface.co/timm/vit_small_patch16_dinov3.lvd1689m) | DINOv3 License，非 MIT | [DINOv3 LICENSE](weights/pretrained/dinov3/LICENSE.md) |
| ImageNet ViT-S/16 · AugReg IN21K → IN1K | [timm/vit_small_patch16_224.augreg_in21k_ft_in1k](https://huggingface.co/timm/vit_small_patch16_224.augreg_in21k_ft_in1k) | 清单登记为 Apache-2.0 | [Apache-2.0 LICENSE](weights/pretrained/imagenet/LICENSE) |
| ImageNet ResNet-50 · A1 IN1K | [timm/resnet50.a1_in1k](https://huggingface.co/timm/resnet50.a1_in1k) | 清单登记为 Apache-2.0 | [Apache-2.0 LICENSE](weights/pretrained/imagenet/LICENSE) |

DINOv3 材料及其衍生物须遵守随附 DINOv3 许可。训练、LoRA 合并、量化或转换为 PT、ONNX、TensorRT、Ascend 格式，不会自动消除原始模型的许可要求。模型权重的许可也不等于对其训练数据或底层图片授予权利。

## 2. 标注工作流的外部模型与工具

[annotation/models/README.md](annotation/models/README.md) 规定本地模型挂载方式；模型权重本身不提交到该目录。以下资源并非本项目原创代码，不能用根目录 MIT 代替其许可证：

| 资源 | 用途 | 使用与分发时应核对 |
|---|---|---|
| Qwen 视觉语言模型及 GGUF / mmproj 文件 | 主体定位 | 具体模型系列、规模、revision 的模型卡；GGUF 转换来源与原权重许可 |
| SAM2 | 主体分割 | 实际模型快照和所用运行库的许可证 |
| CLIP 及其编码器权重 | 图像向量、文本原型与检索 | 实际编码器及权重 revision，不以接口名称推定许可 |
| Retinexformer | 低光增强 | 实际源码与所选预训练权重分别附带的许可 |
| Restormer | 去噪、去模糊与散焦恢复 | 实际源码、任务权重及再分发条款 |
| SwinIR | 超分辨率 | 实际源码与所选权重的许可 |
| llama.cpp 等外部运行时 | 本地多模态执行 | 对应运行时版本及模型文件各自的许可 |

工具 manifest 中的 SHA 校验只证明文件身份与完整性，不证明已获得商业使用或再分发授权。外部模型未固定为单一版本时，本清单不对整个模型家族声明统一许可证。

## 3. 数据集、示例图片与截图

- ImageNet / ImageNet-100、CUB-200-2011、Stanford Cars、CIFAR-100、SVHN 等外部数据集及其原始图片遵循各自来源条款与权利人的要求，不受本项目 MIT 授权。
- 展示文档中的第三方样本缩略图、增强前后对照，以及业务截图内嵌的数据集图片，仍保留原始图片的权利边界；截图、裁剪或增强不会把它们变成本项目可重新授权的素材。
- [data/examples/toy-shapes-imagefolder](data/examples/toy-shapes-imagefolder) 是平台自带的合成几何图形示例，与外部真实数据集区分；本项目原创的生成代码及示例适用 MIT。
- 用户导入的图片、标注、私有数据和基于外部资源生成的训练产物，不因平台存储或处理而被自动授权给公众。

## 4. 软件依赖、容器与远程服务

平台依赖的 React、ECharts、Go/Eino、PyTorch/timm、ONNX Runtime 等库，以及 PostgreSQL、RabbitMQ、MinIO、Qdrant、Loki、Prometheus、Tempo 等服务，分别遵循其上游许可证。容器镜像还可能包含额外组件和系统包；本项目 MIT 不替代镜像及组件的许可义务。

依赖版本入口：

- 前端：[frontend/package.json](frontend/package.json)、[frontend/package-lock.json](frontend/package-lock.json)。
- Go：[go/go.mod](go/go.mod)、[go/go.sum](go/go.sum)。
- Python：[pyproject.toml](pyproject.toml)、[uv.lock](uv.lock)、[annotation/requirements.txt](annotation/requirements.txt)。
- 服务镜像：[docker-compose.yml](docker-compose.yml) 及实际启用的 overlay。

DeepSeek 等远程模型服务还受供应商服务协议、计费和数据处理条款约束；平台代码开源不授予服务额度、模型权重或供应商知识产权。

## 5. 再分发检查清单

1. 保留本项目 MIT 版权与许可声明，以及全部第三方 LICENSE / NOTICE 和已有文件头。
2. 按实际交付内容核对源码、权重、数据、截图和容器的许可，不能仅凭根目录 MIT 徽章判定整个发行包均为 MIT。
3. 第三方内容有独立署名、源码提供或其他义务时，按其原许可证履行；本清单不替代应随分发包附带的正式条款。
4. 对许可不明确、来源不清或不允许再分发的模型与数据，不随公开仓库或镜像发布；使用者应从获授权来源另行取得。
