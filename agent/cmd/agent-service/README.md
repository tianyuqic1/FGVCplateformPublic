# Agent 服务入口

`main.go` 校验配置、连接 Agent 本库、应用有锁迁移，装配平台工具客户端和 LLM Gateway v2 客户端，启动 HTTP、4 个持久 Runner 和一个 Watch 轮询器。SIGINT / SIGTERM 触发优雅退出。

长任务从数据库领取，不在 HTTP 请求里执行模型循环。浏览器断开不会取消后台 Run；所有外部调用均有期限。启动方式和实际限制见 [运行说明](../../docs/runtime.md)。
