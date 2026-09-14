# 多后端推理部署（首版）

## 范围与状态

同一模型发布版本下新增独立部署变体，不增加模型语义版本号。CPU ONNX 路径已接入；TensorRT / Ascend 包含构建、存储、路由和运行适配代码，但需要在目标硬件上完成验收后才能宣称支持该设备。没有将后台标注测评停止或迁移到这些 Worker。

| 后端 | 文件 | 输入 | 本期边界 |
| --- | --- | --- | --- |
| ONNX Runtime CPU | 已发布 `.onnx` | FP32 NCHW，FP32/FP16 模型计算 | 默认优先 FP32，显式选择 FP16 |
| NVIDIA TensorRT 10.x | `.plan` | FP32 NCHW，内部 FP32/混合 FP16 | 从完整 FP32 ONNX 编译；关闭 TF32；固定分辨率，配置 batch 上限 |
| 华为 Ascend ACL | `.om` | FP32 NCHW，按 OM I/O dtype 转换 | ATC 转换 + PyACL 执行；固定分辨率、batch=1；FP32 不支持的算子会构建失败 |

旧分类头 ONNX 继续走 CPU，不伪装成完整图片分类加速部署。模型冻结骨干、LoRA 合并、ImageNet 全参训练逻辑不变。没有加入 INT8、自动降级、跨卡调度或异构训练。

## 生命周期

1. 先发布完整模型 ONNX FP32，保留 PyTorch 检查点与发布编号。
2. 选择加速后端和精度，Go 原子写入 `model_deployments` 和 `deployment_outbox`。
3. `deployment-relay` 经 RabbitMQ 确认投递到 `fgvc.deployment.tensorrt` 或 `fgvc.deployment.ascend_acl`；与训练队列完全隔离。
4. 独立 builder 领取任务，在子进程编译。心跳每 20 秒续约；租约 2 分钟，构建最多 40 分钟，ATC 最多 30 分钟。
5. 比较 ONNX CPU 与编译模型的输出；校验通过后上传 MinIO，Go 再次校验 SHA-256 / 字节数并登记产物。
6. 状态由 `queued → building → ready / failed` 转换。只有 `ready` 且源模型仍为 `production` 才允许推理。

相同模型、源 ONNX、后端、精度、目标 profile、batch 上限唯一。重复创建不重复排队。投递为至少一次；token + worker owner + 活租约约束状态提交。完成回调失败会保留本地结果并重放，不重新编译。进程崩溃/租约失效后由运维检查日志，手动重试生成新 token；旧回调不能激活部署。畸形消息进入死信队列。

如果没有 Worker 在线，任务会保持等待构建（尚无自动排队超时）；需检查服务并启动消费者。健康检查未构成实时硬件目录，界面的“目标硬件”代表管理员已配置的目标，不代表实时在线。

## 数据与产物

迁移：`20260913_0018`，基于 `20260910_0017`。

- `model_deployments`：源产物、编译产物、精度、目标 profile、状态、错误、构建 token、租约、数值验证报告。
- `deployment_outbox`：与构建状态同事务写入，独立确认投递。
- `inference_events.deployment_id / runtime_metadata`：所选/实际后端、精度、实际 Worker、产物 SHA、含 RPC 的耗时。旧 CPU Worker 未报告后端时明确记为 `unreported`，不伪造测量结果。

MinIO 新部署路径：

```text
datasets/<dataset-id>/versions/<dataset-version-id>/models/<model-id>/
  deployments/<deployment-id>/<build-token>/<artifact-type>/<sha前两位>/<sha256>
```

历史 ONNX 无须移动。模型版本与数据集版本保持原有规则。TensorRT 引擎绑定架构、GPU 型号/SM、TensorRT 和 CUDA 版本；Ascend 绑定架构、SoC、ACL 版本。更换目标环境请使用新的 target profile 重新构建，不能直接拷贝 engine 使用。

CPU session 最多缓存 2 个，硬件 engine 最多 1 个；执行与缓存淘汰共享锁，避免释放运行中的上下文。缓存命中仍验证本地文件 SHA/大小。加速 Worker 拒绝不匹配的后端、目标 profile、环境指纹、batch、源模型与产物元数据；没有静默 CPU fallback。

## 接口与界面

- `GET /api/model-versions/{id}/deployments/`：通用 ONNX、加速部署、已配置目标。
- `POST` 同一路径：`{"runtime":"tensorrt","precision":"FP16","max_batch":1,"actor":"operator"}`。
- `POST /api/deployments/{id}/retry`：仅失败部署可重建。
- `POST /api/internal/deployments/{id}/{claim|heartbeat|complete}`：仅内部 builder，Bearer token 校验。
- 图片、上传、文件夹推理接口均支持 `deployment_id`。省略时为兼容旧客户端按稳定顺序优先 ONNX FP32，不任意选择最后一个文件。

模型详情有“推理部署”区域：精度/目标、构建状态、重试、SHA/存储位置、硬件指纹。推理页面只提供 ready 部署，并展示实际后端与耗时。网页初版创建 actor 为 `workbench-user`，不是身份认证；整个管理 API 仍应置于可信网络/认证网关内，不能直接暴露公网。

## 配置和启动

### 本地 CPU（无需新增账号）

继续使用现有 PostgreSQL / RabbitMQ / MinIO。新环境先构建锁定依赖的基础镜像，再构建推理覆盖镜像：

```bash
docker compose build python-training-worker
docker compose build go-control-plane migrate python-inference-runtime
docker compose run --rm migrate
docker compose up -d --no-deps go-control-plane python-inference-runtime deployment-relay
```

`Dockerfile.inference`、`Dockerfile.tensorrt` 基于 `finevision-python-compute:local`；部署流水线必须先构建该基础镜像。生产环境应以不可变 digest 固定基础镜像。

### NVIDIA（可选；先等测评结束或使用另一张卡）

需要 NVIDIA GPU、匹配 CUDA 的驱动、NVIDIA Container Toolkit，以及 GPU 可见的容器。TensorRT Dockerfile 固定 `tensorrt-cu12==10.9.0.34`；构建/推理镜像必须一致。

在本地不入 Git 的环境配置中设置：

```dotenv
FINEVISION_DEPLOYMENT_TOKEN=<随机强密码，Go 与 builder 相同>
FINEVISION_TENSORRT_GRPC=tensorrt-inference:9100
FINEVISION_TENSORRT_TARGET=rtx4060-sm89-trt10.9-cu12-v1
```

```bash
docker compose -f docker-compose.yml -f compose.inference.yml --profile tensorrt build tensorrt-inference
docker compose -f docker-compose.yml -f compose.inference.yml --profile tensorrt up -d --no-deps go-control-plane deployment-relay tensorrt-builder tensorrt-inference
```

`gpus: all` 是单卡开发示例；多卡部署需限制设备，避免与训练争抢显存。构建工作区上限 `FINEVISION_TRT_WORKSPACE_MB` 默认 512 MiB，但并非进程总显存上限。首版 UI batch=1；API 允许 TensorRT 上限 1–32，目前文件夹接口仍逐图执行，不承诺批处理吞吐提升。

### 华为昇腾（可选；需服务器信息）

需要提供实际 NPU 型号（例如 310P / 910B）、CPU 架构、驱动/固件版本和对应 CANN 开发镜像。`ASCEND_BASE_IMAGE` 必须包含可运行的 ATC、PyACL、Python >=3.11，镜像须提前配置 PATH / PYTHONPATH / LD_LIBRARY_PATH。不要使用任意 CANN 镜像替代匹配环境。

配置 `ASCEND_BASE_IMAGE`、`FINEVISION_ASCEND_GRPC=ascend-inference:9100`、`FINEVISION_ASCEND_TARGET=<实际SoC-CANN版本-v1>` 和共享 token，按 NVIDIA 命令将 profile/service 换成 ascend。示例设备映射为 `/dev/davinci0` 及管理设备，实际服务器需校对；不要为了方便直接开启 privileged。生产环境还需按现场镜像配置非 root 用户的设备权限。

远程 Worker 的 gRPC、对象存储、回调地址须互通。当前内网 gRPC 没有 TLS，不要映射到公网。Compose 示例不是多机调度系统；本期每个后端配置一个目标 profile，多种 GPU/SoC 的队列分区是后续扩展。

## 验收

2026-09-13 本地结果：Go 全套单测通过；隔离 PostgreSQL 上迁移与部署生命周期测试通过；Python 定向测试（含原导出回归）16 项通过，4 项真实硬件测试按设计跳过；前端构建、推理客户端、真实浏览器桌面/移动布局检查通过。已更新本地数据库到 `20260913_0018`，重启 Go 控制面 / CPU 推理服务，并启动独立 deployment relay。对现有已发布完整模型做了一次真实图片上传推理，成功返回 `onnx_cpu / FP32` 与 Worker 身份；该图片是连通性测试输入，不用于报告分类精度。真实 TensorRT / 昇腾编译、设备精度与性能仍待验收。

普通测试不分配 GPU/NPU：

```bash
cd go && go test ./...
# 仓库根目录
.venv/bin/python -m pytest backend/tests/test_deployments.py backend/tests/test_inference_runtime.py backend/tests/test_model_export.py backend/tests/test_deployment_hardware.py -q
cd frontend && npm run build && npm run smoke:inference-client
```

数据库集成测试仅接受名为 `deployment_test_*` 的隔离库，需迁移到 head，并放入已发布完整 FP32 ONNX 的模型记录：

```bash
FINEVISION_DEPLOYMENT_TEST_DATABASE_URL=<隔离库连接> go test ./internal/adapters/postgres -run TestDeploymentDatabaseLifecycle -v
```

硬件数值冒烟（在实际设备环境执行；普通测试中显式 skip）：

```bash
FINEVISION_TEST_HARDWARE=tensorrt python -m pytest backend/tests/test_deployment_hardware.py -q
FINEVISION_TEST_HARDWARE=ascend_acl python -m pytest backend/tests/test_deployment_hardware.py -q
```

构建门禁使用全零和固定随机输入，对多个 batch 验证 logits 形状、有限值及误差；FP32 rtol=1e-3 / atol=1e-4，FP16 rtol=1e-2 / atol=1e-2。同时记录探针 top-1 一致率。这是**数值冒烟，不是数据集准确率验收，也不是性能基准**。

正式启用硬件前还必须在同一真实验证集比较 ONNX / 加速后端的 top-1、top-10、Macro F1、置信度/拒识变化、冷启动与热推理 p50/p95、显存，以及重启后加载、SHA损坏、OOM失败和设备离线行为。FP16 为混合精度，不保证每层都 FP16；不可仅凭模型能编译就宣称精度或吞吐已达标。

实现依据：[NVIDIA TensorRT Python API](https://docs.nvidia.com/deeplearning/tensorrt/latest/inference-library/python-api-docs.html)、[TensorRT 10.x API 迁移](https://docs.nvidia.com/deeplearning/tensorrt/10.x.x/api/tensorrt-8x-to-10x-python-api.html)、[华为 ATC 模型转换](https://www.hiascend.com/document/detail/en/canncommercial/850/devaids/atctool/atlasatc_16_0003.html)。本期选择原生 TensorRT engine，而非 ORT TensorRT EP，以便对独立部署产物进行登记与硬件绑定。
