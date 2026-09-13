import { useEffect, useState } from "react";
import { createDeployment, deploymentLabel, listDeployments, retryDeployment, runtimeLabels } from "../../api/deployments.js";
import { Panel } from "../../design-system/components/Workbench.jsx";
import "./model-deployments.css";

const statuses = { ready: "可推理", queued: "等待构建", building: "构建 / 校验中", failed: "构建失败" };
export function ModelDeployments({ version }) {
  const [data, setData] = useState({ deployments: [], targets: [] });
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [revision, refresh] = useState(0);
  const [runtime, setRuntime] = useState("");
  const [precision, setPrecision] = useState("FP16");
  useEffect(() => {
    const controller = new AbortController();
    let timer;
    async function load() {
      try {
        const next = await listDeployments(version.id, controller.signal);
        if (controller.signal.aborted) return;
        setData({ deployments: next.deployments || [], targets: next.targets || [] }); setError("");
        if (next.deployments?.some(row => ["queued", "building"].includes(row.status))) timer = setTimeout(load, 4000);
      } catch (e) { if (!controller.signal.aborted) setError(e.message); }
    }
    load();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [version.id, version.status, revision]);
  const selected = data.targets.find(row => row.runtime === runtime)?.runtime || data.targets[0]?.runtime || "";
  const sourceReady = data.deployments.some(row => row.runtime === "onnx_cpu" && row.precision === "FP32" && row.source.artifact_type === "full_onnx");
  async function perform(id) {
    if (busy) return;
    setBusy(true); setError("");
    try {
      if (id) await retryDeployment(id);
      else await createDeployment(version.id, { runtime: selected, precision, max_batch: 1, actor: "workbench-user" });
      refresh(value => value + 1);
    } catch (e) { setError(e.message); }
    finally { setBusy(false); }
  }
  return <Panel title="推理部署" eyebrow="Deployment variants" className="fv-deployment-panel">
    <div className="fv-deployments">
      <p>同一发布版本可部署到不同硬件，不增加模型版本号。只有构建完成、校验通过的产物可用于推理。</p>
      {version.status === "production" && <div className="fv-deployment-create">
        <label>目标硬件<select aria-label="部署目标硬件" value={selected} onChange={e => setRuntime(e.target.value)} disabled={!data.targets.length || busy}>{!data.targets.length && <option value="">尚未配置加速 Worker</option>}{data.targets.map(t => <option key={t.runtime} value={t.runtime}>{runtimeLabels[t.runtime]} · {t.target_profile}</option>)}</select></label>
        <label>运行精度<select aria-label="部署精度" value={precision} onChange={e => setPrecision(e.target.value)} disabled={busy}><option>FP32</option><option>FP16</option></select></label>
        <button className="primary-button" disabled={busy || !selected || !sourceReady} onClick={() => perform()}>创建加速部署</button>
      </div>}
      {version.status === "production" && !sourceReady && <p>加速部署需要先发布完整模型的 ONNX FP32；历史分类头部署继续使用 CPU。</p>}
      {error && <p role="alert">{error} <button className="secondary-button" onClick={() => refresh(value => value + 1)}>重新加载</button></p>}
      {!error && !data.deployments.length && <p>暂无部署产物，请先发布模型。</p>}
      <div className="fv-deployment-list">{data.deployments.map(row => <article key={row.id} className="fv-deployment-card">
        <div className="fv-deployment-title"><strong>{deploymentLabel(row)}</strong><span data-state={row.status}>{version.status === "production" ? statuses[row.status] || row.status : "模型未发布 · 不可推理"}</span></div>
        <small>批上限 {row.max_batch} · {row.compiled ? `${(row.compiled.size_bytes / 1048576).toFixed(1)} MiB` : "通用 ONNX"}</small>
        {row.compiled && <small>数值冒烟校验 {row.validation?.parity_passed ? "通过" : "未记录"} · 真实数据集精度尚需硬件验收</small>}
        {row.error && <p role="status">{row.error}</p>}
        <details><summary>产物与完整性</summary><code>SHA-256: {(row.compiled || row.source).sha256}</code><code>{(row.compiled || row.source).uri}</code>{row.compiled?.metadata?.runtime_fingerprint && <pre>{JSON.stringify(row.compiled.metadata.runtime_fingerprint, null, 2)}</pre>}</details>
        {row.status === "failed" && version.status === "production" && <button className="secondary-button" disabled={busy} onClick={() => perform(row.id)}>重新构建</button>}
      </article>)}</div>
    </div>
  </Panel>;
}
