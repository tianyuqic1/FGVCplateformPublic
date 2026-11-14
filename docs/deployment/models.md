# 预训练权重

`weights/manifest.json` 是部署权重清单。`weights` 保留 Git LFS 托管身份；`external_weights` 登记外部发布权重。每项固定 alias、preset、来源/版本、大小、SHA-256 和许可证。

```bash
./deploy.sh models list
./deploy.sh models pull --models dinov3-vits,inat-mobilenetv3
./deploy.sh models check --models dinov3-vits,inat-mobilenetv3
./deploy.sh --with-models --models dinov3-vits,inat-mobilenetv3
```

前三条仅列举、准备或校验，不启动平台和 Worker。最后一条准备模型并启用本机计算。当前默认清单为 DINOv3 ViT-S、ImageNet ViT-S、ImageNet ResNet-50、iNat 蒸馏 MobileNetV3；不是下载所有 Hugging Face 模型或整个 pretrained 发布目录。

Git LFS 原文件已存在且校验通过时直接复用；否则从清单固定 revision 的上游 HTTPS 地址下载。外部 iNat 权重使用已登记的发布 URL 和固定哈希，内容变化会校验失败。普通部署不要求 Git LFS CLI；继续使用 git lfs pull 也可以，但应先 git lfs install --local。

缓存位于 `.deploy/models/sha256/<完整哈希>`。下载写临时文件，完整校验后原子替换正式缓存；失败清理临时文件，重试可复用已经完成的其他模型。当前不提供跨中断断点续传。损坏缓存不会被视为成功，也不会被 Worker 使用；无效下载不会覆盖既有文件。

启用 with-models 后，所选模型只读挂载到计算服务。LFS 托管模型还通过严格的一次性初始化登记至 MinIO，并重新校验对象数据；外部模型通过明确的只读路径提供，避免运行时重复下载。下载或登记失败都会让部署非零退出。未启用模型准备时，缺失骨干的实际任务仍会明确失败。

## 离线准备

在联网机器执行 models pull，把 `.deploy/models/sha256/` 中的已校验模型复制到目标机器同目录。运行 models check 确认后，再以 `--with-models --no-build` 启动。还必须提前准备所需 Docker 镜像；模型缓存齐全不代表镜像也已离线可用。

该缓存会以只读方式提供给非 root 计算容器，模型目录/文件可读；配置、私有 state 和密钥文件不在模型挂载内。不要把凭据或数据集放入模型缓存目录。

新增骨干需同时完成平台/Worker 支持及清单登记，不能只添加下载地址。当前部署清单不覆盖本地未提交或实验性的其他骨干。SAM2、CLIP、标注模型及硬件 engine 使用对应专题说明。上游许可证仍适用，下载或训练不会改变许可边界。
