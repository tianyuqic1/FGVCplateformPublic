export function pathWithSearch(path, entries = []) {
  const params = new URLSearchParams();
  entries.forEach(([key, value]) => {
    if (value !== undefined && value !== null && String(value).trim()) {
      params.set(key, String(value).trim());
    }
  });
  const query = params.toString();
  return query ? `${path}?${query}` : path;
}
