import { useEffect, useRef, useState } from "react";
import { getModelVersion, promoteModelVersion } from "../../api/modelVersions.js";

export function PublishDialog({ version, onClose, onPublished }) {
  const dialog = useRef(null);
  const [preview, setPreview] = useState(null);
  const [precision, setPrecision] = useState("FP32");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    dialog.current?.showModal();
    let active = true;
    getModelVersion(version.id).then(v => active && setPreview(v)).catch(e => active && setError(e.message));
    return () => { active = false; };
  }, [version.id]);
  const exported = preview?.releaseVersion && preview.artifacts.some(a =>
    ["full_onnx", "head_onnx"].includes(a.artifact_type) && a.metadata?.model_version_id === version.id && (a.metadata?.precision || "FP32") === precision);
  async function publish(event) {
    event.preventDefault();
    if (busy || !preview || exported || !reason.trim()) return;
    setBusy(true); setError("");
    try {
      const result = await promoteModelVersion(version.id, "production", reason.trim(), "local-user", precision);
      onPublished(result);
    } catch (e) { setError(e.message); setBusy(false); }
  }
  return <dialog ref={dialog} className="fv-publish-dialog" aria-labelledby="publish-title" onCancel={event => { event.preventDefault(); if (!busy) onClose(); }}>
    <form onSubmit={publish}>
      <header><div><small>MODEL RELEASE</small><h2 id="publish-title">发布模型</h2></div><button type="button" className="secondary-button" disabled={busy} onClick={onClose} aria-label="关闭发布弹窗">关闭</button></header>
      <div className="fv-release-preview"><span>{version.datasetName} · {version.name}</span><strong>{preview?.nextReleaseVersion || "正在计算版本…"}</strong><p>{preview?.nextReleaseReason}</p><small>预计版本以成功发布时为准；并发发布可能使版本号递增。</small></div>
      <p className="fv-publish-hint">{version.architecture || version.backboneKey} · 训练数据 {version.datasetVersionNumber ? `v${version.datasetVersionNumber}` : "历史快照"} · Accuracy {version.metrics?.accuracy != null && Number.isFinite(Number(version.metrics.accuracy)) ? `${(Number(version.metrics.accuracy) * 100).toFixed(2)}%` : "未采集"}。不同测试集或评估协议的指标不直接比较。</p>
      <fieldset disabled={busy}><legend>部署精度</legend><div className="fv-precision-options">{["FP32", "FP16"].map(value => <label key={value} className={precision === value ? "is-selected" : ""}>
        <input type="radio" name="precision" value={value} checked={precision === value} onChange={() => setPrecision(value)} /><span><strong>{value}</strong><small>{value === "FP32" ? "标准精度 · 默认选择" : "半精度 · 减少模型体积"}</small></span>
      </label>)}</div></fieldset>
      <p className="fv-publish-hint">{version.headType === "image_classifier_v2" ? "合并 LoRA（如有），导出完整模型 PT + ONNX。" : "历史模型只导出分类头 PT + ONNX。"} FP16 保留必要的 FP32 算子与输入输出；通过数值、SHA-256 和大小校验后保存至 MinIO。</p>
      <label className="fv-publish-reason">发布说明<textarea required maxLength={1000} rows={3} value={reason} disabled={busy} onChange={e => setReason(e.target.value)} placeholder="例如：完成验证，发布用于本地推理" /></label>
      {exported && <p role="status">此版本已发布 {precision}，无需重复导出。可以选择另一种精度。</p>}
      {error && <p role="alert" className="fv-publish-error">{error}</p>}
      {busy && <p role="status" aria-live="polite">正在导出、校验并上传，请勿关闭页面。成功前不会开放新增权重。</p>}
      <footer><button type="button" className="secondary-button" disabled={busy} onClick={onClose}>取消</button><button className="primary-button" disabled={busy || !preview || exported || !reason.trim()}>{busy ? "正在发布…" : `确认发布 ${precision}`}</button></footer>
    </form>
  </dialog>;
}
