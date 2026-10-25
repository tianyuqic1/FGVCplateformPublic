# Runner

`runner.go` 实现有预算的模型 / 工具循环、租约续期、确认等待、授权等待、停止和未知结果处理。`context.go` 对列表上下文投影并保留完整证据。`watches.go` 独立轮询已授权训练，等待时不占模型或 Run 槽。

领取、全局与用户限额、epoch fencing、检查点和持久事件在 `internal/storage`。领域状态结构在 `internal/service`，HTTP 层不直接派发模型。真实 PostgreSQL 并发、重启与确认测试见本目录 `runner_integration_test.go`。
