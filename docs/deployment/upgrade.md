# 升级与回退

1. 确认长任务已完成，备份平台、Agent 数据库和所需对象存储。
2. 更新代码并预览与原部署一致的功能组合；旧脚本部署没有 state，首次使用新入口需显式带上原 Worker/GPU/观测参数。
3. 执行 deploy.sh up。脚本先准备权重和镜像，再通过 Compose 的依赖关系应用平台迁移并启动服务；Agent 应用本库迁移。
4. 通过配置的健康检查才写入成功状态；使用 status、doctor 核对。

```bash
./deploy.sh --with-worker --with-models --device cuda --dry-run
./deploy.sh --with-worker --with-models --device cuda
./deploy.sh doctor
```

失败不删除已运行容器、业务数据或模型缓存，也不覆盖上次成功 state；Compose 启动期间仍可能已更新部分服务，因此不是跨服务原子发布。修复问题后重复 up，或恢复兼容的旧镜像/代码并使用原组合。新入口不是自动回滚引擎。

restart 复用现有镜像和保存的组合，不替代代码升级所需的构建。stop 保留 volume，不能当作数据库备份。脚本不执行数据库降级或自动删表；回退旧版本前必须确认迁移兼容性。已提交训练不随 Agent 停止自动撤销。

当前前端仍是开发用 Vite 容器；deploy.sh 统一部署入口不代表已交付生产静态前端、HTTPS、滚动升级或多节点调度。
