const base = (import.meta.env?.VITE_API_BASE_URL || "").replace(/\/$/, "");
export async function cardRequest(version, { method = "GET", body, generate = false, signal } = {}) {
  const response = await fetch(`${base}/api/dataset-versions/${encodeURIComponent(version)}/card${generate ? "/generate" : ""}`, {
    method, signal, headers: { Accept: "application/json", ...(body ? { "Content-Type": "application/json" } : {}) },
    body: body ? JSON.stringify(body) : undefined,
  });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload?.error?.message || `请求失败 (${response.status})`);
  return payload;
}
