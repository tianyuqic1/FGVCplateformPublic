# Agent 应用状态

首版状态类型位于 `internal/service/types.go`，HTTP 应用层位于 `internal/httpapi`，持久化位于 `internal/storage`，模型与工具编排位于 `internal/runner`。本目录保留设计入口，不额外复制一套状态机。

完整运行与扩展边界见 [runtime.md](../../docs/runtime.md)。
