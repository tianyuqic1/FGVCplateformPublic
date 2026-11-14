# 部署验证

## 自动回归

```bash
bash -n deploy.sh scripts/demo-up.sh
python3 -m unittest discover -s deploy/tests -v
uv run pytest backend/tests/test_deploy_model_catalog.py backend/tests/test_pretrained_weights_manifest.py backend/tests/test_deployments.py -q
```

2026-11-14：26 项部署回归和 14 项权重/推理部署测试通过。部署回归在临时目录用 Docker 替身模拟失败，并用真实 Docker Compose 解析基础及 CUDA/模型/可观测性组合。

覆盖只读预览、配置优先级、私有权限、旧入口兼容、旧 Python 的容器引导、自定义外部配置复用、Compose 版本、独立项目、并发下载、损坏/超量下载、SHA-256、GPU 探测失败、部署锁、启动/健康失败不覆盖成功状态，以及省略开关后保留原有组合。CI 持续执行部署回归。

## 真实 Docker 生命周期

```bash
# 已构建应用镜像时使用；去掉 --no-build 可同时检查构建。
python3 deploy/tests/smoke_live.py --no-build --models all
```

测试自动创建 `finevision-test-<随机后缀>` 项目、配置、独立网络和数据卷，并为七个基础宿主映射选择空闲端口。依次检查默认 11 个服务、本机 CPU Worker 后的 14 个服务、前端/API HTTP、doctor、模型缓存与 MinIO 登记、无功能参数再次 up、restart、stop 后无运行服务且数据卷仍在。最终只删除测试项目的容器和数据卷。日志与结果保存在 `.deploy/<测试项目>/`；共享模型缓存保留，测试配置清理。

这项测试不修改已有 Compose 项目的服务。应用镜像标签、源码目录和模型缓存仍与工作区共享；不带 --no-build 会更新本地应用镜像标签。不要在需要固定镜像标签的发布窗口执行构建测试。

本次另外完成四个登记模型的实际准备及大小/SHA-256 校验，包含外部 iNat MobileNetV3 的 HTTPS 下载；总计 289,064,152 字节。LFS 已有的三个真实文件复用本地副本。

完整生命周期验收项目为 `finevision-test-59189e70b0`，结果通过；停止后保留卷，再由验收脚本清理。另通过真实 bootstrap 容器的 dry-run 和 models check，以及 UID 10001 容器只读挂载下四个缓存文件的哈希校验。

## 验证边界

本次生命周期测试复用已有应用镜像，不代表空缓存环境的全量构建已验收。CUDA/可观测性组合通过 Compose 渲染和模拟失败测试；未在此验收中实际启动 GPU 或新的可观测性栈。未提交真实训练、推理业务任务或付费 LLM 请求，平台健康检查不能替代这些专项验收。

主动拉取镜像的探测中，Quay 的 mc 镜像返回 `unauthorized`；本次真实启动使用已有镜像，未验证冷启动拉取。目标环境需确认第三方镜像源可访问，或提前通过受控途径加载对应镜像。`--pull` 只请求第三方服务，不尝试从公共仓库获取本地应用标签。
