# Go API 链路补齐与验收（2026-09-13）

## 原因

前端已切换到 Go Control Plane，但反馈池、人工复核、弃权策略、推理和训练记录删除仍调用仅存在于旧 Python HTTP 服务的接口，因此返回 404。历史复核图片所在上传卷也没有挂载到新服务。

## 已接入的接口

| 模块 | 接口 |
| --- | --- |
| 人工复核 | GET `/api/review-items`、GET `/api/review-items/{id}`、POST `/{id}/submit`、POST `/{id}/assist` |
| 反馈池 | GET `/api/feedback-items` |
| 弃权策略 | GET `/api/abstention-policies`、POST `/propose`、POST `/{id}/activate`、POST `/{id}/deactivate`、GET `/{id}/shadow-decisions` |
| 推理 | POST `/api/inference`、POST `/api/inference/upload`、POST `/api/inference/upload-folder` |
| 推理图片 | GET `/api/uploads/{image_id}` |
| 训练记录 | DELETE `/api/training-runs/{id}` |

这些扩展接口注册在 `go/internal/httpapi` 的手写路由中；并非旧 Python HTTP API 的代理。CRUD、事务、文件上传和状态校验由 Go 完成，ONNX 计算通过 gRPC 交给独立 Python 推理服务。

## 数据与安全约束

- 复核/反馈列表支持数据集筛选、limit/offset 分页及稳定排序；无记录返回空数组和真实总数，服务错误不伪装为空列表。
- 复核提交在行锁事务内写反馈并更新状态；重复提交返回 409。训练候选标签必须属于对应数据集版本的类别。
- 只有 production 且存在匹配源 bundle 的已发布 ONNX 才能推理，模型与数据集版本必须一致。旧模型仅有 production 状态但没有 ONNX 时需要重新发布，不回退到未发布检查点。
- 完整模型用完整 ONNX；旧分类头 ONNX 保留骨干特征提取及欧氏距离近邻计算。FP32/FP16 沿用发布时的预处理契约，下载/读取校验 SHA-256 和文件大小。
- 新推理图片存入 MinIO，复核保存制品 URI、前端使用图片 API；这样训练候选可以沿用已校验的制品加入数据集扩充流程。
- 历史上传卷只读挂载在 `/data/uploads`，仅对数据库中存在且文件名合法的旧图片提供访问。不能读取任意服务器路径，前端移除了此类输入框。
- 单图最多 20 MiB、4000 万像素；批量最多 100 张、请求总量 128 MiB。批量逐图独立记录运行与复核，返回成功、失败及复核数量；坏图不会让其他图片的结果丢失。
- 策略按模型/数据集版本隔离，人工启用需要原因、至少 5 条反馈且风险达标；同一范围只允许一个 active 策略。shadow 策略不修改真实决策，记录回放与新推理差异。
- 训练队列删除仅允许无产物的 failed/cancelled 记录。排队、暂停任务需先取消，运行中或已有模型产物的任务不能删除。不会删除权重、数据集或 MinIO 文件。

## 配置

- 现有 PostgreSQL、MinIO、LLM gateway 配置继续使用，不新增密钥。
- `FINEVISION_INFERENCE_GRPC` 默认 `python-inference-runtime:9100`。
- Compose 为 Go 服务设置 `FINEVISION_UPLOAD_DIR=/data/uploads` 并只读挂载既有 `finevision-uploads` 卷。
- 本次不修改数据库结构，无新增迁移。依赖现有复核、反馈、策略、推理运行表迁移已执行。

## 验收方式

- Go：`cd go && go test ./...`。
- 数据库集成：在独立、已迁移且含模型样例的 `api_repair_test_*` 数据库上设置 `FINEVISION_TEST_GO_DATABASE_URL`，运行 `go test ./internal/adapters/postgres -run TestReviewFeedbackAndPolicyDatabaseFlow -v`。覆盖无效标签、并发提交、分页、策略提议/启停、无产物任务删除。
- Python：`.venv/bin/python -m pytest backend/tests/test_inference_runtime.py backend/tests/test_model_export.py -q`。覆盖真实小型 FP32/FP16 ONNX、阈值覆盖、源 bundle 不匹配及制品完整性。
- 真实 HTTP 验收：独立 Control Plane 使用测试数据库并监听 `18001`，设置 `FINEVISION_TEST_API_URL=http://localhost:18001`，运行 `backend/tests/test_go_feedback_acceptance.py`。使用已发布的玩具数据集模型，不启动训练；覆盖上传、样本 ID、批量部分失败、图片读取、复核提交、反馈及发布门禁。
- 前端：`npm run build`；浏览器检查反馈池、复核列表与详情。真实 LLM 付费调用不属于这次验收。

不要将带写入的验收测试指向日常开发数据库。独立标注测评进程不属于平台重启范围。
