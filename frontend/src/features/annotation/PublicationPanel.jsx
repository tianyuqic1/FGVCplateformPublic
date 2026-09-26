import { useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import {
  annotationTaskImageUrl,
  listPublicationCandidates,
  listPublicationHistory,
  listPublicationTargets,
  previewPublication,
  transitionPublication,
} from "../../api/annotation.js";
import { PaginatedSelect } from "../../design-system/components/PaginatedSelect.jsx";
import "./publication.css";
import { selectAllCandidates } from "./selectAllCandidates.js";
import { useAuth } from "../../auth/AuthContext.jsx";

const statuses = { preview: "待发布", queued: "排队中", building: "构建中", registering: "注册中", published: "已发布", failed: "发布失败", cancelled: "已取消" };
const sum = counts => Object.values(counts || {}).reduce((a, b) => a + Number(b), 0);

export function PublicationPanel({ projects, projectId, historyOnly = false }) {
  const { user } = useAuth();
  const canPublish = user?.role === "admin";
  const [params] = useSearchParams();
  const [source, setSource] = useState(params.get("source") === "feedback" ? "feedback" : "annotation");
  const [mode, setMode] = useState(params.get("source") === "feedback" ? "append" : "new");
  const [targets, setTargets] = useState([]);
  const [targetId, setTargetId] = useState(params.get("dataset") || "");
  const [baseId, setBaseId] = useState("");
  const [scopeProject, setScopeProject] = useState(projectId);
  const [name, setName] = useState("");
  const [split, setSplit] = useState({ train: 80, val: 10, test: 10, seed: 42 });
  const [trainOnly, setTrainOnly] = useState(true);
  const [notes, setNotes] = useState("");
  const [page, setPage] = useState(1);
  const [items, setItems] = useState({ items: [], total: 0 });
  const [selected, setSelected] = useState({});
  const [historyPage, setHistoryPage] = useState(1);
  const [history, setHistory] = useState({ items: [], total: 0 });
  const [preview, setPreview] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [revision, setRevision] = useState(0);
  const identity = useRef(crypto.randomUUID());
  const datasets = [...new Map(targets.map(t => [t.dataset_id, t.name])).entries()];
  const versions = targets.filter(t => t.dataset_id === targetId);
  const base = versions.find(t => t.version_id === baseId);
  const project = projects.find(p => p.id === scopeProject);
  const labels = Object.fromEntries((project?.classes || []).map(c => [c.id, c.name]));
  const chosen = Object.values(selected);
  const effectiveTrainOnly = source === "feedback" || (mode === "append" && trainOnly);
  const scope = source === "annotation" ? scopeProject : targetId;
  useEffect(() => { listPublicationTargets().then(d => setTargets(d.items)).catch(e => setError(e.message)); }, [revision]);
  useEffect(() => { if (!scopeProject && projectId) setScopeProject(projectId); }, [projectId, scopeProject]);
  useEffect(() => {
    let live = true; setItems({ items: [], total: 0 });
    if (scope && !historyOnly) listPublicationCandidates(source, scope, page).then(d => { if (live) { setItems(d); if (d.total && page > Math.ceil(d.total / 12)) setPage(Math.ceil(d.total / 12)); } }).catch(e => { if (live) setError(e.message); });
    return () => { live = false; };
  }, [source, scope, page, revision, historyOnly]);
  useEffect(() => {
    let live = true;
    const refresh = () => listPublicationHistory(historyPage).then(d => { if (live) setHistory(d); }).catch(e => { if (live) setError(e.message); });
    refresh(); const timer = setInterval(refresh, 4000);
    return () => { live = false; clearInterval(timer); };
  }, [historyPage, revision]);
  function invalidate() { identity.current = crypto.randomUUID(); setPreview(null); setError(""); }
  function resetScope() { invalidate(); setSelected({}); setPage(1); }
  function chooseDataset(id) { resetScope(); setTargetId(id); setBaseId(targets.find(t => t.dataset_id === id)?.version_id || ""); }
  function toggle(task) { invalidate(); if (source === "feedback" && !chosen.length && task.source_version_id) setBaseId(task.source_version_id); setSelected(old => { const next = { ...old }; if (next[task.id]) delete next[task.id]; else next[task.id] = task; return next; }); }
  function selectPage() { invalidate(); const origins = [...new Set(items.items.map(t => t.source_version_id).filter(Boolean))]; if (source === "feedback" && !chosen.length && origins.length === 1) setBaseId(origins[0]); setSelected(old => ({ ...old, ...Object.fromEntries(items.items.map(t => [t.id, t])) })); }
  async function run(fn) { setBusy(true); setError(""); try { await fn(); } catch (e) { setError(e.message); } finally { setBusy(false); } }
  async function selectAll() {
    await run(async () => {
      const all = await selectAllCandidates(p => listPublicationCandidates(source, scope, p, { signal: AbortSignal.timeout(15000) }));
      invalidate();
      const origins = [...new Set(all.map(t => t.source_version_id).filter(Boolean))];
      if (source === "feedback" && origins.length === 1) setBaseId(origins[0]);
      setSelected(Object.fromEntries(all.map(t => [t.id, t])));
      setNotice(`已全选当前来源的 ${all.length} 张待发布样本（包含其他页）。`);
    });
  }
  async function check() {
    await run(async () => {
      const data = await previewPublication({ id: identity.current, source, project_id: source === "annotation" ? scopeProject : "", dataset_id: mode === "append" ? targetId : "", base_id: mode === "append" ? baseId : "", name: mode === "new" ? name : "", ids: chosen.map(t => t.id), train_only: effectiveTrainOnly, split, notes });
      setPreview(data); setRevision(r => r + 1);
    });
  }
  async function transition(id, action) {
    await run(async () => { await transitionPublication(id, action); if (action !== "cancel") { setNotice("发布批次已进入后台，离开页面不影响执行。不会自动训练。"); setSelected({}); } setPreview(null); identity.current = crypto.randomUUID(); setRevision(r => r + 1); });
  }
  const validRatios = Number.isInteger(split.train) && split.train > 0 && [split.val, split.test, split.seed].every(Number.isInteger) && split.val >= 0 && split.test >= 0 && split.train + split.val + split.test === 100;
  const ready = chosen.length > 0 && chosen.length <= 1000 && validRatios && (mode === "new" ? name.trim() : baseId && targetId);
  return <div className="publication-panel">
    <header className="pub-heading"><div><span className="ann-eyebrow">DATASET RELEASE</span><h2>{historyOnly ? "发布记录" : "待发布区"}</h2><p>检查、发布后，人工确认的样本才会进入可追溯的数据集版本。</p></div><span className="pub-lock">已注册标签只读</span></header>
    {error && <div className="ann-alert" role="alert">{error}</div>}{notice && <div className="ann-notice" role="status">{notice}</div>}
    {!historyOnly && <fieldset className="pub-fields" disabled={busy}><div className="pub-layout"><section className="pub-card">
      <h3><span>01</span>选择已确认样本</h3>
      <div className="pub-options">{[["annotation", "标注成果"], ["feedback", "推理回流"]].map(([value, label]) => <button type="button" key={value} className={`btn ${source === value ? "primary" : ""}`} onClick={() => { resetScope(); setSource(value); if (value === "feedback") setMode("append"); }}>{label}</button>)}</div>
      {source === "annotation" ? <label>标注项目<PaginatedSelect aria-label="发布标注项目" value={scopeProject} onChange={e => { resetScope(); setScopeProject(e.target.value); }} options={projects.map(p => ({ value: p.id, label: p.name, detail: `${p.confirmed} 张已确认` }))} /></label> : <label>来源数据集<PaginatedSelect aria-label="回流来源数据集" value={targetId} onChange={e => chooseDataset(e.target.value)} options={datasets.map(([value, label]) => ({ value, label }))} placeholder="选择模型所属数据集" /></label>}
      <div className="pub-selection"><span>已选 {chosen.length} 张 · 可选 {items.total} 张</span><button type="button" className="btn" disabled={busy || !scope || !items.total} title="选择当前来源的全部待发布样本，包含其他页" onClick={selectAll}>全选</button><button type="button" className="btn" onClick={selectPage}>选择本页</button><button type="button" className="btn" onClick={() => { invalidate(); setSelected({}); }}>清空</button></div>
      <div className="pub-samples">{items.items.map(t => <label key={t.id} className={`pub-sample ${selected[t.id] ? "selected" : ""}`}><input type="checkbox" checked={!!selected[t.id]} onChange={() => toggle(t)} />{source === "annotation" && <img src={annotationTaskImageUrl(t.id)} alt="已确认样本" loading="lazy" />}<span><strong>{source === "annotation" ? labels[t.label] || t.label : t.label}</strong><small title={t.filename || t.review_id}>{t.filename || t.review_id}</small>{source === "feedback" && <small>来源 v{targets.find(v => v.version_id === t.source_version_id)?.number || "?"} · 人工已确认</small>}</span></label>)}{!items.items.length && <p className="ann-empty">没有可发布样本。请先完成人工确认，或选择其他来源。</p>}</div>
      <Pager page={page} total={items.total} onChange={setPage} prefix="待发布样本" />
    </section><section className="pub-card"><h3><span>02</span>发布去向与划分</h3>
      <div className="pub-options">{[["new", "新建数据集"], ["append", "追加已有数据集"]].map(([value, label]) => <button type="button" key={value} disabled={source === "feedback" && value === "new"} className={`btn ${mode === value ? "primary" : ""}`} onClick={() => { invalidate(); setMode(value); }}>{label}</button>)}</div>
      {mode === "new" ? <label>数据集名称<input aria-label="发布数据集名称" value={name} maxLength={120} onChange={e => { invalidate(); setName(e.target.value); }} placeholder="例如：鸟类识别 · 标注数据集" /></label> : <>
        {source !== "feedback" && <label>目标数据集<PaginatedSelect aria-label="发布目标数据集" value={targetId} onChange={e => chooseDataset(e.target.value)} options={datasets.map(([value, label]) => ({ value, label }))} placeholder="请选择目标" /></label>}
        <label>基准版本<PaginatedSelect aria-label="发布基准版本" value={baseId} onChange={e => { invalidate(); setBaseId(e.target.value); }} options={versions.map(t => ({ value: t.version_id, label: `${t.name} · v${t.number}`, detail: `训练 ${sum(t.split_counts?.train)} / 验证 ${sum(t.split_counts?.val)} / 测试 ${sum(t.split_counts?.test)}` }))} placeholder="选择追加的原版本" /></label>
        {base && <p className="pub-info">保留 v{base.number} 的图片、标签与划分，生成新版本。版本号在注册时按该数据集递增。</p>}
        {source === "feedback" && chosen.some(t => t.source_version_id !== baseId) && <p className="pub-warning">所选反馈存在不同来源版本。请确认当前基准；其他版本不会被自动合并。</p>}
        {source === "annotation" && <p className="pub-info">保留已确认类别名称。同名类别自动追加，不存在的类别加入新版本，无需重新指定标签。</p>}
      </>}
      {mode === "append" && source !== "feedback" && <label>新增样本的划分<PaginatedSelect aria-label="新增样本划分" value={trainOnly ? "train" : "split"} onChange={e => { invalidate(); setTrainOnly(e.target.value === "train"); }} options={[{ value: "train", label: "全部加入训练集（推荐）" }, { value: "split", label: "仅新增样本按比例划分" }]} /></label>}
      {effectiveTrainOnly ? <div className="pub-info">新样本 100% 加入训练集。历史验证集、测试集保持不变。</div> : <><div className="pub-ratios">{[["train", "训练集 %"], ["val", "验证集 %"], ["test", "测试集 %"]].map(([key, label]) => <label key={key}>{label}<input aria-label={label} type="number" min={key === "train" ? 1 : 0} max={100} step={1} value={split[key]} onChange={e => { invalidate(); setSplit(s => ({ ...s, [key]: e.target.value === "" ? "" : Number(e.target.value) })); }} /></label>)}</div><label>随机种子<input type="number" aria-label="划分随机种子" step={1} value={split.seed} onChange={e => { invalidate(); setSplit(s => ({ ...s, seed: e.target.value === "" ? "" : Number(e.target.value) })); }} /></label><small>按类别分层划分。同图只保留一份；少样本类别优先保证训练样本，并在检查结果中提示。</small>{!validRatios && <p className="pub-warning">比例须为整数、合计 100%，且训练集大于 0。</p>}</>}
      <label>发布说明<textarea rows={3} maxLength={2000} value={notes} onChange={e => { invalidate(); setNotes(e.target.value); }} placeholder="记录本次标注范围、来源或采集批次" /></label>
      <button type="button" className="btn primary" disabled={!ready || busy} onClick={check}>{busy ? "正在处理…" : "检查发布清单"}</button>
    </section></div></fieldset>}
    {preview && <ReleasePreview job={preview} busy={busy} canPublish={canPublish} onPublish={() => transition(preview.id, "publish")} />}
    <section className="pub-card pub-history"><h3>发布批次 <small>共 {history.total} 批</small></h3>{history.items.map(j => <article key={j.id}><div><strong>{j.plan.request.name || targets.find(t => t.dataset_id === j.plan.request.dataset_id)?.name || "已有数据集"}</strong><small>{j.plan.request.source === "feedback" ? "推理回流" : "标注成果"} · {j.plan.request.ids.length} 张 · {new Date(j.created_at).toLocaleString()}</small>{j.error && <p className="pub-warning">{j.error}</p>}{j.result?.version && <small>已注册 v{j.result.version.version_number} · 新增 {j.result.upload?.added_count || 0} 张</small>}</div><span className={`pub-state ${j.status}`}>{statuses[j.status]}</span><div className="pub-history-actions">
      {j.status === "preview" && <button className="btn" disabled={busy} onClick={() => setPreview(j)}>查看清单</button>}
      {canPublish && j.status === "failed" && <button className="btn" disabled={busy} onClick={() => transition(j.id, "retry")}>重试原批次</button>}
      {canPublish && ["preview", "failed"].includes(j.status) && <button className="btn" disabled={busy} onClick={() => transition(j.id, "cancel")}>取消批次</button>}
      {canPublish && j.result?.version && <><Link className="btn" to={`/datasets/${encodeURIComponent(j.result.version.dataset_id)}`}>查看数据集</Link>{j.result.version.readiness?.ready ? <Link className="btn primary" to={`/training?create=1&dataset_version_id=${encodeURIComponent(j.result.version.dataset_version_id)}`}>创建训练</Link> : <small>样本不足，暂不可训练</small>}</>}
    </div></article>)}{!history.items.length && <p className="ann-empty">暂无发布记录</p>}<Pager page={historyPage} total={history.total} onChange={setHistoryPage} prefix="发布记录" /></section>
  </div>;
}
function ReleasePreview({ job, busy, canPublish, onPublish }) {
  const summary = job.preview.changes;
  return <section className="pub-card pub-preview" aria-label="发布检查结果"><h3><span>03</span>检查完成 · 确认发布</h3><p>{job.preview.new_dataset ? `新建「${job.plan.request.name}」· 首版 v1` : `追加到「${job.preview.base_name}」· 基于 v${job.preview.base_number}`}</p><div className="pub-totals"><span>所选<strong>{job.preview.selected_count}</strong></span><span>新增<strong>{summary.added_count ?? "后台确认"}</strong></span><span>去重<strong>{summary.duplicate_count ?? "后台确认"}</strong></span></div>
    {summary.existing_class_additions && <p>已有类别补充：{Object.entries(summary.existing_class_additions).map(([label, count]) => `${label} +${count} 张`).join("；") || "无"}</p>}
    {summary.new_class_additions && <p>新增类别：{Object.entries(summary.new_class_additions).map(([label, count]) => `${label}（${count} 张）`).join("；") || "无"}</p>}
    {summary.split_counts && <div className="pub-table-wrap"><table><thead><tr><th>合并后的类别</th><th>训练集</th><th>验证集</th><th>测试集</th></tr></thead><tbody>{[...new Set(Object.values(summary.split_counts).flatMap(c => Object.keys(c)))].sort().map(label => <tr key={label}><td>{label}</td>{["train", "val", "test"].map(s => <td key={s}>{summary.split_counts[s]?.[label] || 0}</td>)}</tr>)}</tbody><tfoot><tr><th>合计</th>{["train", "val", "test"].map(s => <th key={s}>{sum(summary.split_counts[s])}</th>)}</tr></tfoot></table></div>}
    {summary.warnings?.map((warning, i) => <p key={i} className="pub-warning">{warning}</p>)}{job.plan.request.notes && <p>说明：{job.plan.request.notes}</p>}<p>本批样本与标签已锁定快照。后台注册成功后才能创建训练；不会修改原版本或自动启动训练。</p>{canPublish ? <button type="button" className="btn primary" disabled={busy || job.status !== "preview"} onClick={onPublish}>确认发布数据集版本</button> : <p className="pub-info">清单已保存，请联系平台管理员确认发布数据集版本。</p>}
  </section>;
}
function Pager({ page, total, onChange, prefix }) { const pages = Math.max(1, Math.ceil(total / 12)); return <footer className="ann-pagination"><button className="btn" aria-label={`${prefix}上一页`} disabled={page <= 1} onClick={() => onChange(page - 1)}>‹</button><span>{page} / {pages} · 每页 12 条</span><button className="btn" aria-label={`${prefix}下一页`} disabled={page >= pages} onClick={() => onChange(page + 1)}>›</button></footer>; }
