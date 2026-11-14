# Agent 部署

`Dockerfile` 构建独立 Agent 镜像；主 `docker-compose.yml` 已增加 `agent-service` 和 `agent-postgres`。首期一个 Agent 进程提供 HTTP、4 个 Runner 和独立 Watch 轮询，启动时使用事务锁应用本库迁移。

从项目根目录运行 `./deploy.sh`，会补齐本地随机密钥并启动基础平台与 Agent；本机训练用 `--with-worker`。统一命令和升级步骤见 [部署指南](../../docs/deployment/README.md)。旧 `scripts/demo-up.sh` 仅保留兼容。已有密钥不会覆盖；数据库密码生成后必须与已有 volume 一致，不能通过重新生成密码来修改已有 PostgreSQL 账号。

配置、端口、数据隔离、并发、健康检查、测试和当前扩展边界见 [当前实现与运行](../docs/runtime.md)。未来可拆分 API / Runner 和生产迁移角色；当前未提供这些独立部署角色。
