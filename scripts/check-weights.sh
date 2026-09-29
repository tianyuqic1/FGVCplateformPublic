#!/usr/bin/env bash
# 预训练权重预检。
#
# 对照 weights/manifest.json，检查每个权重是否存在、是否为真实文件（而非
# Git LFS 指针）、大小与 SHA-256 是否与清单一致。
#
# 默认【只告警、不阻断】：权重缺失不影响栈启动，运行时会回退到 timm 自带
# 预训练下载（见 backend/src/finevision/ml_toolkit/features.py）。
# 传 --strict 时发现问题以非零退出，可用作 CI 或发布前的完整性门禁。
set -uo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

strict=0
case "${1:-}" in
  "")        ;;
  --strict)  strict=1 ;;
  *)
    echo "用法：scripts/check-weights.sh [--strict]" >&2
    exit 2
    ;;
esac

if ! command -v python3 >/dev/null 2>&1; then
  echo "跳过权重预检：未找到 python3" >&2
  exit 0
fi

python3 - <<'PY'
import hashlib
import json
import pathlib
import sys

manifest_path = pathlib.Path("weights/manifest.json")
if not manifest_path.is_file():
    print("错误：找不到 weights/manifest.json", file=sys.stderr)
    sys.exit(2)

manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
weights = manifest["weights"]
LFS_MAGIC = b"version https://git-lfs.github.com/spec/v1"

problems: list[tuple[str, pathlib.Path, str]] = []
for weight in weights:
    path = pathlib.Path(weight["lfs_path"])
    label = weight["preset"]

    if not path.is_file():
        problems.append((label, path, "文件缺失"))
        continue

    with path.open("rb") as stream:
        head = stream.read(len(LFS_MAGIC))

    if head.startswith(LFS_MAGIC):
        problems.append((label, path, "是 Git LFS 指针，真实权重未拉取"))
        continue

    size = path.stat().st_size
    if size != weight["size_bytes"]:
        problems.append((label, path, f"大小不符：实际 {size}，期望 {weight['size_bytes']}"))
        continue

    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1 << 20):
            digest.update(chunk)
    if digest.hexdigest() != weight["sha256"]:
        problems.append((label, path, "SHA-256 与清单不一致"))

if not problems:
    print(f"权重预检通过：{len(weights)} 个权重均就位且校验一致")
    sys.exit(0)

for label, path, reason in problems:
    print(f"  [{label}] {path}：{reason}", file=sys.stderr)

if any("Git LFS 指针" in reason for _, _, reason in problems):
    print("", file=sys.stderr)
    print("  修复方式：", file=sys.stderr)
    print("      git lfs install --local && git lfs pull", file=sys.stderr)
    print("  注意：只跑 git lfs pull 在未配置 filter 的机器上会退出码 0 却不还原文件，", file=sys.stderr)
    print("        因此 git lfs install 这一步不能省。", file=sys.stderr)

print("", file=sys.stderr)
print(f"  共 {len(problems)}/{len(weights)} 个权重不可用。", file=sys.stderr)
print("  这【不影响】docker compose 启动：运行时将回退到 timm 自带预训练下载。", file=sys.stderr)
sys.exit(1)
PY
status=$?

if [[ "$status" -eq 0 ]]; then
  exit 0
fi

if [[ "$status" -eq 2 ]]; then
  exit 2
fi

if [[ "$strict" -eq 1 ]]; then
  echo "权重预检未通过（--strict）" >&2
  exit 1
fi

echo "权重预检有告警，按非严格模式继续。" >&2
exit 0
