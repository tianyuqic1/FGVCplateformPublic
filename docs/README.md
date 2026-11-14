# FineVision 文档目录

先按使用场景选择入口。当前操作说明、组件设计和历史验收分别列出；历史命令不替代当前部署指南。

## 部署与日常维护

- [部署总览与快速启动](deployment/README.md)
- [命令、环境配置与隔离项目](deployment/configuration.md)
- [预训练权重、缓存与离线准备](deployment/models.md)
- [本机 / 远程 Worker 与 CPU / CUDA](deployment/workers.md)
- [升级、迁移与回退](deployment/upgrade.md)
- [故障排查](deployment/troubleshooting.md)
- [部署验证与复现](deployment/validation.md)

## 使用平台

- [账号、角色和审核](auth.md)
- [数据集导入](dataset-import.md)、[样本预览](dataset-preview-api.md)
- [实验助手与 15 个工具](../agent/README.md)、[测试集错分分析](../agent/docs/test-error-analysis.md)
- [AI 标注](annotation-platform.md)、[标注发布与数据回流](annotation-dataset-publication.md)
- [Worker 状态与权限](worker-status.md)、[硬件监控](hardware-monitoring.md)
- [多后端推理与硬件专项部署](inference-deployments.md)
- [日志、指标、追踪和审计](observability.md)
- [预训练权重身份](../weights/README.md)、[对外模型发布包](../pretrained/README.md)

## 开发与组件设计

- [项目 README：开发检查](../README.md#开发与验证)
- [前端工程约定](frontend-engineering.md)
- [Agent 架构](../agent/docs/architecture.md)、[跨服务协议](../agent/docs/integration-contracts.md)、[实施记录与后续计划](../agent/docs/implementation-plan.md)
- [仪表盘设计决策](decisions/dashboard-command-center.md)

## 历史验收与展示

以下材料记录当时版本，不能视为当前部署状态或持续运行承诺：

- [可观测性验收](observability-acceptance.md)、[原实施计划](observability-implementation-plan.md)
- [前端浏览器验收](qa/frontend-browser-acceptance-2026-09-25.md)、[缺陷记录](qa/frontend-browser-defect-report-2026-09-25.md)
- [项目展示文档](../showcase/README.md)

部署命令只在 `docs/deployment/` 维护，模块文档链接该入口；添加新命令需同步脚本帮助、配置示例和回归测试。展示站的历史内容独立保留，不从展示页复制命令作为操作来源。
