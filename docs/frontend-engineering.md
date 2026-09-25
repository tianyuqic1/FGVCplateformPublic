# 前端工程约定

FineVision 前端采用领域模块、统一请求状态和路由级按需加载。页面只消费已经规范化的领域数据；HTTP 路径、重试和缓存策略集中在 `src/api` 与 `src/query`。

## 本地检查

```bash
cd frontend
npm ci
npm run check
npm run check:api-types
npm run test:e2e
```

`npm run check` 会依次执行静态检查、增量格式检查、TypeScript 契约检查、单元与组件测试、生产构建和产物体积预算。

## 目录职责

- `src/features`：按数据集、训练、推理、复核、模型、流水线等领域组织页面实现。
- `src/api`：HTTP Adapter、响应规范化和 OpenAPI 生成类型。
- `src/query`：缓存、请求去重、重试、轮询与 mutation 状态的统一 Interface。
- `src/design-system`：token、布局、表格、分页、筛选与异步状态等共享视觉规则。
- `src/hooks`：向页面提供兼容的领域 hook，不再重复实现请求生命周期。
- `e2e`：从用户视角验证关键路由和工作流入口。

## OpenAPI 类型

后端契约源为 `go/api/openapi/finevision.yaml`。执行：

```bash
npm run generate:api-types
```

生成结果保存在 `src/api/generated/finevision.d.ts`。CI 会重新生成并检查差异，后端字段变化不能静默绕过前端。

## 页面状态语义

- 总数只有在接口明确返回时才展示；否则显示“已读取 N 条”。
- 绿色状态必须对应真实系统证据，建议人工核查项使用琥珀色并说明原因。
- 数据集、模型版本等工作台作用域写入 URL，链接可以复现当前视图。
- 加载、刷新、空结果、失败和后台处理中必须使用明确的文字状态，不能仅依赖颜色。

## 样式规则

- 共享 token 与控件样式放在 `src/design-system`。
- 业务模块不得引用另一个业务模块的 CSS。
- 新样式优先复用现有 token，不使用新的硬编码品牌色。
- 大表格支持分页，筛选后回到第一页，并显示筛选后的真实数量。
