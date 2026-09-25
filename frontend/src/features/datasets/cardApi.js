import { fetchJson } from "../../api/http.js";

export async function cardRequest(version, { method = "GET", body, generate = false, signal } = {}) {
  return fetchJson(`/api/dataset-versions/${encodeURIComponent(version)}/card${generate ? "/generate" : ""}`, {
    method,
    body,
    signal,
  });
}
