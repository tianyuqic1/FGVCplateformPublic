# AI 标注工作区（本地首版）

入口：`http://localhost:5173/annotation`，左侧导航「AI 标注」。

这是分类标注平台，不是目标检测框/掩膜编辑器。SAM 的作用是辅助生成可信主体视图，不直接生成最终人工标签。实验室原始代码、测评集和 48 组结果保持不变。

## 已接入的操作

- 新建项目：名称、JSON 文件导入的 2–1000 类固定目录、图像领域、A/B/C/D 方案。提供 JSON 模板、前后端校验、每页 6 类的搜索预览，不再使用每行文本输入。
- 上传未标注 JPG/PNG，每批最多 100 张；每张最多 20 MiB、2500 万像素。原图经实际解码校验后保存到 MinIO，按项目 + 源文件 SHA 去重。
- 图片队列服务端分页，每页 12 张，按状态筛选。大类别目录和项目下拉支持搜索、分页。
- AI 单张、本页批量或全项目跨页批量建议。全项目批量先显示待分析数量并二次确认，冻结当前最大序号，只排队快照范围内的 pending 图片；之后上传不加入、重试请求不重复排队。显式勾选远程图片传输与计费提示后才可提交；上传本身不会触发远程请求。仍以低资源单 Worker 顺序消费，不等于并发启动所有模型。
- 原图查看、Top-10 候选、完整类别目录改选、人工确认、标注人记录、工具路线及降级信息。
- 人工确认后异步入库。没有 AI 建议、候选不完整、远程失败的图片，也可以人工标注。
- 导出确认结果 JSON，包括图片 SHA、稳定 ID、类别 ID、标注人、时间和图片下载 URL。

项目人工标签与已注册训练版本相互隔离。新增「待发布区 / 发布记录」：人工确认后可以显式发布为新数据集，或追加到已有版本；复核反馈可补充到来源版本的训练集。不会自动修改原有版本或启动训练，已注册标签始终只读。完整规则见 [数据集发布与回流](annotation-dataset-publication.md)。仍不提供多人账号权限、已确认标签原地修订。已确认同标签重复提交幂等；修改已确认标签会返回 409，避免向量库里残留旧标签。目录变更请建立新项目。UI 的标注人字段是本地审计备注，不代表已验证账号身份。

## 工作流

### JSON 类别目录契约

仅导入 `.json` 文件（最大 512 KiB），格式如下；`id/name` 为非空字符串、各不超过 180 UTF-8 字节，无首尾空格，ID 和名称分别唯一。固定 `schema_version: 1`，拒绝未知字段。模板可在创建表单下载。

```json
{"schema_version":1,"classes":[{"id":"001","name":"Black-footed Albatross"},{"id":"002","name":"Laysan Albatross"}]}
```

新客户端创建项目使用 `catalog` 字段提交此对象；服务端校验后将 `classes` 保存到原有 PostgreSQL JSONB 列，作为唯一权威目录，不另存一份可能失步的本地文件。AI Worker、确认、检索和数据发布继续读取这个 JSON 数组。既有项目无需迁移，旧 API 的 `classes` 数组保留兼容，但不能同时提交 `catalog/classes`。

- `POST /api/annotation/catalog/validate`：无副作用的目录校验，返回标准 JSON。
- `GET /api/annotation/projects/{id}/catalog`：下载当前项目的版本化 JSON 目录。
- `GET /api/annotation/projects/{id}/queue-summary`：pending/queued/running 数量和 `through_seq`。
- `POST /api/annotation/projects/{id}/queue-all`：显式 `allow_remote:true` 和确认时的 `through_seq`，仅将该项目 `seq<=through_seq` 的 pending 图片置为 queued。已确认、suggested、failed、unknown 不重放。无需数据库迁移。

新增回归：`go test ./internal/annotation -run 'TestCatalogValidationAPI|TestBatchQueueDatabase'`（后者需要隔离测试库），`node scripts/test-annotation-catalog.mjs`、`node scripts/smoke-annotation-catalog.mjs`。UI 验收仅调用无副作用的校验接口，不创建项目或触发付费分析。

1. Go 控制面负责项目、上传、分页、任务状态、确认和导出。PostgreSQL 保存任务；单 Worker 用 `FOR UPDATE SKIP LOCKED` 拉取待执行记录。本地首版复用 PostgreSQL 持久队列，没有再为标注增设 RabbitMQ 消费者。
2. Python 仅协调图像计算与持久阶段状态；远程多模态请求由 `go/cmd/annotation-agent` 的 Go/Eino 适配器执行，不把凭据发送给 SAM、CLIP 或增强容器。
3. 方案 A 直接原图分类；B 执行定位/分割/按需增强但不检索；C 加入 CLIP 图像检索；D 加入 CLIP 图像和文本的类别级 RRF 融合。
4. Qwen 定位 → SAM2 掩膜保护 → 保守合并框并留白 → 从原始像素裁剪。原图永远保留，主体过小、几乎全图或完整性不符时不用该视图。
5. 质量规则或模型选择至多一种增强：Retinexformer 暗光、SwinIR ×2 超分、Restormer 去噪/运动模糊/散焦模糊。增强图只是额外证据，不能替代原图，不自动写入检索库。
6. 检索作用域为项目 + 类别目录摘要 + 编码器修订。每类至少 10 张不同源 SHA、人工确认且向量投递成功才参与检索。按类别去重，排除自身；原图与合格主体两个视图仍只算一张。
7. D 的文本模板按通用/鸟类/汽车分别生成；图像、文本排序用等权 RRF，而不是把相似度当成概率。最多六类参考图，分类请求总图数不超过八张。分类始终看到完整目录，不限于检索类别。
8. 预测先锁定，用户再确认或改选；预测本身绝不入库。人工标签与 PostgreSQL 入库 outbox 同一事务保存。Worker 先生成原图向量（可附合格主体向量），再幂等写入 Qdrant，最后确认 outbox。

真实标注允许乱序确认，因此检索门槛按“预测时已确认并投递成功”判断，不再用测评集的上传序号模拟过去。所有项目记忆均为空库起步，不自动导入实验真值或评测参考库。

## 稳定性与降级

- AI 阶段沿用 v4.4 持久 ledger、请求摘要、最多 3 次/阶段和 6 次/图片远程尝试、240 秒累计远程预算。429/明确可重试响应遵守冷却；超时/断流等结果未知会隔离，不自动重新付费。
- 远程 ACK 先存本地文件，再提交控制面；丢失响应时只重发相同 ACK。相同 token/结果幂等，不重放模型调用。
- `running` 任务持有 20 分钟租约；过期转 `unknown`，不是重新入队。Worker 重启不会自动恢复可能已经计费的调用。首版无付费重试按钮，可人工完成这些图片。
- 定位失败回原图，SAM 失败回定位框，增强失败回未增强图，检索失败降级无检索并记录警告。
- 向量库失败保留人工标签和 outbox，60 秒后补偿；投递未成功不推进类别成熟度。原图向量失败不能假装入库成功。
- 单 Worker 有进程锁。此版本不支持多个机器同时共享该记忆协调器；扩容前需要迁移本地阶段 ledger/参考图片寻址并实现跨节点所有权。

## 低资源部署

| 组件 | 本机配置 | 空闲行为 |
|---|---|---|
| Qwen3.5 4B Q4_K_M + 视觉投影 | llama.cpp，CPU，4 线程/4 CPU 上限，8 GiB 内存上限，4096 上下文，1 slot，图像 token 256–768 | 保留模型，实测空闲约 2.2 GiB |
| SAM2.1 Hiera Base+ | 每任务临时 CPU 容器，2 CPU、6 GiB 上限，单并发，120 秒调用限制 | 结束移除容器，不占常驻显存 |
| CLIP/增强网络 | 与 SAM 串行、按需临时 CPU 容器，禁网、模型只读 | 结束释放；只缓存结果 |
| Qdrant | 独立服务，1 CPU、1 GiB 上限，宿主机 `127.0.0.1:6335` | 初始小库实测约 58 MiB |
| 标注 Worker | 本地后台进程，1 个任务执行槽，独立 Python 环境 | 3 秒检查任务，10 秒心跳 |

本机所谓的“VLM 服务”继续使用已经验证的 GGUF/llama.cpp，**没有安装或启动 vLLM 引擎**。低资源服务不会使用 GPU；与旧测评的 GPU/更高视觉 token 配置不同，不能直接继承旧测评的准确率结论。

CPU 配置对增强额外限流：辅助增强输入长边最多 512；超分仅接受原生长边 ≤224 的视图，超出即回退原图，不先缩小再放大。验收中直接对 500×477 图片调用 SwinIR 触发了 120 秒时限，因此加入了这条适用范围保护，避免不合适的超分请求拖住标注队列。

服务镜像和现有模型 SHA 由启动脚本及工具 manifest 固定。Qdrant server 1.18.1 搭配独立环境的 client 1.18.0，不修改实验室 v4-env。未自动下载新的模型权重。

### 启动

平台已按正常流程构建 Go 控制面并执行 `20260914_0019` 数据库迁移。新机器/重新部署：

```bash
docker compose build go-control-plane migrate
docker compose run --rm --no-deps migrate
docker compose up -d --no-deps go-control-plane
python3 -m venv annotation/runtime/venv
annotation/runtime/venv/bin/python -m pip install -r annotation/requirements.txt
python3 annotation/start_local.py
```

启动器默认从仓库内被 Git 忽略的 `annotation/models/` 读取工作流网络和视觉投影，并复用本机 Qwen/SAM 快照。可通过 `ANNOTATION_ASSET_ROOT`、`ANNOTATION_QWEN_MODEL`、`ANNOTATION_SAM_MODEL` 指定路径。模型文件不随源码发布，目录约定见 `annotation/models/README.md`。已有容器不会被强制替换，变更资源参数需要先确认无任务运行，再重建两个专用容器。

凭据从现有 `finevision-go-llm-gateway-1` 的运行时配置读入，只传给私有 Go/Eino 适配器进程，不输出密钥。独立部署 Worker 时配置 `ANNOTATION_WORKER_TOKEN` 与控制面的 internal token 一致，以及 `VLM_BASE_URL`、`VLM_MODEL`、`VLM_API_KEY`。Worker CLI 可设置 API、定位和 Qdrant 地址。不要把 token 填进前端。

### 持久化与运维

- 标签/队列：PostgreSQL `annotation_projects`、`annotation_tasks`、`annotation_memory_outbox`。
- 原图：MinIO `finevision-artifacts/annotation_image/<SHA前缀>/<SHA>`，读取时再次验证 SHA 和大小。
- 阶段 ledger、参考原图/主体缓存、文本原型：`annotation/runtime/state/<项目UUID>/`。图片原始下载缓存位于 `annotation/runtime/state/images/`。
- 向量：独立 Docker volume `finevision-annotation-qdrant`。备份应同时覆盖 PostgreSQL、MinIO、Worker state 和 Qdrant，不能单独删除阶段 ledger 或缓存目录。
- 日志/PID：`annotation/runtime/worker.log`、`annotation/runtime/worker.pid`。停止前先查 `/api/annotation/status` 确认无活动任务；验证 PID 对应 `annotation/worker.py` 再发送 SIGTERM。重启使用启动器。
- 启动器保持容器 `unless-stopped`；宿主 Worker 为后台进程，不自动注册开机启动。机器重启后再执行启动器即可。
- 模型工具计算失败可在 UI「工作流记录与降级信息」查看。`unknown` 不代表模型答错，也不代表未计费。

沿用当前平台的**可信本地开发**安全边界：公开 UI/API 尚无多用户鉴权，标注人可手填。不得直接暴露到公网；远程访问前需加入账号权限、审计、TLS、上传配额和独立 Worker 凭据。Qwen/Qdrant 的宿主端口仅绑定 loopback。

## 测试入口

```bash
cd go && go test ./...
# 真实数据库测试只允许 annotation_test_ 前缀的独立库，先迁移到 head。
ANNOTATION_TEST_DATABASE_URL=postgres://.../annotation_test_example go test ./internal/annotation -v
cd ../annotation && runtime/venv/bin/python -m unittest test_platform -v
cd ../frontend && npm run build
# PLAYWRIGHT_MODULE 可指向本机已安装的 Playwright 模块。
node scripts/smoke-annotation-ui.mjs
```

`annotation/smoke_live.mjs` 创建独立验收项目，导入官方训练集的 10 张已知标签参考图和 3 张匿名待标图片，验证真实 MinIO/队列分页/导出。执行前必须通过 `ANNOTATION_BENCHMARK_ROOT` 显式指向私有测评夹具目录。**只有显式加 `--queue` 才提交 1 张付费分类**，重复执行不会重试已运行图片。此脚本不是自动为业务图片标真值的功能；Worker 没有评测 truth reader。

测评结论仍是：CUB D 的 Top-10 为 95.83%，鲁棒性 D 为 92.33%；平台接入与单张验收不等于达到 97% 或 100%。

### 2026-09-13 本机验收记录

- `go test ./...` 通过；新模块独立 PostgreSQL 集成测试通过，覆盖去重、隔离、token、租约过期、重复 ACK、确认与入库 outbox。
- 平台 Python 10 个测试通过，覆盖少类别目录、未知调用不重放、人工入库门禁、每类 10 张、跨项目/自身排除、入库补偿、原图+主体算一个来源、增强图不入库、CPU 超分限制。
- 前端构建、真实页面桌面/390px 窄屏检查通过：12 张分页、末页边界、类别搜索、人工改选、远程授权门禁、无横向溢出或页面 JS 异常。
- 「验收 · CUB 鸟类标注」项目包含 13 张图：10 张官方训练集参考图已人工确认并实际写入 Qdrant；3 张待标图中只提交了 `query-1.jpg` 一张真实 AI 任务。该图返回 10 个合法候选，首项 Cardinal；路线 `qwen_sam_union`，检索参考 1 张，无降级警告，未自动确认候选。
- D 的 200 类文本原型已真实生成（512 维），原图/主体查询实际命中 Qdrant。这里只验证链路，不把单张结果当准确率测评。
- 五种增强工具的受限 CPU 执行检查全部通过：暗光 4.63 秒、原生 96×96 图 ×2 超分 7.96 秒、去噪 32.37 秒、去运动模糊 32.49 秒、去散焦模糊 32.84 秒。除超分外测试输入为 500×477。计时包含临时容器/模型启动；不表示每张标注都会运行这些工具。
- 本地原始验收记录：`annotation/runtime/live-acceptance.json`、`annotation/runtime/cpu-tool-results-bounded.json`；运行时文件不提交 Git。旧的超分超时记录保留在 `cpu-tool-results.json` 便于对照。
