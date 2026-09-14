# AI 标注模型目录

此目录只记录本地模型的挂载约定，模型权重本身不提交 Git。默认布局：

```text
annotation/models/
├── workflow/
│   ├── manifest.json
│   ├── clip/
│   ├── sources/
│   └── weights/
└── qwen/
    ├── model.gguf
    └── mmproj-F16.gguf
```

`workflow/manifest.json` 必须记录每个工具权重的 SHA-256；Worker 启动时会逐项校验。Qwen GGUF 与 SAM2 快照可分别通过 `ANNOTATION_QWEN_MODEL` 和 `ANNOTATION_SAM_MODEL` 指向仓库外的本机缓存。整个根目录也可用 `ANNOTATION_ASSET_ROOT` 覆盖。

不要将商业模型、私有数据集或 API 密钥复制到此目录后提交。
