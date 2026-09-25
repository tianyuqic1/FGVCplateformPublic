import { fetchJson } from "./http.js";

const routes = { dataset: "/datasets/", training: "/training/", model: "/models/", review: "/review/" };

export async function searchEntities(query, signal) {
  const payload = await fetchJson(`/api/search?q=${encodeURIComponent(query)}`, { signal });
  return (payload?.items ?? []).filter((item) => routes[item.kind] && item.id).map((item) => ({
    label: item.label,
    hint: item.hint,
    to: `${routes[item.kind]}${encodeURIComponent(item.id)}`,
    kind: item.kind,
  }));
}
