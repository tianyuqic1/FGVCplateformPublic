import { fetchJson } from "./http.js";

export function listHardwareNodes(signal) {
  return fetchJson("/api/hardware/nodes", { signal });
}
export function getHardwareNode(nodeId, window, signal) {
  return fetchJson(`/api/hardware/nodes/${encodeURIComponent(nodeId)}?window=${encodeURIComponent(window)}`, { signal });
}
