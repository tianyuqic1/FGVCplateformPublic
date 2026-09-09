export function formatBytes(bytes) {
  const value = Number(bytes);
  if (!Number.isFinite(value) || value <= 0) return "0 MB";
  if (value >= 1024 ** 3) return `${(value / 1024 ** 3).toFixed(1)} GB`;
  return `${(value / 1024 ** 2).toFixed(1)} MB`;
}

export function modelWeightTone(state) {
  if (state === "cached" || state === "managed") return "default";
  if (state === "partial") return "warn";
  return "neutral";
}

export function modelWeightLabel(state) {
  return {
    managed: "已纳入管理",
    cached: "已缓存",
    partial: "下载中/未完成",
    missing: "未缓存",
    loading: "读取中",
  }[state] ?? "状态未知";
}

export function modelWeightDetails(weight) {
  if (weight?.state === "managed") return `${formatBytes(weight.sizeBytes)} · 登记大小 · 节点缓存未上报`;
  if (weight?.state === "cached") return `${formatBytes(weight.cacheBytes)} · ${weight.completeFileCount} 个文件`;
  if (weight?.state === "partial") return `${formatBytes(weight.partialBytes)} · 下载未完成`;
  if (weight?.state === "missing") return "未下载";
  return "等待权重状态";
}
