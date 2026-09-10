# 数据集后台导入

默认 Go 服务的 `POST /api/datasets/upload-imagefolder` 在上传文件存入对象存储、任务写入 PostgreSQL 后返回 `202 {"job": ...}`。图片验证、目录扫描、manifest 存储和版本注册由后台消费者执行。原先的同步 `201` 响应已从 Go API 移除；前端仍兼容旧 Python 服务的 `201`。

流程：浏览器上传 → Go 流式写 ZIP → MinIO 持久化 → PostgreSQL 队列 → Go 后台消费者调用 Python 扫描 → 原子注册数据集版本 → 页面刷新列表。

## 容量和状态

- 每个 Go 实例最多同时接收 2 路上传；队列最多 20 个未完成任务，超限返回 `429`。
- 全局同时执行 1 个导入。每个 control-plane 进程有一个消费者，数据库事务锁负责跨实例的容量约束和领取。
- `queued → running → succeeded | failed`。`GET /api/dataset-imports` 返回全部活跃任务与最近完成任务，合计最多 40 条。
- 前端每 100 个文件构建一批 FormData，并让出主线程。**网络传输仍是一次 multipart 请求，并非分片上传或断点续传。** 界面显示真实上传百分比；后台处理显示阶段，不伪造百分比。
- 原有 10000 张、512 MiB 总图片大小、32 MiB 单图限制保留。multipart 的头部额度独立于图片字节上限；ZIP 使用 Store 模式，避免重复压缩 JPEG/PNG。
- 上传阶段最多等待 10 分钟；后台导入有独立的 20 分钟执行时限。

## 中断与恢复

上传期间的“停止上传”只中断浏览器请求。如果服务端已经提交任务，任务仍会继续，页面通过轮询展示真实结果。收到 `202` 后可以切换页面或刷新，文件无需保留在浏览器内存。

队列使用 2 分钟租约，每 30 秒续租。服务停止后任务保留在数据库，租约到期后可由新消费者领取。每次领取递增 attempt；旧消费者不能覆盖新 attempt 的结果。任务 ID 同时作为不可变版本 UUID，入库已提交但队列结果尚未提交的恢复不会重复创建版本。计算失败会显示失败原因；修正后可重新上传，已成功的版本需要新版本号。

## 部署

先执行新增 Alembic 迁移 `20260910_0014`，再启动更新后的 Go、Python compute 与前端。沿用当前部署的数据库和 MinIO 配置，无需新增 RabbitMQ 队列。

在待部署的 checkout 中：

```sh
docker compose build migrate go-control-plane python-inference-runtime frontend
docker compose run --rm migrate
docker compose up -d --no-deps go-control-plane python-inference-runtime frontend
```

本改动的验证使用独立测试数据库；不会自动迁移或重启当前运行环境。

## 验证

```sh
cd go
go test ./...
FINEVISION_TEST_GO_DATABASE_URL=postgres://... go test -race ./internal/adapters/postgres -run TestDatasetImport -v
cd ..
npm run smoke:dataset-import --prefix frontend
npm run build --prefix frontend
python -m pytest backend/tests/test_dataset_compute.py -q
```

数据库测试使用新迁移后的测试库，队列容量测试另建隔离 schema。浏览器测试 `frontend/scripts/smoke-dataset-import-ui.mjs` 需要 Playwright/Chromium 和启动后的前端；支持 `PLAYWRIGHT_MODULE`、`CHROMIUM_PATH`、`SMOKE_BASE_URL`。它用模拟 API 检查上传时页面可操作、刷新恢复、状态更新、成功后刷新列表、失败提示。`scripts/check-live-workflow.py` 支持真实 `202` 导入队列验收。
