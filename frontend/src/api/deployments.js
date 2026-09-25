import { fetchJson } from "./http.js";

export const runtimeLabels = { onnx_cpu: "ONNX Runtime · CPU", tensorrt: "TensorRT · NVIDIA", ascend_acl: "Ascend ACL · 昇腾" };
export function deploymentLabel(row) { return `${runtimeLabels[row.runtime] || row.runtime} · ${row.precision} · ${row.target_profile}`; }
export const listDeployments = (id, signal) => fetchJson(`/api/model-versions/${encodeURIComponent(id)}/deployments/`, { signal });
export const createDeployment = (id, input) => fetchJson(`/api/model-versions/${encodeURIComponent(id)}/deployments/`, { method: "POST", body: input });
export const retryDeployment = id => fetchJson(`/api/deployments/${encodeURIComponent(id)}/retry`, { method: "POST" });
