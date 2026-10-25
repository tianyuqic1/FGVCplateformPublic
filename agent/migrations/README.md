# Agent 数据库与迁移

Agent 使用独立 `agent-postgres` 容器中的 `finevision_agent` database，不访问平台库。`001_initial.sql` 创建会话、Run、持久事件和迁移表；`002_watches.sql` 增加训练跟踪。`embed.go` 把迁移编入二进制。

`storage.Store.Migrate` 在一个事务中取得 advisory lock 后应用迁移并登记版本，支持并发启动与重复启动。平台 Alembic 仅维护平台表，不登记 Agent 表。

本地容器使用自己的库账号管理本库 schema。生产部署建议由管理员预建库、拆分 DDL / DML 角色并在启动前执行迁移；当前入口尚无独立 migrate-only 模式。

两份迁移都是增量建表，默认不自动回滚或删除会话。回退应用前保留 Agent volume 与数据库备份；停下 Agent 不影响平台已经提交的训练。事件和会话的定期保留清理尚未实现，需按部署容量安排维护。
