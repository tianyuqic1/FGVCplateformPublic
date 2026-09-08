import { useEffect, useRef, useState } from "react";
import { Panel, StatusChip } from "../../components/ui.jsx";
import { cardRequest } from "./cardApi.js";

export function DatasetCardPanel({ dataset, showToast }) {
  // Remount isolates late responses and unsaved edits when the dataset version changes.
  return <CardEditor key={dataset.datasetVersionId} version={dataset.datasetVersionId} showToast={showToast} />;
}

function CardEditor({ version, showToast }) {
  const [state, setState] = useState(null);
  const [card, setCard] = useState(null);
  const [revision, setRevision] = useState(0);
  const [draft, setDraft] = useState(null);
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const active = useRef(true);
  const requests = useRef(new Set());
  async function request(options = {}) {
    const controller = new AbortController(); requests.current.add(controller);
    try { return await cardRequest(version, { ...options, signal: controller.signal }); }
    finally { requests.current.delete(controller); }
  }
  function loadEditor(next) {
    setState(next); setCard(next.dataset_card); setRevision(next.revision); setDraft(null); setDirty(false); setError("");
  }
  useEffect(() => {
    active.current = true;
    request().then(next => { if (active.current) loadEditor(next); }).catch(err => { if (active.current && err.name !== "AbortError") setError(err.message); });
    return () => { active.current = false; requests.current.forEach(c => c.abort()); };
  }, [version]);
  const running = state?.generations?.some(g => g.status === "running");
  useEffect(() => {
    if (!running) return;
    const timer = setInterval(() => {
      request().then(next => { if (active.current) setState(next); }).catch(() => {});
    }, 3000);
    return () => clearInterval(timer);
  }, [running, version]);
  function edit(field, value) { setCard(current => ({ ...current, [field]: value })); setDirty(true); }
  async function generate() {
    setBusy("generating"); setError("");
    try {
      const { generation } = await request({ method: "POST", generate: true, body: { request_id: crypto.randomUUID() } });
      if (!active.current) return;
      if (generation.status !== "succeeded" || !generation.result?.dataset_card) throw new Error("AI 未返回完整说明，请稍后重试");
      if (generation.base_revision !== revision) throw new Error("说明修订已变化，请重新加载后再生成");
      setCard(generation.result.dataset_card); setDraft(generation.id); setDirty(true);
      setState(current => ({ ...current, generations: [generation, ...current.generations.filter(g => g.id !== generation.id)] }));
      showToast("AI 说明已填入编辑框，可直接修改后保存");
    } catch (err) {
      if (active.current) setError(err.message);
      try { const next = await request(); if (active.current) setState(next); } catch { /* keep editor */ }
    } finally { if (active.current) setBusy(""); }
  }
  async function save() {
    setBusy("saving"); setError("");
    try {
      const next = await request({ method: "PUT", body: { dataset_card: card, expected_revision: revision, ...(draft ? { draft_id: draft } : {}) } });
      if (active.current) { loadEditor(next); showToast("数据集说明已保存为新修订"); }
    } catch (err) { if (active.current) setError(err.message); }
    finally { if (active.current) setBusy(""); }
  }
  async function reload() {
    if (dirty && !window.confirm("重新加载会丢弃编辑区未保存的修改，是否继续？")) return;
    setBusy("loading");
    try { const next = await request(); if (active.current) { loadEditor(next); setError(""); } }
    catch (err) { if (active.current) setError(err.message); }
    finally { if (active.current) setBusy(""); }
  }
  return <Panel title="数据集说明 · AI 辅助" caption="AI 生成后直接填入下方编辑框，可人工修改后保存；重新生成会替换框内内容。仅发送类别、统计和已保存说明，不上传图片。" action={<StatusChip tone="info">修订 {revision}{dirty ? " · 未保存" : ""}</StatusChip>}>
    {error && <div role="alert" className="error-text section-gap-small">{error}</div>}
    {!card ? <button className="secondary-button" onClick={reload}>重新加载说明</button> : <>
      <div className="chips section-gap-small"><StatusChip tone="info">{state.facts.class_count} 类</StatusChip><StatusChip tone="info">{state.facts.sample_count} 张样本</StatusChip>{Object.entries(state.facts.split_totals || {}).map(([key, value]) => <StatusChip key={key} tone="neutral">{key}: {value}</StatusChip>)}</div>
      <fieldset disabled={!!busy} className="field-grid section-gap-small" style={{border:0,padding:0,marginInline:0}}>
        <div className="field"><label htmlFor="card-task">任务（只读）</label><input id="card-task" value={card.task} readOnly /></div>
        <div className="field"><label htmlFor="card-domain">领域</label><input id="card-domain" value={card.domain} onChange={e => edit("domain", e.target.value)} maxLength={1500} /></div>
        {[ ["summary", "摘要"], ["ood_policy", "OOD 范围建议（不自动生效）"], ["review_guidance", "复核指引"] ].map(([field,label]) => <div className="field full-span" key={field}><label htmlFor={`card-${field}`}>{label}</label><textarea id={`card-${field}`} value={card[field]} onChange={e => edit(field,e.target.value)} rows={5} maxLength={1500} /></div>)}
        <div className="field full-span"><label htmlFor="card-confusions">可能易混类别（每行一项）</label><textarea id="card-confusions" value={(card.known_confusions || []).join("\n")} onChange={e => edit("known_confusions",e.target.value.split("\n"))} rows={6} maxLength={6000} /></div>
      </fieldset>
      <div className="toolbar section-gap-small">
        <button className="secondary-button" disabled={!!busy || running || !state.facts.classes?.length} onClick={generate}>{busy === "generating" || running ? "AI 正在生成…" : "AI 生成说明"}</button>
        <button className="primary-button" disabled={!!busy || !dirty} onClick={save}>{busy === "saving" ? "保存中…" : "确认保存说明"}</button>
        <button className="secondary-button" disabled={!!busy} onClick={reload}>重新加载已保存版本</button>
      </div>
      {!state.facts.classes?.length && <p className="row-meta">此版本没有可用类别清单，请重新导入真实 ImageFolder 后再生成。</p>}
    </>}
  </Panel>;
}
