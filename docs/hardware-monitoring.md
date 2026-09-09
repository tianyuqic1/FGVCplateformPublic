# 硬件监控

独立页面 `/hardware`，侧栏与全局搜索均可进入。首版提供 Linux 宿主机 CPU、内存、NVIDIA GPU、显存、温度、功耗、数据/缓存/对象存储磁盘容量、24 小时历史，以及正在运行的训练任务的节点关联。

## 部署

随 Compose 启动的 `hardware-collector` 使用独立轻量镜像，不导入训练依赖；只读挂载宿主机 `/proc` 和目标数据卷，没有 Docker socket、特权模式或进程控制接口。共享文件系统按 `st_dev` 去重，列出所有用途标签。数据卷存在但采集用户无法访问时显示采集失败，而不是零容量。

升级步骤（在目标部署的项目目录执行）：

```bash
# .env 中为每台物理/虚拟计算主机设置稳定且唯一的 ID。
# 同一主机的所有训练 worker 与采集器必须使用相同 ID。
# FINEVISION_NODE_ID=compute-01
# FINEVISION_NODE_NAME=训练服务器-01
# FINEVISION_HARDWARE_TOKEN=<为部署配置的独立随机令牌>

docker compose build go-control-plane python-training-worker hardware-collector migrate frontend
docker compose run --rm migrate
docker compose up -d go-control-plane python-training-worker hardware-collector frontend
```

GPU 部署在上述每条 Compose 命令中均使用 `-f docker-compose.yml -f docker-compose.gpu.yml`。GPU override 为采集器启用全部 GPU 与 `utility` 驱动能力。沿用项目已有的 NVIDIA Container Toolkit 前提。采集器与训练 worker 必须重建/重启才会使用新代码；新认领的任务才带节点绑定。运行中的旧 worker 任务不会被猜测分配到某台主机。

Compose 的默认节点 ID 为 `local-compute`，开发默认 token 仅供现有本地开发模式使用。远程节点使用唯一节点 ID 和相同控制面采集 token；经受控网络或 HTTPS 上报。只需每台主机一个采集器，训练与推理共用机器时不要重复部署采集器。页面读取沿用项目现有控制面访问边界，采集写接口另行验证专用 token；不将 token 暴露给前端。

不使用容器时，可直接在 Linux 宿主机运行：

```bash
export FINEVISION_NODE_ID=compute-01
export FINEVISION_HARDWARE_ENDPOINT=http://localhost:8001/api/internal/hardware/samples
export FINEVISION_HARDWARE_TOKEN=<与控制面一致的令牌>
export FINEVISION_HARDWARE_DISKS='{"数据集":"/srv/datasets","模型缓存":"/srv/cache"}'
PYTHONPATH=backend/src python3 -m finevision.compute.hardware_collector
```

`FINEVISION_HOST_PROC` 默认为 `/proc`；Compose 设置为 `/host/proc`。不要将宿主机口径采集器直接放入无法读取宿主机 proc 的隔离容器而仍称为整机指标。`FINEVISION_HARDWARE_DISKS` 指定实际需要关注的挂载路径，默认宿主机运行时为根盘；Compose 显式指定数据卷。

## 数据与交互规则

- 采集/上报默认 5 秒一次。页面前台 5 秒、后台 30 秒轮询，支持手动刷新与暂停。离开页面取消请求；节点和时间范围切换不会显示上一节点的读数。
- `sampled_at` 来自采集端，允许与服务端最多 30 秒时钟偏差。`received_at` 由控制面赋值，单节点拒绝重复或倒序的采样时间。失败重试发送新的样本，不重放离线缓存。节点时钟应同步。
- 15 秒没有收到样本为“数据已过期”，60 秒为“采集离线”。前端用服务端时间估算经过时间，保留最后读数并标注时间；接口失败单独展示错误。恢复后自动更新。
- 首次 CPU 采样没有时间差，显示未采集。CPU 统计排除已计入 user/nice 的 guest 重复值；内存使用量为 MemTotal − MemAvailable。
- NVIDIA GPU 支持 `nvidia-smi` CSV 读取；不支持字段保留 null。缺驱动/采集能力与未发现 NVIDIA GPU 分开处理。AMD 等其他厂商 GPU 的设备监控不在首版范围。
- GPU 概览为所有可见设备利用率的算术平均、显存容量合计；有缺失值就不呈现不完整的“总量”。GPU 卡片是整卡占用，不是任务的独占资源。设备 UUID 用于关联历史。
- 内存使用 ≥90%、单卡显存使用 ≥95%、磁盘可用空间 <10%，连续至少 60 秒才提醒；采样缺口超过 15 秒或字段缺失都会中断连续性。不将高 GPU 利用率视为异常。温度/功耗展示原始数值，不猜测设备专属安全阈值。
- PostgreSQL 保存最新节点记录与历史样本。控制面启动时及每分钟清理超过 24 小时的历史；节点最后快照保留以便展示离线节点。15 分钟/1 小时/24 小时分别以 5/15/300 秒为桶返回末次真实读数，最大约 289 个点/序列。较长时间范围会隐藏桶内细节，可切换短时间范围查看。告警始终使用最近 90 秒原始数据，不依赖图表降采样。
- 图表同步时间轴交互，已知采样缺口不连线、不填零。训练列表仅返回绑定该节点、当前 attempt 的运行租约有效的训练。历史训练详情中的节点入口指向当前记录的 attempt 节点，不保证它代表过去所有 attempt。
- 不采集进程命令行/环境变量，不提供 kill、重启、GPU reset 等操作。磁盘 I/O、网络、精确的进程资源占用和外部通知留待后续。

## 接口与验证

完整契约位于 `go/api/openapi/hardware.yaml`，与现有生成式业务 API 并列，由 `go/internal/hardware.Handler` 注册。

- `POST /api/internal/hardware/samples`：专用 Bearer token，128 KiB 上限，字段/容量/范围/时间校验；成功 202，鉴权失败 401，倒序/重复 409。
- `GET /api/hardware/nodes`：节点目录和服务端时间。
- `GET /api/hardware/nodes/{nodeID}?window=15m|1h|24h`：最新快照、状态、历史、关联训练、资源提醒。
- 原训练任务读取新增可空 `runtime_node_id`；没有明确绑定的旧任务返回 null。

```bash
uv run pytest backend/tests/test_hardware_collector.py backend/tests/test_queue_training_worker.py -q
(cd go && go test ./internal/hardware ./internal/httpapi ./internal/adapters/postgres)
(cd frontend && npm run build && npm run smoke:hardware && npm run smoke:training-client && npm run smoke:routes)
# 先迁移一个隔离测试数据库，再运行持久化、降采样、保留期、任务租约关联测试：
(cd go && FINEVISION_TEST_GO_DATABASE_URL=postgres://... go test ./internal/hardware -count=1)
```

指标口径参考：[Linux proc 文档](https://www.kernel.org/doc/html/latest/filesystems/proc.html)、[NVIDIA System Management Interface 文档](https://docs.nvidia.com/deploy/nvidia-smi/index.html)。
