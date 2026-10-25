# Agent 公共契约

- `toolcontract`：15 个工具的闭合 JSON Schema、校验与平台工具 OpenAPI。
- `delegation`：有期限、用途和会话绑定的 HMAC 身份断言。
- `modelcontract`：Gateway v2 多轮消息、工具调用和用量协议。
- `openapi.yaml`：Agent 会话、Run、事件、确认和训练跟踪的浏览器接口。

实际路由实现在 `internal/httpapi`，浏览器经平台 `/api/agent/*` 代理到 Agent `/v1/*`。工具调用走平台独立的 `/api/agent-tools/*`，两者职责不同。状态、限制及当前边界见 [运行说明](../docs/runtime.md)。
