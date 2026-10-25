# Agent 验收

常规测试分布于 `api/*`、`internal/tools`、`internal/adapters` 和 `internal/runner`；真实 PostgreSQL 用例要求专用 `FINEVISION_AGENT_TEST_DATABASE_URL`，每例新建并清理独立 schema。

- `smoke_runtime.py`：显式使用测试账号和真实模型，验证数据/Worker 查询、持久事件和重复请求键。
- `smoke_test_errors.py`：显式小样本训练，核对独立 test 证据和历史报告不可用行为；详见[错分分析验证](../docs/test-error-analysis.md#验证)。
- `smoke_write.py`：显式开启小样本写验收，验证方案、重复确认、唯一训练、训练成功和后台 Watch；只接受至多 16 个样本的测试版本。
- `frontend/e2e/agent.spec.js`：确认按钮、请求键、刷新恢复、角色和抽屉，使用受控接口替身。
- `frontend/e2e/agent.live.spec.js`：显式测试真实登录、助手历史、证据和抽屉，默认跳过。

完整命令与限制见 [运行说明](../docs/runtime.md#验证)。不要把账号密码、Cookie、密钥或数据库连接输出到测试报告。
