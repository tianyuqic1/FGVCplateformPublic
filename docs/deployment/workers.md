# Worker 与设备

默认部署不启动本机训练、推理和硬件采集。必要的 `python-artifact-runtime` 仍属于基础服务，负责数据校验和导出，当前与训练共用计算镜像。

```bash
./deploy.sh --with-worker                    # CPU，不主动下载权重
./deploy.sh --with-worker --with-models       # CPU，先准备权重
./deploy.sh --with-worker --with-models --device cuda
```

GPU 模式先运行一次容器内 nvidia-smi 检查 NVIDIA 驱动与 Toolkit，再给所选本机训练、推理、采集服务配置 GPU。该探测镜像是 ubuntu:24.04，离线环境也应预先准备。训练与推理可能共享同一张卡；脚本不提供显存调度或资源预留。

CPU 模式明确配置 CPU，不根据 GPU 是否存在自动切换。CUDA 不表示 ONNX CUDA/TensorRT/Ascend 所有后端都可用；硬件模型构建仍按 [推理部署](../inference-deployments.md) 配置。

## 远程计算

没有本机 Worker 时，可以管理资源、查看历史和对接远程 Worker。远程训练进程需要配置控制面 gRPC、状态上报 HTTP、RabbitMQ、MinIO、独立 Worker ID 和节点 ID；Compose 内部服务名仅在同一网络有效。

远程推理另设控制面的 `FINEVISION_INFERENCE_GRPC`，仅启用远程心跳不能替代推理 RPC 地址。`FINEVISION_DATASET_GRPC` 可覆盖数据服务地址，但首版仍保留本机数据运行时。远程机器的启动与网络配置尚未自动化成一个 worker-only 子命令。

状态和角色语义以 [Worker 状态文档](../worker-status.md) 为准。该页保留上报协议，部署参数以本目录为准。部署不会主动提交训练、推理或付费模型调用作为健康检查。
