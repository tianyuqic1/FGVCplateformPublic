# 部署指南

更新：2026-11-14。项目统一使用根目录 `deploy.sh`，以 Docker Compose 部署。当前是单机/私网运行方案，HTTPS、生产密码、备份和公网网络边界仍须按环境配置。

## 环境与首次启动

需要 Bash、Docker Engine 和 Docker Compose 2.30+。宿主机不必安装 Node、Go 或 Python；脚本优先用 Python 3.11+ 执行标准库辅助程序，缺失时使用一次性 `python:3.12-alpine` 容器。CUDA 模式另外需要 NVIDIA 驱动与 NVIDIA Container Toolkit。

```bash
# 下载源码时可先跳过大权重，稍后按需准备。
GIT_LFS_SKIP_SMUDGE=1 git clone git@github.com:tianyuqic1/FGVCplateformPublic.git
cd FGVCplateformPublic
./deploy.sh --dry-run
./deploy.sh
```

首次创建 `.env` 并补齐 Agent 空白密钥和数据库密码，已有非空字段不会覆盖。新机器构建计算镜像可能较大；即使不启用训练，平台的数据校验/导出运行时仍使用该镜像。

默认启动平台、实验助手、必要数据运行时和基础设施，不启动本机训练、推理、硬件采集。没有模型供应商配置时，可以登录和管理平台，但 Agent 不能生成模型回答。脚本不会代用户调用收费模型或提交训练。

## 常见组合

```bash
./deploy.sh --with-worker
./deploy.sh --with-models
./deploy.sh --with-worker --with-models --device cuda
./deploy.sh --with-models --models dinov3-vits,inat-mobilenetv3
./deploy.sh --with-worker --with-observability
./deploy.sh --env-file .env.server --no-build
```

- `--with-worker`：本机训练、推理和硬件采集，默认 CPU。
- `--with-models`：准备全部部署清单中的预训练权重，隐含本机 Worker。只有所选权重全部下载并通过 SHA-256/大小校验后才启动服务。
- `--models`：按 alias 或完整 preset 选择，隐含 `--with-models`。`all` 代表部署清单全部模型，不包含尚未登记的其他骨干、SAM2、CLIP 或任意 LLM。
- `--device cuda`：对本机训练和推理启用 NVIDIA GPU；不静默降级 CPU，不等同于启用 TensorRT。
- `--with-observability`：同时启动日志、指标和追踪组件。

默认地址为工作台 `http://localhost:5173`、API `http://localhost:8001`。部署成功输出实际前端端口。首次管理员初始化命令也在成功输出中提供，密码从终端读取。

## 日常操作

```bash
./deploy.sh status
./deploy.sh logs agent-service --tail 100
./deploy.sh logs --follow
./deploy.sh doctor
./deploy.sh restart
./deploy.sh stop
```

只有部署成功并通过健康检查才保存 `.deploy/<project>/state.json`。后续省略参数会复用功能组合；status/logs/restart/stop 使用保存的选择，不随 `.env` 中功能开关变化而切换拓扑。改变设备或启用新功能请使用 up。停止保留容器、数据库 volume 和模型缓存，不执行 down -v。

`.env` 显式关闭已保存功能会报配置冲突，不自动停掉旧 Worker。需要完全更换组合时，先停止原组合，再另建项目或在确认原服务已停止后备份并移走其 state.json。没有保存状态的既有部署，应首次带上原有 `--with-worker`、`--device`、`--with-observability` 参数。

doctor 检查 Docker、Compose 配置、所选权重和已部署服务状态；CUDA 还检查容器 GPU 访问。新环境尚未启动会报告未就绪。健康检查不是实际训练、远程推理或付费模型验收。stop/restart 会影响所选服务，请先让长任务完成。

## 文档入口

- [参数和配置](configuration.md)
- [模型准备、缓存与离线部署](models.md)
- [本机及远程 Worker](workers.md)
- [升级与回退](upgrade.md)
- [故障排查](troubleshooting.md)
- [回归测试与部署验收](validation.md)

旧 `scripts/demo-up.sh` 保留兼容，会转发为 `deploy.sh --with-worker`；旧 `--gpu` 转为 `--device cuda`，`--skip-smoke` 兼容接受。新入口只进行部署健康检查，开发测试见根 README。

AI 标注继续使用 [标注运行说明](../annotation-platform.md)，TensorRT/Ascend 使用 [硬件推理部署](../inference-deployments.md)。它们尚未整合进 deploy.sh，不接受占位开关。
