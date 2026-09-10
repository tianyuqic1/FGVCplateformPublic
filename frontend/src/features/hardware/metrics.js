export const isMetric = (value) => typeof value === "number" && Number.isFinite(value);
export const percent = (value) => isMetric(value) ? `${value.toFixed(1)}%` : "—";
export const ratio = (memory) => isMetric(memory?.used_bytes) && isMetric(memory?.total_bytes) && memory.total_bytes > 0 ? memory.used_bytes / memory.total_bytes * 100 : null;
export const gib = (value) => isMetric(value) ? (value / 1024 ** 3).toFixed(1) : "—";
export function gpuSummary(snapshot) {
  const gpus = snapshot?.gpus ?? [];
  const complete = gpus.length > 0 && snapshot?.gpu_status === "ok";
  const avg = complete && gpus.every((gpu) => isMetric(gpu.percent)) ? gpus.reduce((sum, gpu) => sum + gpu.percent, 0) / gpus.length : null;
  const memory = {};
  for (const key of ["used_bytes", "total_bytes"]) memory[key] = complete && gpus.every((gpu) => isMetric(gpu.memory?.[key])) ? gpus.reduce((sum, gpu) => sum + gpu.memory[key], 0) : null;
  return { percent: avg, memory };
}
export function freshness(snapshot, now) {
  const age = Math.max(0, (now - Date.parse(snapshot?.received_at)) / 1000);
  return { age, status: !Number.isFinite(age) || age > 60 ? "offline" : age > 15 ? "stale" : "online" };
}
// Insert explicit nulls for collection outages; never connect a gap as a healthy interval.
export function trendData(history, getter, step) {
  const result = [];
  let previous = null;
  for (const sample of history) {
    const time = Date.parse(sample.received_at);
    if (previous !== null && time - previous > Math.max(15000, step * 2000)) result.push([previous + 1, null]);
    const value = getter(sample);
    result.push([time, isMetric(value) ? value : null]);
    previous = time;
  }
  return result;
}
export function chartOption(history, metrics, step, start, end) {
  return {
    animation: false,
    color: ["#3569e8", "#6258d8", "#16845b", "#bd7b27", "#de7188", "#43a4b2"],
    tooltip: { trigger: "axis", valueFormatter: (value) => isMetric(value) ? `${value.toFixed(1)}%` : "未采集" },
    legend: { type: "scroll", bottom: 0, textStyle: { color: "#59677d", fontSize: 11 } },
    grid: { left: 42, right: 18, top: 20, bottom: 72 },
    xAxis: { type: "time", min: start, max: end, axisLabel: { color: "#8490a4", hideOverlap: true }, axisLine: { lineStyle: { color: "#e2e7ef" } } },
    yAxis: { type: "value", min: 0, max: 100, axisLabel: { formatter: "{value}%", color: "#8490a4" }, splitLine: { lineStyle: { color: "#edf0f5" } } },
    dataZoom: [{ type: "inside", filterMode: "none" }],
    series: metrics.map(([name, getter, dashed]) => ({ name, type: "line", showSymbol: false, connectNulls: false, lineStyle: { width: 2, type: dashed ? "dashed" : "solid" }, data: trendData(history, getter, step) })),
  };
}
