# 故障排查

先运行 `./deploy.sh status`、`./deploy.sh doctor`，再用 `./deploy.sh logs 服务名 --tail 100`。检查不会输出 `.env` 内容或供应商密钥；不要把完整 docker compose config 的环境输出贴到公开日志。

| 问题 | 检查 |
|---|---|
| Docker/Compose 不可用 | 启动 Docker daemon，安装 Compose 2.30+；首次无宿主 Python 时需能拉取 bootstrap 镜像 |
| 自定义配置找不到 | --env-file 必须存在，路径相对仓库根目录；默认 .env 才会自动创建 |
| 服务端口冲突 | 用 .env 的 FINEVISION_*_PORT 修改宿主映射；多个项目不能复用同一端口 |
| GPU 检查失败 | 驱动、NVIDIA Container Toolkit 和 Docker GPU 访问；修复后重试或显式选择 CPU |
| 权重缺失/损坏 | models list/check/pull；检查网络、磁盘、来源是否仍提供注册哈希的文件 |
| 权重已下载但初始化失败 | 查看 pretrained-weight-init 容器日志，核对 MinIO、对象桶与凭据；不会按成功继续 |
| Agent 数据库认证失败 | 现有 volume 密码必须匹配原账号；不能靠生成新 .env 密码修改已有 PostgreSQL 账号 |
| --no-build 找不到镜像 | 先正常 up 构建，或预载入所需本地标签的应用镜像 |
| 第三方镜像拉取 unauthorized | 单独验证报错镜像的 pull 与配置的镜像源访问权限，或提前加载已验证的对应镜像 |
| 存在部署锁 | 检查 .deploy/<project>/lock/pid，对应进程仍存活时不能移除 |
| 成功状态未更新 | 只有完整健康检查通过才保存 state；失败可能已经改变部分服务，先查状态和日志 |
| 基础部署没有本机算力 | 默认行为；用 --with-worker，或正确配置远程训练/推理地址 |
| Agent 不能生成回答 | 检查 Gateway 供应商配置；health 通过不等于模型凭据或额度有效 |

没有自动端口改号、CPU 回退、重试未知业务任务、删除 volume 或重置业务密码的行为。硬件和权重问题应修复配置后重新 up。
