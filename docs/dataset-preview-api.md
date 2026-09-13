# 数据集样本预览

- GET /api/dataset-versions/{version_id}/sample-previews?limit=6：limit 为 1–24，返回 sample_id、label、split、image_url。按类别轮流选取预览，不暴露归档内部路径。该接口是有限预览，不是完整样本分页。
- GET /api/dataset-versions/{version_id}/samples/{sample_id}/image：只能读取该版本清单内的样本，返回原始图片字节；不接受客户端指定文件路径。
- PostgreSQL 提供不可变清单和归档描述符；对象存储归档首次加载校验 SHA-256 与大小。不解压归档，拒绝路径穿越、符号链接、超限图片及非图片内容。
- 每个 Go 进程最多缓存一份归档（临时目录，替换时删除上一份）；读写互斥，避免重复下载和读取中删除。大归档首次读取可能较慢，受 HTTP 请求超时约束。浏览器图片缓存为 private / 1 小时。
- 数据版本或样本不存在返回 404；输入/图片格式不合法返回 422；存储、完整性校验或元数据不可用返回 503。
- 无数据库迁移。更新 go-control-plane 即可启用。历史版本若没有可用归档与清单，将明确报错，不回退访问旧本地绝对路径。
