import { useRef, useState } from "react";
import { catalogTemplate, parseCatalog } from "./catalog.js";
import { fetchJson } from "../../api/http.js";
import "./catalog.css";

export function CatalogImport({ value, onChange, disabled }) {
  const input = useRef(null);
  const [filename, setFilename] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [query, setQuery] = useState("");
  const [page, setPage] = useState(1);
  const matches = (value?.classes || []).filter(c => `${c.id} ${c.name}`.toLowerCase().includes(query.toLowerCase()));
  const pages = Math.max(1, Math.ceil(matches.length / 6));
  function template() { const url = URL.createObjectURL(new Blob([JSON.stringify(catalogTemplate, null, 2)], { type: "application/json" })); const a = document.createElement("a"); a.href = url; a.download = "classes-template.json"; a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000); }
  async function choose(event) {
    const file = event.target.files?.[0]; event.target.value = "";
    if (!file) return;
    onChange(null); setFilename(""); setError(""); setLoading(true);
    try {
      if (!/\.json$/i.test(file.name) || file.size > 512 * 1024) throw new Error("仅支持不超过 512 KiB 的 .json 文件。");
      const parsed = parseCatalog(await file.text());
      const validated = await fetchJson("/api/annotation/catalog/validate", { method: "POST", body: parsed });
      onChange(validated); setFilename(file.name); setPage(1); setQuery("");
    } catch (e) { setError(e.message); } finally { setLoading(false); }
  }
  return <section className="ann-catalog"><div className="ann-catalog-heading"><div><span className="ann-eyebrow">CLASS CATALOG</span><h3>导入类别目录</h3></div><button type="button" className="btn" onClick={template}>下载 JSON 模板</button></div>
    <input ref={input} type="file" accept=".json,application/json" hidden aria-label="导入类别 JSON 文件" onChange={choose} disabled={disabled || loading} />
    <button type="button" className="ann-catalog-upload" disabled={disabled || loading} onClick={() => input.current?.click()}><strong>{loading ? "正在校验目录…" : filename || "选择 JSON 类别文件"}</strong><span>{value ? `${value.classes.length} 类 · 校验通过 · 点击更换` : "仅支持 .json · 最大 512 KiB · 2–1000 类"}</span></button>
    {error && <p role="alert" className="pub-warning">{error}</p>}
    {value ? <><label>查找类别<input aria-label="查找导入类别" value={query} onChange={e => { setQuery(e.target.value); setPage(1); }} placeholder="搜索编号或名称" /></label><div className="ann-catalog-table"><table><thead><tr><th>类别编号</th><th>类别名称</th></tr></thead><tbody>{matches.slice((page - 1) * 6, page * 6).map(c => <tr key={c.id}><td>{c.id}</td><td>{c.name}</td></tr>)}</tbody></table></div><footer className="ann-pagination"><button type="button" className="btn" aria-label="类别预览上一页" disabled={page === 1} onClick={() => setPage(page - 1)}>‹</button><span>{page} / {pages} · {matches.length} 类</span><button type="button" className="btn" aria-label="类别预览下一页" disabled={page >= pages} onClick={() => setPage(page + 1)}>›</button></footer></> : <details><summary>查看 JSON 格式</summary><pre>{JSON.stringify(catalogTemplate, null, 2)}</pre></details>}
    <small>编号和名称必须唯一。项目创建后目录固定；AI、人工确认、检索和数据发布统一读取这份 JSON 目录。</small>
  </section>;
}
