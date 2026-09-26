export function annotationServiceLabel(worker) {
  if (worker?.service_state === "initializing") return "AI 服务正在初始化 · 可人工标注";
  if (worker?.service_state === "ready") return "AI 服务已就绪";
  if (worker?.online) return "AI 服务已连接";
  if (worker?.service_state === "unavailable" || worker?.online === false) return "AI 服务暂不可用 · 可人工标注";
  return "AI 服务状态暂未确认 · 可人工标注";
}
