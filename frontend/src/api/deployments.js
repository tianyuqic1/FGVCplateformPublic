export const runtimeLabels = { onnx_cpu: "ONNX Runtime · CPU", tensorrt: "TensorRT · NVIDIA", ascend_acl: "Ascend ACL · 昇腾" };
export function deploymentLabel(row) { return `${runtimeLabels[row.runtime] || row.runtime} · ${row.precision} · ${row.target_profile}`; }
async function request(path, options = {}) {
  const base = (import.meta.env?.VITE_API_BASE_URL || "").replace(/\/$/, "");
  const response = await fetch(`${base}${path}`, { ...options, headers: { "Content-Type": "application/json" } });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : `部署请求失败 (${response.status})`);
  return data;
}
export const listDeployments = (id, signal) => request(`/api/model-versions/${encodeURIComponent(id)}/deployments/`, { signal });
export const createDeployment = (id, input) => request(`/api/model-versions/${encodeURIComponent(id)}/deployments/`, { method: "POST", body: JSON.stringify(input) });
export const retryDeployment = id => request(`/api/deployments/${encodeURIComponent(id)}/retry`, { method: "POST" });
