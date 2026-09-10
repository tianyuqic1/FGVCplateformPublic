# 数据集版本管理：训练集扩充

本次按已确认的范围实现：导入填写名称，标识由系统生成；同一数据集下版本号递增；每个版本独立展示权重；人工上传和已复核样本只扩充训练集。

## 数据关系与发布规则

- `datasets.id/dataset_key`：新数据集使用系统生成的 UUID；`name` 保存用户填写的名称。历史标识保持兼容。
- `dataset_versions.id/version_key`：新版本使用全局唯一 UUID；`version_number` 在数据集内按 v1、v2…递增。
- `parent_version_id`：本版本采用的基础快照。第一阶段维护单一主线，发布时基础版本必须仍是该数据集的最新版本。
- `source_type`：首次导入、人工扩充、复核回流或混合扩充。
- `change_summary`：新增数、重复数、训练集范围、评测集保持标记及本批样本来源。大体积样本来源保存在清单中，不下发到列表。
- `import_request_id/import_fingerprint`：同一请求重试返回原版本；复用请求标识但修改内容会冲突。
- `dataset_version_feedback`：记录反馈被哪个版本纳入，禁止同一数据集重复消费同一反馈。

历史版本按原创建时间和 ID 稳定编号。迁移不推断旧版本间的衍生关系，不修改已有模型、权重或历史数据内容。

发布会在数据集行锁内检查基础版本、分配下一版本号并登记完整快照、产物和反馈消费记录。失败不产生可见版本。扩充后的归档使用显式 train/val/test 目录，基础清单决定原样本的标签和划分；计算服务和训练 worker 再扫描也不会重新随机划分旧数据。

基于 v1 权重推理产生的复核数据可以合入当前 v3，生成 v4：基础版本是 v3，来源记录仍是 v1/原模型/原复核记录。数据发布不启动训练，不替换当前模型。

## 页面和接口

- 首次导入：`POST /api/datasets/upload-imagefolder`，multipart：`name`、`request_id`、`files`。客户端不再填写数据集标识和版本。
- 待纳入候选：`GET /api/datasets/{dataset_id}/training-candidates`。
- 批次发布：`POST /api/datasets/{dataset_id}/versions`，multipart：`base_version_id`、`request_id`、可选 `files` 和 JSON 字符串 `feedback_ids`。
- 数据集列表与详情返回 `versions`，含版本号、基础版本、来源、划分数量、训练状态、`has_weights`、`model_count` 及模型链接。

列表按数据集折叠，展开后逐版本查看。权重状态根据该版本的模型和已验证权重产物计算；仅有训练任务不等于已有权重。数据集详情提供批次选择和发布入口，训练与推理选择器保留历史版本。详情页的推理入口选择已有可用权重的数据版本。

手动上传在页面中暂存到本批选择；复核候选本身已持久化。当前没有跨页面保存手动上传草稿，也没有分支、删除样本、修改旧标签或扩充评测集的入口。文件夹可以是 `类别/图片` 或 `train/类别/图片`，含 `val/test` 的扩充请求整体拒绝。

按文件内容 SHA-256 去重，包含与评测图片的精确重复；同图不同标签拒绝发布。所选数据全部重复时不创建空版本。沿用现有完整快照上限：10000 张、512 MiB，单图 32 MiB。原始图片字节去重不等于近似图片识别；新增类别仍受现有 readiness 检查，并不会自动为它补充验证或测试样本。

## 运行与兼容

先执行迁移 `20260910_0014`，再部署新 Go API 和前端。上传扫描需要包含 DatasetCompute.Scan 的 Python runtime；当前代码已注册此 RPC，旧镜像需要重建。

复核回流优先通过 `input_ref` 匹配已验证的对象存储图片产物。旧本地上传图片需要为 Go API 配置 `FINEVISION_UPLOAD_DIR` 并只读挂载相同目录；只允许读取该目录内的原图。无法读取原图会拒绝整批发布，不生成不完整版本。此功能消费已有人工复核记录，未重构旧推理/复核服务。

## 验证

- `go test ./...` 通过。
- 独立 PostgreSQL 测试库执行全量迁移成功，Go PostgreSQL 集成测试通过，覆盖自动编号、重复请求、并发冲突、旧权重归属、旧模型反馈合入最新版本、反馈只消费一次。
- 真实 Go HTTP → 对象存储 → 当前 Python DatasetCompute 链路通过：生成图片首次导入、v1 → v2、幂等重试、过期基础版本、拒绝评测图片、划分数量保持不变。
- Go 合并测试按图片实际字节检查验证集/测试集保持、重复排除、标签冲突和上传目录边界。
- 前端构建、客户端 smoke 和浏览器页面检查通过：折叠列表逐版本显示权重，历史模型入口保留。
- Python DatasetCompute 测试 7 项通过；旧数据库测试 15 项通过、2 项失败。两项失败已用修改前 HEAD 代码复现：`test_database_backed_training_run_executes_toolkit_flow`、`test_dinov3_training_request_uses_canonical_backbone_metadata`。

复验 HTTP 链路：`uv run python scripts/check-dataset-versions.py --url <独立测试 API 地址>`。该脚本使用生成图片并创建标记为“版本管理验收”的数据集。

本次修改尚未部署到正在运行的服务；数据库验证使用独立测试库。
