import { useCallback, useEffect, useRef, useState } from "react";
import { apiBaseUrl, apiErrorFromResponse, fetchJson } from "../../api/http.js";
import { PaginatedSelect } from "../../design-system/components/PaginatedSelect.jsx";
import { Icon } from "../../components/icons.jsx";
import "./annotation.css";
import { useSearchParams } from "react-router-dom";
import { PublicationPanel } from "./PublicationPanel.jsx";
import { CatalogImport } from "./CatalogImport.jsx";

const ROOT = "/api/annotation";
const states = { pending: "待标注", queued: "排队中", running: "AI 分析中", suggested: "待确认", confirmed: "已确认", failed: "建议不完整", unknown: "调用结果未知" };
const methods = { A: "原图直读", B: "工具工作流", C: "工作流 + 图像检索", D: "工作流 + 图文检索" };
const picture = id => `${apiBaseUrl()}${ROOT}/tasks/${id}/image`;

export function AnnotationPage() {
  const [params, setParams] = useSearchParams();
  const tab = params.get("tab") || "work";
  const [projects, setProjects] = useState([]);
  const [projectId, setProjectId] = useState("");
  const [view, setView] = useState({ items: [], total: 0 });
  const [page, setPage] = useState(1);
  const [filter, setFilter] = useState("");
  const [selectedId, setSelectedId] = useState("");
  const [label, setLabel] = useState("");
  const [actor, setActor] = useState("本地标注员");
  const [consent, setConsent] = useState(false);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [worker, setWorker] = useState({ online: false });
  const [creating, setCreating] = useState(false);
  const [draft, setDraft] = useState({ name: "", method: "D", domain: "general" });
  const [catalog, setCatalog] = useState(null);
  const [queueSummary, setQueueSummary] = useState({ pending: 0, queued: 0, running: 0 });
  const [batch, setBatch] = useState(null);
  const fileInput = useRef(null);
  const generation = useRef(0);
  const project = projects.find(p => p.id === projectId);
  const selected = view.items.find(t => t.id === selectedId);
  const names = Object.fromEntries((project?.classes ?? []).map(c => [c.id, c.name]));
  const pageCount = Math.max(1, Math.ceil(view.total / 12));

  const loadProjects = useCallback(async () => {
    const data = await fetchJson(`${ROOT}/projects`);
    setProjects(data.items);
    setProjectId(current => current || data.items[0]?.id || "");
  }, []);
  const reload = useCallback(async () => {
    if (!projectId) return;
    const ticket = ++generation.current;
    const [data, summary] = await Promise.all([fetchJson(`${ROOT}/projects/${projectId}/tasks?page=${page}&status=${filter}`), fetchJson(`${ROOT}/projects/${projectId}/queue-summary`)]);
    if (ticket !== generation.current) return;
    setView(data);
    setQueueSummary(summary);
    if (data.total > 0 && page > Math.ceil(data.total / 12)) setPage(Math.ceil(data.total / 12));
    setSelectedId(current => data.items.some(t => t.id === current) ? current : (data.items.find(t => t.status === "suggested")?.id || data.items.find(t => t.status === "pending")?.id || data.items[0]?.id || ""));
  }, [projectId, page, filter]);
  useEffect(() => { loadProjects().catch(e => setError(e.message)); }, [loadProjects]);
  useEffect(() => {
    let stopped = false;
    const refresh = async () => {
      try { await Promise.all([reload(), loadProjects()]); const status = await fetchJson(`${ROOT}/status`); if (!stopped) setWorker(status); }
      catch (e) { if (!stopped) setError(e.message); }
    };
    refresh();
    const timer = setInterval(refresh, 5000);
    return () => { stopped = true; generation.current++; clearInterval(timer); };
  }, [reload, loadProjects]);
  useEffect(() => { setLabel(""); }, [selectedId]);

  async function act(name, fn) {
    setBusy(name); setError(""); setNotice("");
    try { await fn(); await Promise.all([reload(), loadProjects()]); }
    catch (e) { setError(e.message); }
    finally { setBusy(""); }
  }
  function changeProject(id) { generation.current++; setProjectId(id); setPage(1); setSelectedId(""); setView({ items: [], total: 0 }); setConsent(false); setBatch(null); setQueueSummary({pending:0,queued:0,running:0}); }
  async function create(event) {
    event.preventDefault();
    if (!catalog) { setError("请先导入并校验 JSON 类别目录。"); return; }
    await act("创建项目", async () => {
      const p = await fetchJson(`${ROOT}/projects`, { method: "POST", body: { name: draft.name, catalog, method: draft.method, domain: draft.domain } });
      changeProject(p.id); setCreating(false); setCatalog(null); setDraft({ name: "", method: "D", domain: "general" });
      setNotice("项目已创建，上传图片后即可开始。不会自动发送图片到大模型。");
    });
  }
  async function upload(event) {
    const files = [...(event.target.files ?? [])]; event.target.value = "";
    if (!files.length) return;
    if (files.length > 100 || files.some(f => f.size > 20 * 1024 * 1024)) { setError("每批最多 100 张，单张不超过 20 MiB。"); return; }
    await act("上传图片", async () => {
      let count = 0;
      for (const file of files) {
        const form = new FormData(); form.append("image", file);
        const path = `${ROOT}/projects/${projectId}/images`;
        const response = await fetch(`${apiBaseUrl()}${path}`, { method: "POST", body: form });
        if (!response.ok) {
          throw await apiErrorFromResponse(response, {
            method: "POST",
            path,
            fallback: `已处理 ${count} 张；${file.name} 上传失败。已上传的图片会保留`,
          });
        }
        count++; setBusy(`上传 ${count}/${files.length}`);
      }
      setNotice(`已处理 ${count} 张图片；相同图片自动去重。`); setPage(1);
    });
  }
  async function queue(ids) {
    await act("加入 AI 队列", async () => {
      const data = await fetchJson(`${ROOT}/projects/${projectId}/queue`, { method: "POST", body: { ids, allow_remote: consent } });
      setNotice(`${data.queued} 张已进入后台队列，可以离开页面。`);
    });
  }
  async function prepareBatch() { await act("检查批量范围", async () => { const snapshot = await fetchJson(`${ROOT}/projects/${projectId}/queue-summary`); setBatch({ ...snapshot, projectId }); }); }
  async function queueAll() {
    await act("提交批量分析", async () => {
      const data = await fetchJson(`${ROOT}/projects/${batch.projectId}/queue-all`, { method: "POST", body: { through_seq: batch.through_seq, allow_remote: consent } });
      setBatch(null); setNotice(`${data.queued} 张已进入后台批量分析；按低资源单并发处理，无需停留在本页。`);
    });
  }
  async function confirm() {
    const targetId = selected.id;
    await act("保存标注", async () => {
      await fetchJson(`${ROOT}/tasks/${targetId}/confirm`, { method: "POST", body: { label, actor } });
      setNotice("人工标签已保存，确认样本将异步写入检索库。"); setLabel("");
    });
  }
  const locked = busy || !selected || ["queued", "running", "confirmed"].includes(selected.status);
  return <div className="annotation-page">
    <header className="ann-hero"><div><span className="ann-eyebrow">HUMAN IN THE LOOP</span><h1>AI 标注工作区</h1><p>让模型提供候选，让人决定答案。每一次确认，都成为下一次判断的参考。</p></div>
      <span className={`ann-service ${worker.online ? "online" : ""}`}><i />{worker.online ? "低资源 Worker 在线" : "Worker 离线 · 可人工标注"}</span></header>
    <nav className="ann-tabs" aria-label="标注工作区导航">{[["work", "标注工作区"], ["publish", "待发布区"], ["history", "发布记录"]].map(([value, label]) => <button key={value} className={`btn ${tab === value ? "primary" : ""}`} onClick={() => { const next = new URLSearchParams(params); next.set("tab", value); setParams(next); }}>{label}</button>)}</nav>
    {tab !== "work" ? <PublicationPanel key={tab} projects={projects} projectId={projectId} historyOnly={tab === "history"} /> : <>
    {error && <div className="ann-alert" role="alert">{error}<button type="button" onClick={() => setError("")} aria-label="关闭错误">×</button></div>}
    {notice && <div className="ann-notice" role="status">{notice}</div>}
    <section className="ann-project-bar">
      <div className="ann-project-select"><label>标注项目</label><PaginatedSelect aria-label="标注项目" value={projectId} disabled={!!busy} onChange={e => changeProject(e.target.value)} options={projects.map(p => ({ value: p.id, label: p.name, detail: `${p.classes.length} 类 · ${p.confirmed}/${p.total} 已确认 · ${methods[p.method]}` }))} placeholder="创建第一个标注项目" /></div>
      {project && <div className="ann-project-stats"><span><strong>{project.total}</strong> 张图片</span><span><strong>{project.confirmed}</strong> 已确认</span><span><strong>{project.indexed}</strong> 已入库</span></div>}
      <button className="btn" disabled={!!busy} onClick={() => setCreating(!creating)}><Icon name="Plus" size={16} />新建项目</button>
      {project && <><button className="btn primary" disabled={!!busy} onClick={() => fileInput.current?.click()}><Icon name="Upload" size={16} />上传图片</button><a className="btn" href={`${apiBaseUrl()}${ROOT}/projects/${projectId}/export`} download>导出标注</a><a className="btn" href={`${apiBaseUrl()}${ROOT}/projects/${projectId}/catalog`} download>类别 JSON</a></>}
      <input ref={fileInput} type="file" hidden multiple accept="image/jpeg,image/png" onChange={upload} />
    </section>
    {creating && <form className="ann-create" onSubmit={create}>
      <div><h2>新建标注项目</h2><p>项目内独立保存类别、标签和检索记忆。类别表创建后固定，避免标签串库。</p>
        <label>项目名称<input required maxLength={180} value={draft.name} onChange={e => setDraft({ ...draft, name: e.target.value })} placeholder="例如：鸟类细粒度标注 · 第一批" /></label>
        <label>AI 方案<PaginatedSelect aria-label="AI 方案" value={draft.method} onChange={e => setDraft({ ...draft, method: e.target.value })} options={Object.entries(methods).map(([value, label]) => ({ value, label: `${value} · ${label}` }))} /></label>
        <label>图像领域<PaginatedSelect aria-label="图像领域" value={draft.domain} onChange={e => setDraft({ ...draft, domain: e.target.value })} options={[{ value: "general", label: "通用图像" }, { value: "birds", label: "鸟类" }, { value: "cars", label: "汽车" }]} /></label>
      </div>
      <div><CatalogImport value={catalog} onChange={setCatalog} disabled={!!busy} /><div className="ann-actions"><button type="button" className="btn" onClick={() => setCreating(false)}>取消</button><button className="btn primary" disabled={!!busy || !catalog}>创建项目</button></div></div>
    </form>}
    {project && <div className="ann-policy"><span><Icon name="Cpu" size={15} />{methods[project.method]} · 单并发 · SAM 按需运行</span><span>每类 ≥ 10 张人工确认且入库成功后参与检索；增强图不入库。</span></div>}
    {project && <section className="ann-batch-bar"><div><strong>批量 AI 分析</strong><small>待分析 {queueSummary.pending} 张 · 排队 {queueSummary.queued} 张 · 运行 {queueSummary.running} 张 · 跨全部分页</small></div><label className="ann-consent"><input type="checkbox" checked={consent} disabled={!!busy} onChange={e => setConsent(e.target.checked)} />允许远程图片分析及可能产生的费用</label><button className="btn primary" disabled={!!busy || !consent || !worker.online || !queueSummary.pending} onClick={prepareBatch}>分析全部待标图片</button></section>}
    {batch && <section className="ann-batch-confirm" role="dialog" aria-label="确认批量分析"><h3>确认分析 {batch.pending} 张图片？</h3><p>范围为当前项目所有分页中的待标图片。已确认、已有建议、失败或结果未知的任务不会重新调用。确认后新上传的图片不进入本批；后台仍保持单并发低资源运行，可能产生大模型调用费用。</p><div className="ann-actions"><button className="btn" disabled={!!busy} onClick={() => setBatch(null)}>取消批量分析</button><button className="btn primary" disabled={!!busy || !consent || !batch.pending} onClick={queueAll}>确认加入分析队列</button></div></section>}
    {project && <section className="ann-workspace">
      <aside className="ann-queue"><div className="ann-queue-head"><h2>图片队列 <small>{view.total}</small></h2><PaginatedSelect aria-label="标注状态" value={filter} onChange={e => { setFilter(e.target.value); setPage(1); }} options={[{ value: "", label: "全部状态" }, ...Object.entries(states).map(([value, label]) => ({ value, label }))]} /></div>
        <div className="ann-thumbnails">{view.items.map(task => <button key={task.id} className={`ann-thumb ${selectedId === task.id ? "active" : ""}`} onClick={() => setSelectedId(task.id)} aria-pressed={selectedId === task.id}>
          <img src={picture(task.id)} alt="待标注样本缩略图" loading="lazy" /><span><strong title={task.filename}>{task.filename}</strong><small className={`ann-state ${task.status}`}>{states[task.status]}</small>{task.status === "confirmed" && <small>{names[task.label]} · {task.indexed ? "已入库" : "待入库"}</small>}</span>
        </button>)}{!view.items.length && <p className="ann-empty">{filter ? "当前筛选没有图片" : "上传 JPG / PNG 开始标注"}</p>}</div>
        <footer className="ann-pagination"><button className="btn" disabled={page <= 1} onClick={() => setPage(page - 1)} aria-label="图片上一页">‹</button><span>{page} / {pageCount} · 每页 12 张</span><button className="btn" disabled={page >= pageCount} onClick={() => setPage(page + 1)} aria-label="图片下一页">›</button></footer>
      </aside>
      <main className="ann-canvas"><div className="ann-canvas-head"><div><span className="ann-eyebrow">ORIGINAL IMAGE</span><h2>{selected ? selected.filename : "选择一张图片"}</h2></div>{selected && <span className={`ann-state ${selected.status}`}>{states[selected.status]}</span>}</div>
        <div className="ann-image-stage">{selected ? <img key={selected.id} src={picture(selected.id)} alt="原始待标注图像" /> : <div className="ann-empty"><Icon name="ImageUp" size={40} /><p>原图始终作为判断依据</p></div>}</div>
        <div className="ann-ai-controls"><label className="ann-consent"><input type="checkbox" checked={consent} onChange={e => setConsent(e.target.checked)} />允许将所选图片、类别目录及检索参考图发送至配置的大模型服务（可能产生费用）</label><div className="ann-actions"><button className="btn primary" disabled={!!busy || !worker.online || !consent || selected?.status !== "pending"} onClick={() => queue([selected.id])}>分析当前图片</button><button className="btn" disabled={!!busy || !worker.online || !consent || !view.items.some(t => t.status === "pending")} onClick={() => queue(view.items.filter(t => t.status === "pending").map(t => t.id))}>分析本页待标图片</button>{busy && <small role="status">{busy}…</small>}</div></div>
        {selected && <details className="ann-trace"><summary>工作流记录与降级信息</summary><dl><dt>处理路线</dt><dd>{selected.result.route || "尚未完成"}</dd><dt>检索参考</dt><dd>{selected.result.reference_count ?? 0} 张 · {selected.result.retrieval_enabled ? "检索已启用" : "未启用 / 尚无成熟类别"}</dd><dt>增强工具</dt><dd>{selected.result.tool?.tool || "无"} · {selected.result.tool?.status || "未执行"}</dd><dt>远程调用耗时</dt><dd>{selected.result.remote_seconds ?? "—"} 秒</dd></dl>{selected.result.warnings?.length > 0 && <p>{selected.result.warnings.join(" · ")}</p>}{selected.error && <p>{selected.error}</p>}{selected.index_error && <p>{selected.index_error}</p>}</details>}
      </main>
      <aside className="ann-inspector"><div className="ann-inspector-head"><span className="ann-eyebrow">AI SUGGESTIONS</span><h2>Top-{Math.min(10, project.classes.length)} 候选</h2><p>候选按相对支持度排序，不是置信概率。</p></div>
        <div className="ann-candidates">{(selected?.result.class_ids ?? []).map((id, i) => <button key={id} type="button" disabled={!!locked} className={label === id ? "chosen" : ""} onClick={() => setLabel(id)}><span>{String(i + 1).padStart(2, "0")}</span><strong>{names[id] || id}</strong>{label === id && <Icon name="Check" size={16} />}</button>)}{!selected?.result.class_ids?.length && <p className="ann-empty">{selected?.status === "running" ? "AI 正在分析，页面会自动更新…" : "尚无 AI 建议。你也可以直接从完整目录选择类别。"}</p>}</div>
        <div className="ann-confirm"><label>最终类别<PaginatedSelect aria-label="最终类别" value={selected?.status === "confirmed" ? selected.label : label} disabled={!!locked} onChange={e => setLabel(e.target.value)} options={project.classes.map(c => ({ value: c.id, label: c.name, detail: c.id }))} placeholder="搜索完整类别目录" /></label><label>标注人<input value={actor} maxLength={120} onChange={e => setActor(e.target.value)} disabled={!!busy} /></label><button className="btn primary" disabled={!!locked || !label || !actor.trim()} onClick={confirm}><Icon name="CheckCircle2" size={16} />{selected?.status === "confirmed" ? "人工标签已确认" : "确认标注"}</button><small>确认后保存标签并异步入库。类别不在 Top-10 内，也可以手动选择。首版已确认标签不直接覆盖。</small></div>
      </aside>
    </section>}
    {!project && !creating && <div className="ann-welcome"><Icon name="ScanSearch" size={48} /><h2>从一份类别目录开始</h2><p>新建项目 → 上传图片 → AI 建议 → 人工确认 → 检索记忆</p><button className="btn primary" onClick={() => setCreating(true)}>创建第一个项目</button></div>}
    </>}
  </div>;
}
