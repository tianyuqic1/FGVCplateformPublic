export const catalogTemplate = { schema_version: 1, classes: [{ id: "001", name: "Black-footed Albatross" }, { id: "002", name: "Laysan Albatross" }] };
export function parseCatalog(text) {
  if (new TextEncoder().encode(text).length > 512 * 1024) throw new Error("JSON 文件不能超过 512 KiB。");
  let data;
  try { data = JSON.parse(text.replace(/^\uFEFF/, "")); } catch { throw new Error("JSON 格式不正确，请检查引号、逗号和括号。"); }
  if (!data || data.schema_version !== 1 || !Array.isArray(data.classes) || Object.keys(data).some(k => !["schema_version", "classes"].includes(k))) throw new Error("请使用模板结构：schema_version 为 1，classes 为类别数组。");
  if (data.classes.length < 2 || data.classes.length > 1000) throw new Error("类别数量须为 2–1000。");
  const ids = new Set(), names = new Set();
  data.classes.forEach((c, i) => {
    if (!c || typeof c !== "object" || Object.keys(c).some(k => !["id", "name"].includes(k)) || [c.id, c.name].some(v => typeof v !== "string" || !v.trim() || v !== v.trim() || new TextEncoder().encode(v).length > 180)) throw new Error(`第 ${i + 1} 类须包含非空字符串 id 和 name，不能有首尾空格，各不超过 180 字节。`);
    if (ids.has(c.id) || names.has(c.name)) throw new Error(`第 ${i + 1} 类的编号或名称重复。`);
    ids.add(c.id); names.add(c.name);
  });
  return data;
}
