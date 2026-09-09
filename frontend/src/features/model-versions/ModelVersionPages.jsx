import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import {
  archiveModelVersion,
  compareModelVersions,
  promoteModelVersion,
  setModelAlias,
} from "../../api/modelVersions.js";
import { Icon } from "../../components/icons.jsx";
import { EChart } from "../../design-system/charts/EChart.jsx";
import {
  CodeValue,
  EmptyState,
  MetricTile,
  PageHeading,
  Panel,
  StatusBadge,
  formatNumber,
  formatPercent,
} from "../../design-system/components/Workbench.jsx";
import { useModelVersion, useModelVersions } from "./useModelVersions.js";

function backboneLabel(version) {
  if (version.backboneKey === "dinov3_vits16_lvd1689m") return "ViT-S/16 · DINOv3";
  if (version.backboneKey === "imagenet_vits16_augreg_in21k_ft_in1k") return "ViT-S/16 · ImageNet";
  if (version.backboneKey === "imagenet_resnet50_a1_in1k") return "ResNet-50 · ImageNet";
  return version.architecture || version.backboneKey || "未记录";
}

function AliasBadges({ aliases }) {
  if (!aliases.length) return <span className="fv-muted">—</span>;
  return <span className="fv-aliases">{aliases.map((alias) => <b key={alias}>{alias}</b>)}</span>;
}

export function ModelVersionsPage() {
  const navigate = useNavigate();
  const [filters, setFilters] = useState({ architecture: "", pretraining: "", status: "" });
  const { versions, loading, error } = useModelVersions(filters);
  const [selected, setSelected] = useState([]);
  const production = versions.filter((version) => version.status === "production").length;
  const champion = versions.filter((version) => version.aliases.includes("champion")).length;

  function toggle(id) {
    setSelected((current) => current.includes(id) ? current.filter((item) => item !== id) : current.length < 5 ? [...current, id] : current);
  }

  return (
    <div className="fv-feature-page">
      <PageHeading eyebrow="Model registry" title="模型版本" description="按 Dataset Version 和评估协议治理候选、生产版本与别名。" actions={<button className="primary-button" disabled={selected.length < 2} onClick={() => navigate(`/models/compare?ids=${selected.join(",")}`)}><Icon name="GitCompareArrows" size={15} />比较 {selected.length || ""}</button>} />
      <div className="fv-metric-grid"><MetricTile label="可见版本" value={versions.length} caption="当前筛选范围" /><MetricTile label="Production" value={production} caption="显式晋级版本" tone="success" /><MetricTile label="Champion" value={champion} caption="Dataset 作用域唯一" tone="success" /><MetricTile label="待验证" value={versions.filter((version) => ["candidate", "staging"].includes(version.status)).length} caption="candidate + staging" tone="running" /></div>
      <Panel eyebrow="Registry table" title="版本清单" aside={<div className="fv-filter-row"><select aria-label="架构筛选" value={filters.architecture} onChange={(event) => setFilters({ ...filters, architecture: event.target.value })}><option value="">全部架构</option><option value="vit_small_patch16">ViT-S/16</option><option value="resnet50">ResNet-50</option></select><select aria-label="预训练筛选" value={filters.pretraining} onChange={(event) => setFilters({ ...filters, pretraining: event.target.value })}><option value="">全部预训练</option><option value="DINOv3">DINOv3</option><option value="supervised">Supervised</option></select><select aria-label="状态筛选" value={filters.status} onChange={(event) => setFilters({ ...filters, status: event.target.value })}><option value="">全部状态</option><option value="candidate">Candidate</option><option value="staging">Staging</option><option value="production">Production</option><option value="archived">Archived</option></select></div>}>
        {loading && <EmptyState icon="LoaderCircle" title="正在读取 Model Registry" description="连接 Go Control Plane…" />}
        {!loading && error && <EmptyState icon="TriangleAlert" title="Model Registry 不可用" description={error.message} />}
        {!loading && !error && versions.length === 0 && <EmptyState icon="Boxes" title="尚无 Model Version" description="训练成功后，Control Plane 会创建不可变候选版本。" />}
        {versions.length > 0 && <div className="fv-table-wrap"><table className="fv-table"><thead><tr><th aria-label="选择比较" /><th>Model Version</th><th>Alias</th><th>Dataset Version</th><th>Backbone</th><th>状态</th><th className="numeric">Accuracy</th><th className="numeric">Macro F1</th><th>创建时间</th></tr></thead><tbody>{versions.map((version) => <tr key={version.id}><td><input aria-label={`选择 ${version.name}`} type="checkbox" checked={selected.includes(version.id)} onChange={() => toggle(version.id)} disabled={!selected.includes(version.id) && selected.length >= 5} /></td><td><Link to={`/models/${version.id}`}><strong title={version.name}>{version.name}</strong><CodeValue>{version.id}</CodeValue></Link></td><td><AliasBadges aliases={version.aliases} /></td><td><CodeValue>{version.datasetVersionKey}</CodeValue></td><td>{backboneLabel(version)}<small className="fv-cell-note">{version.pretrainingDataset || "预训练信息未记录"}</small></td><td><StatusBadge status={version.status} /></td><td className="numeric">{formatPercent(version.metrics.accuracy)}</td><td className="numeric">{formatNumber(version.metrics.macro_f1)}</td><td>{version.createdAt ? new Date(version.createdAt).toLocaleString("zh-CN") : "未记录"}</td></tr>)}</tbody></table></div>}
      </Panel>
    </div>
  );
}

export function ModelVersionDetailPage({ showToast }) {
  const [busy, setBusy] = useState(false);
  const { modelId } = useParams();
  const { version, loading, error, refresh } = useModelVersion(modelId);

  async function perform(kind) {
    if (busy) return;
    const reason = window.prompt("请输入本次操作原因（会写入审计记录）");
    if (!reason) return;
    setBusy(true);
    try {
      if (kind === "archive") await archiveModelVersion(modelId, reason);
      if (kind === "staging" || kind === "production") await promoteModelVersion(modelId, kind, reason);
      if (kind === "champion" || kind === "challenger") await setModelAlias(version.datasetId, kind, modelId, reason);
      showToast?.("Model Version 已更新");
      refresh();
    } catch (actionError) {
      showToast?.(actionError.message);
    } finally {
      setBusy(false);
    }
  }

  if (loading) return <EmptyState icon="LoaderCircle" title="正在读取 Model Version" description={modelId} />;
  if (error || !version) return <EmptyState icon="TriangleAlert" title="Model Version 不可用" description={error?.message ?? modelId} />;
  const artifactIntegrity = version.artifacts.length && version.artifacts.every((item) => item.sha256 && Number(item.size_bytes) >= 0);
  return <div className="fv-feature-page">
    <PageHeading eyebrow="Model version detail" title={version.name} description={<><CodeValue>{version.id}</CodeValue> · {backboneLabel(version)}</>} actions={<><AliasBadges aliases={version.aliases} /><StatusBadge status={version.status} /><Link className="secondary-button" to={`/models/compare?ids=${version.id}`}><Icon name="GitCompareArrows" size={15} />比较</Link></>} />
    <div className="fv-metric-grid"><MetricTile label="Accuracy" value={formatPercent(version.metrics.accuracy)} caption="同协议评估" tone="success" /><MetricTile label="Macro F1" value={formatNumber(version.metrics.macro_f1)} caption="未采集显示 N/A" /><MetricTile label="Coverage" value={formatPercent(version.metrics.expected_coverage)} caption="选择性预测" /><MetricTile label="Artifact Integrity" value={artifactIntegrity ? "已登记" : "待核验"} caption={`${version.artifacts.length} 个逻辑 Artifact`} tone={artifactIntegrity ? "success" : "danger"} /></div>
    <div className="fv-model-detail-grid">
      <div className="fv-side-stack">
        <Panel eyebrow="Lineage" title="两层逻辑归属"><dl className="fv-definition-list"><div><dt>Dataset</dt><dd>{version.datasetName}<br /><CodeValue>{version.datasetId}</CodeValue></dd></div><div><dt>Dataset Version</dt><dd><CodeValue>{version.datasetVersionKey}</CodeValue></dd></div><div><dt>Training Run</dt><dd><Link to={`/training/${version.trainingRunId}`}><CodeValue>{version.trainingRunId}</CodeValue></Link></dd></div><div><dt>Protocol Fingerprint</dt><dd><CodeValue>{version.evaluationContext.protocol_fingerprint}</CodeValue></dd></div></dl></Panel>
        <Panel eyebrow="Configuration" title="模型配置"><dl className="fv-definition-list"><div><dt>Backbone Key</dt><dd><CodeValue>{version.backboneKey}</CodeValue></dd></div><div><dt>Architecture</dt><dd>{version.architecture || "未采集"}</dd></div><div><dt>Pretraining</dt><dd>{version.pretrainingMethod || "未采集"} · {version.pretrainingDataset || "未采集"}</dd></div><div><dt>Input / Feature</dt><dd>{version.inputSize ?? "N/A"} px · {version.featureDim ?? "N/A"} dim</dd></div><div><dt>Parameters</dt><dd>{version.parameterCount ? `${(version.parameterCount / 1e6).toFixed(1)}M` : "未采集"}</dd></div><div><dt>Pooling / Head</dt><dd>{version.pooling || "N/A"} · {version.headType || "N/A"}</dd></div></dl></Panel>
      </div>
      <div className="fv-side-stack">
        <Panel eyebrow="Artifacts" title="内容寻址对象">{version.artifacts.length ? <div className="fv-artifact-list">{version.artifacts.map((item) => <div key={item.artifact_id}><span><Icon name="ShieldCheck" size={15} />{item.artifact_type}</span><CodeValue>{item.sha256}</CodeValue><small>{Number(item.size_bytes).toLocaleString()} bytes · {item.uri}</small></div>)}</div> : <EmptyState title="暂无 Artifact" description="该版本没有返回已登记对象。" />}</Panel>
        <Panel eyebrow="Governance" title="生命周期操作"><div className="fv-action-list">{["candidate", "staging"].includes(version.status) && <button className="primary-button" disabled={busy} onClick={() => perform("production")}>{busy ? "正在导出并校验 ONNX…" : "发布模型 · 导出 ONNX"}</button>}{version.status === "production" && <button disabled={busy} className="secondary-button" onClick={() => perform("champion")}>设为 Champion</button>}{["candidate", "staging"].includes(version.status) && <button disabled={busy} className="secondary-button" onClick={() => perform("challenger")}>设为 Challenger</button>}{version.status !== "archived" && <button disabled={busy} className="danger-button" onClick={() => perform("archive")}>归档版本</button>}</div><p className="fv-governance-note">{version.headType === "image_classifier_v2" ? "发布会合并 LoRA（如有），导出骨干 + 分类头的完整 .pt 和 ONNX；输入为预处理后的 RGB 图片张量。" : "这是历史分类头模型，发布仍导出分类头 .pt 和 ONNX；如需完整模型，请使用新训练流程重新训练。"} 验证输出一致性、SHA-256 和文件大小后保存到 MinIO，成功才标记 Production；原训练检查点保留。</p></Panel>
        <Panel eyebrow="Audit" title="审计事件">{version.events.length ? <ol className="fv-event-list">{version.events.map((event) => <li key={event.event_id}><i /><span><strong>{event.event_type}</strong><small>{event.actor} · {event.reason || "未填写"}</small></span><time>{new Date(event.created_at).toLocaleString("zh-CN")}</time></li>)}</ol> : <EmptyState title="暂无审计事件" description="后续状态与 alias 变更会记录在这里。" />}</Panel>
      </div>
    </div>
  </div>;
}

function comparisonChartOption(versions) {
  return {
    animationDuration: 280,
    grid: { left: 44, right: 20, top: 24, bottom: 58 },
    tooltip: { trigger: "axis", axisPointer: { type: "shadow" } },
    xAxis: { type: "category", data: versions.map((version) => version.name), axisLabel: { color: "#68748a", rotate: versions.length > 3 ? 18 : 0 } },
    yAxis: { type: "value", min: 0, max: 1, axisLabel: { formatter: (value) => `${Math.round(value * 100)}%` }, splitLine: { lineStyle: { color: "#e8ecf2" } } },
    series: [
      { name: "Accuracy", type: "bar", data: versions.map((version) => version.metrics.accuracy ?? null), itemStyle: { color: "#3569e8", borderRadius: [4, 4, 0, 0] } },
      { name: "Macro F1", type: "bar", data: versions.map((version) => version.metrics.macro_f1 ?? null), itemStyle: { color: "#6258d8", borderRadius: [4, 4, 0, 0] } },
    ],
  };
}

export function ModelComparisonPage() {
  const [searchParams] = useSearchParams();
  const ids = useMemo(() => [...new Set((searchParams.get("ids") ?? "").split(",").filter(Boolean))].slice(0, 5), [searchParams]);
  const [state, setState] = useState({ comparison: null, loading: ids.length >= 2, error: null });
  useEffect(() => {
    let active = true;
    if (ids.length < 2) {
      setState({ comparison: null, loading: false, error: null });
      return undefined;
    }
    compareModelVersions(ids).then((comparison) => active && setState({ comparison, loading: false, error: null })).catch((error) => active && setState({ comparison: null, loading: false, error }));
    return () => { active = false; };
  }, [ids]);
  if (ids.length < 2) return <EmptyState icon="GitCompareArrows" title="请选择 2–5 个版本" description="回到模型版本列表勾选可比较版本。" />;
  if (state.loading) return <EmptyState icon="LoaderCircle" title="正在校验可比性" description="检查 Dataset Version 与评估协议…" />;
  if (state.error || !state.comparison) return <EmptyState icon="TriangleAlert" title="比较不可用" description={state.error?.message} />;
  const { comparison } = state;
  const option = comparisonChartOption(comparison.versions);
  const configRows = ["backboneKey", "pretrainingDataset", "inputSize", "featureDim", "pooling", "headType"];
  return <div className="fv-feature-page">
    <PageHeading eyebrow="Model comparison" title="模型性能对比" description="先校验 Exact Scope 与协议，再展示可解释的指标和配置差异。" actions={<Link className="secondary-button" to="/models"><Icon name="ArrowLeft" size={15} />返回列表</Link>} />
    <div className={`fv-comparability ${comparison.comparable ? "is-comparable" : "is-warning"}`}><Icon name={comparison.comparable ? "BadgeCheck" : "TriangleAlert"} size={20} /><div><strong>{comparison.comparable ? "这些版本可直接比较" : "这些版本不可直接排名"}</strong><p>{comparison.comparable ? `Dataset Version ${comparison.datasetVersionId} · 协议 ${comparison.protocolFingerprint.slice(0, 12)}` : "Dataset Version 或评估协议不同，指标仅供查看。"}</p></div></div>
    {comparison.warnings.map((warning) => <div className="fv-inline-warning" key={warning.code}><CodeValue>{warning.code}</CodeValue>{warning.message}</div>)}
    <Panel eyebrow="Comparable metrics" title="Accuracy / Macro F1">{comparison.comparable ? <EChart option={option} className="fv-chart fv-chart--wide" ariaLabel="模型版本性能柱状对比图" /> : <EmptyState icon="ShieldAlert" title="已阻止跨范围排名" description="请选择相同 Dataset Version 且 protocol fingerprint 一致的版本。" />}</Panel>
    <Panel eyebrow="Metric table" title="指标明细"><div className="fv-table-wrap"><table className="fv-table"><thead><tr><th>指标</th>{comparison.versions.map((version) => <th key={version.id}>{version.name}</th>)}</tr></thead><tbody>{[["Accuracy", "accuracy", true], ["Macro F1", "macro_f1"], ["Coverage", "expected_coverage", true], ["Selective Risk", "expected_selective_risk", true], ["Inference Latency", "inference_latency_ms"]].map(([label, key, percent]) => <tr key={key}><th>{label}</th>{comparison.versions.map((version) => <td key={version.id}>{percent ? formatPercent(version.metrics[key]) : formatNumber(version.metrics[key])}</td>)}</tr>)}</tbody></table></div></Panel>
    <Panel eyebrow="Configuration diff" title="配置差异"><div className="fv-table-wrap"><table className="fv-table"><thead><tr><th>配置</th>{comparison.versions.map((version) => <th key={version.id}>{version.name}</th>)}</tr></thead><tbody>{configRows.map((key) => <tr key={key}><th>{key}</th>{comparison.versions.map((version) => <td key={version.id}>{version[key] ?? "未采集"}</td>)}</tr>)}</tbody></table></div></Panel>
  </div>;
}
