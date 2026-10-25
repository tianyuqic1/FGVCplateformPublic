# FineVision Agent 设计文档入口

Agent 已确定采用独立服务、独立数据库，通过平台 API 集成；首期一个 Docker 容器内运行 API 和后台 Runner。

方案与模块文档已统一放入 [agent 目录](../agent/README.md)：

- [当前实现与运行](../agent/docs/runtime.md)
- [测试集错分分析](../agent/docs/test-error-analysis.md)
- [架构与并发设计](../agent/docs/architecture.md)
- [平台与模型服务集成契约](../agent/docs/integration-contracts.md)
- [实施计划](../agent/docs/implementation-plan.md)

本文件保留原文档入口，完整设计以 `agent/docs/` 为准。完整首版对话、后台执行、训练跟踪、工具接入和独立容器已实现并通过本地真实模型验收。当前启动方式、准确能力边界和测试记录见 [运行说明](../agent/docs/runtime.md)。
