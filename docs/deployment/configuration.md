# 命令与配置

入口为根目录 `./deploy.sh`，无子命令等同 `up`。`--help` 列出全部选项。

| 参数 | 默认 | 含义 |
|---|---|---|
| `--with-worker` | 首次关闭 | 本机训练、推理、硬件采集 |
| `--with-models` | 首次关闭 | 准备所选预训练权重，启用本机 Worker |
| `--models A,B` | all | alias 或完整 preset，隐含 with-models |
| `--device cpu/cuda` | cpu | 本机计算设备，CUDA 必须启用 Worker |
| `--with-observability` | 首次关闭 | 可观测性组件 |
| `--env-file PATH` | .env 或保存路径 | 自定义文件必须存在；默认 .env 可以自动创建 |
| `--project-name NAME` | finevision | Compose 项目与独立状态空间 |
| `--no-build` | 关闭 | 复用本地应用镜像；缺失时报错 |
| `--pull` | 关闭 | 拉取第三方服务镜像；应用仍正常本地构建 |
| `--timeout N` | 180 秒 | Compose 就绪等待，超时非零退出 |
| `--dry-run` | 关闭 | 只读预览，不创建配置/状态，不下载权重或变更服务 |
| `--tail N` / `--follow` | 100 / 关闭 | logs 输出参数 |

up 的功能选择优先级：命令行 > 指定 `.env` 的 `FINEVISION_DEPLOY_*` > 上次成功选择 > 内置默认。对应变量见根 [.env.example](../../.env.example)。这些开关只接受字面值，不执行 shell、不展开 `$()`、反引号或其他变量引用。布尔值接受 true/false、yes/no、1/0。

业务配置仍由 Docker Compose 的 dotenv 和环境插值处理。状态保存配置文件路径与功能选择，不保存 API Key、数据库密码、Cookie 或完整环境。配置和状态权限为 0600，`.deploy/` 不进入 Git 或镜像构建上下文。

宿主 Python 低于 3.11 或未安装时，辅助程序在一次性 Python 容器内执行，不挂载 Docker socket。首次执行包括 dry-run 都可能需要拉取这个辅助镜像；dry-run 不准备权重，也不创建业务配置、部署状态或服务。可用 `FINEVISION_DEPLOY_BOOTSTRAP=container` 显式验证此路径。

## 隔离项目

```bash
cp .env.example .env.testserver
# 按需设置 FINEVISION_FRONTEND_PORT、FINEVISION_API_PORT、PostgreSQL/MinIO/RabbitMQ 端口。
./deploy.sh --project-name finevision-test --env-file .env.testserver
./deploy.sh status --project-name finevision-test
./deploy.sh stop --project-name finevision-test
```

不同 Compose 项目拥有独立网络与命名 volume，但应用镜像、本机模型缓存和源码目录共享。暴露端口必须分别设置，默认端口不会自动寻找空位；占用时 Docker 明确报错。可观测性覆盖文件还有固定本机端口，多套观测栈并存需单独覆盖端口。

生成的 `.deploy/<project>/plans/*.compose.json` 是可审查的 Compose override；根 Compose 是服务定义来源。GPU 设备、模型只读挂载与健康检查由所选计划补齐。不要编辑生成计划来替代配置源。

部署变更按项目加锁；另一个 up/restart/stop 不会并行抢占同一项目。失败不覆盖上次成功状态。锁文件有 pid，异常断电遗留时需确认对应进程确实不存在再清理锁目录。下载另按权重哈希加文件锁，多个项目可共享缓存。
