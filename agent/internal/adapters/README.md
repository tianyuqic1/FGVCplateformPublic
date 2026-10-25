# Agent 基础设施适配器

- `platform`：请求级会话或受限签名委托、工具调用、确认回执和写操作对账。
- `model`：LLM Gateway `/internal/v2/chat` 多轮协议、超时与响应上限，不自动重试未知请求。

数据库实现位于 `internal/storage`，HTTP 与 SSE 位于 `internal/httpapi`。当前使用可持久化的显式执行循环，没有额外 Eino 适配器，也没有 Gateway 请求状态/取消端点。各客户端不记录凭据与模型正文，Agent 不接收供应商密钥。
