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
import "./model-detail.css";
import { PublishDialog } from "./PublishDialog.jsx";
import { ModelGroups } from "./ModelGroups.jsx";
import { ModelDeployments } from "./ModelDeployments.jsx";
import { modelScope, toggleModelSelection } from "./modelGroups.js";

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
  const { versions, loading, error, refresh } = useModelVersions(filters);
  const [publishing, setPublishing] = useState(null);
  const [notice, setNotice] = useState("");
  const [selected, setSelected] = useState([]);
  const production = versions.filter((version) => version.status === "production").length;
  const champion = versions.filter((version) => version.aliases.includes("champion")).length;

  useEffect(() => { setSelected([]); }, [filters, versions]);
  function toggle(version) {
    setSelected(current => toggleModelSelection(current, version, versions));
  }

  return (
    <div className="fv-feature-page">
      <PageHeading eyebrow="Model registry" title="模型版本" description="按数据集管理发布版本，保留训练数据与评估记录。" actions={<button className="primary-button" disabled={selected.length < 2} onClick={() => navigate(`/models/compare?ids=${selected.join(",")}`)}><Icon name="GitCompareArrows" size={15} />比较 {selected.length || ""}</button>} />
      <div className="fv-metric-grid"><MetricTile label="可见版本" value={versions.length} caption="当前筛选范围" /><MetricTile label="Production" value={production} caption="显式晋级版本" tone="success" /><MetricTile label="Champion" value={champion} caption="Dataset 作用域唯一" tone="success" /><MetricTile label="待验证" value={versions.filter((version) => ["candidate", "staging"].includes(version.status)).length} caption="candidate + staging" tone="running" /></div>
      <Panel eyebrow="Registry table" title="版本清单" aside={<div className="fv-filter-row"><select aria-label="架构筛选" value={filters.architecture} onChange={(event) => setFilters({ ...filters, architecture: event.target.value })}><option value="">全部架构</option><option value="vit_small_patch16">ViT-S/16</option><option value="resnet50">ResNet-50</option></select><select aria-label="预训练筛选" value={filters.pretraining} onChange={(event) => setFilters({ ...filters, pretraining: event.target.value })}><option value="">全部预训练</option><option value="DINOv3">DINOv3</option><option value="supervised">Supervised</option></select><select aria-label="状态筛选" value={filters.status} onChange={(event) => setFilters({ ...filters, status: event.target.value })}><option value="">全部状态</option><option value="candidate">Candidate</option><option value="staging">Staging</option><option value="production">Production</option><option value="archived">Archived</option></select></div>}>
        {loading && <EmptyState icon="LoaderCircle" title="正在读取 Model Registry" description="连接 Go Control Plane…" />}
        {!loading && error && <EmptyState icon="TriangleAlert" title="Model Registry 不可用" description={error.message} />}
        {!loading && !error && versions.length === 0 && <EmptyState icon="Boxes" title="尚无 Model Version" description="训练成功后，Control Plane 会创建不可变候选版本。" />}
        {!loading && !error && versions.length > 0 && <ModelGroups versions={versions} onScopeChange={() => setSelected([])}>{groupVersions => <div className="fv-table-wrap"><table className="fv-table fv-registry-table"><thead><tr><th aria-label="选择比较" /><th>模型 / 发布版本</th><th>别名</th><th>数据集名称</th><th>模型方案</th><th>状态</th><th className="numeric">Accuracy</th><th className="numeric">Macro F1</th><th>创建时间</th><th>操作</th></tr></thead><tbody>{groupVersions.map((version) => <tr key={version.id}><td><input aria-label={`选择 ${version.name}`} type="checkbox" checked={selected.includes(version.id)} onChange={() => toggle(version)} disabled={!modelScope(version) || (!selected.includes(version.id) && selected.length >= 5)} /></td><td><Link to={`/models/${version.id}`}><strong title={version.name}>{version.name}</strong><small className="fv-release-tag">{version.releaseVersion || (version.status === "production" ? "历史发布" : "尚未发布")}</small></Link></td><td><AliasBadges aliases={version.aliases} /></td><td><strong>{version.datasetName}</strong><small className="fv-cell-note" title={version.datasetVersionKey}>训练数据 {version.datasetVersionNumber ? `v${version.datasetVersionNumber}` : "历史快照"}</small></td><td>{backboneLabel(version)}<small className="fv-cell-note">{version.pretrainingDataset || "预训练信息未记录"}</small></td><td><StatusBadge status={version.status} /></td><td className="numeric">{formatPercent(version.metrics.accuracy)}</td><td className="numeric">{formatNumber(version.metrics.macro_f1)}</td><td>{version.createdAt ? new Date(version.createdAt).toLocaleString("zh-CN") : "未记录"}</td><td><div className="fv-registry-actions"><Link className="secondary-button" to={`/models/${version.id}`}>详情</Link><button className="primary-button" disabled={!["candidate", "staging", "production"].includes(version.status)} onClick={() => setPublishing(version)}>发布</button></div></td></tr>)}</tbody></table></div>}</ModelGroups>}
      </Panel>
      {notice && <p role="status">{notice}</p>}
      {publishing && <PublishDialog key={publishing.id} version={publishing} onClose={() => setPublishing(null)} onPublished={v => { setPublishing(null); setNotice(`发布成功 · ${v.releaseVersion}`); refresh(); }} />}
    </div>
  );
}

export function ModelVersionDetailPage({ showToast }) {
  const [busy, setBusy] = useState(false);
  const [publishing, setPublishing] = useState(false);
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
  const metrics = [
    ["准确率", "accuracy", true],
    ["Macro F1", "macro_f1", false],
    ["预测覆盖率", "expected_coverage", true],
  ].filter(([, key]) => version.metrics[key] != null && version.metrics[key] !== "" && Number.isFinite(Number(version.metrics[key])));
  const configuration = [
    ["模型架构", backboneLabel(version)],
    ["预训练数据", version.pretrainingDataset],
    ["输入尺寸", version.inputSize ? `${version.inputSize} px` : null],
    ["参数量", version.parameterCount ? `${(version.parameterCount / 1e6).toFixed(1)}M` : null],
  ].filter(([, value]) => value);
  const technical = [
    ["模型 ID", version.id], ["数据集 ID", version.datasetId],
    ["数据版本", version.datasetVersionKey], ["训练任务 ID", version.trainingRunId],
    ["评估协议指纹", version.evaluationContext.protocol_fingerprint],
    ["骨干网络", version.backboneKey], ["架构标识", version.architecture],
    ["预训练方法", version.pretrainingMethod], ["特征维度", version.featureDim],
    ["池化方式", version.pooling], ["分类头", version.headType],
  ].filter(([, value]) => value != null && value !== "");
  const eventLabels = { published: "发布模型", created: "创建版本", status_changed: "更新状态", alias_set: "设置别名", archived: "归档版本" };
  return <div className="fv-feature-page fv-model-detail">
    <Link className="fv-model-back" to="/models"><Icon name="ArrowLeft" size={14} />模型版本</Link>
    <PageHeading title={`${version.name} · ${version.releaseVersion || (version.status === "production" ? "历史发布" : "尚未发布")}`} description={backboneLabel(version)} actions={<>
      {version.aliases.length > 0 && <AliasBadges aliases={version.aliases} />}
      <StatusBadge status={version.status} />
      {version.trainingRunId && <Link className="secondary-button" to={`/training/${version.trainingRunId}`}>查看训练</Link>}
      <Link className="secondary-button" to="/models"><Icon name="GitCompareArrows" size={15} />选择版本比较</Link>
    </>} />
    <div className="fv-model-overview">
      <Panel title="评估结果" className="fv-model-evaluation">
        {metrics.length ? <div className="fv-model-metrics">{metrics.map(([label, key, percent]) => <div key={key}><span>{label}</span><strong>{percent ? formatPercent(version.metrics[key]) : formatNumber(version.metrics[key])}</strong></div>)}</div> : <p className="fv-model-empty">暂无评估结果</p>}
        <p className="fv-model-footnote">{metrics.length ? "仅展示已记录指标，版本比较需使用相同数据与评估协议。" : "完成评估后，指标将在这里展示。"}</p>
      </Panel>
      <Panel title="模型概况">
        <dl className="fv-definition-list">
          <div><dt>所属数据集</dt><dd>{version.datasetName}</dd></div>
          {configuration.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}
          {version.createdAt && <div><dt>创建时间</dt><dd>{new Date(version.createdAt).toLocaleString("zh-CN")}</dd></div>}
        </dl>
      </Panel>
    </div>
    <ModelDeployments key={version.id} version={version} />
    <div className="fv-model-disclosures">
      <details className="fv-model-disclosure"><summary><span>技术详情<small>版本标识、完整配置与模型文件</small></span><Icon name="ChevronDown" size={16} /></summary>
        <dl className="fv-definition-list fv-model-technical">{technical.map(([label, value]) => <div key={label}><dt>{label}</dt><dd><code>{value}</code></dd></div>)}</dl>
        <div className="fv-model-files"><h4>模型文件 · {version.artifacts.length}</h4>{version.artifacts.length ? version.artifacts.map((item, index) => <div className="fv-model-file" key={item.artifact_id || index}><strong>{item.artifact_type || "模型文件"}</strong><dl className="fv-definition-list">
          {[["大小", item.size_bytes != null ? `${Number(item.size_bytes).toLocaleString()} bytes` : null], ["SHA-256", item.sha256], ["存储位置", item.uri]].filter(([, value]) => value).map(([label, value]) => <div key={label}><dt>{label}</dt><dd><code>{value}</code></dd></div>)}
        </dl></div>) : <p className="fv-model-empty">暂无已登记文件</p>}</div>
      </details>
      <details className="fv-model-disclosure"><summary><span>操作记录<small>{version.events.length} 条记录</small></span><Icon name="ChevronDown" size={16} /></summary>
        {version.events.length ? <ol className="fv-event-list">{version.events.map((event) => <li key={event.event_id}><i /><span><strong>{eventLabels[event.event_type] || event.event_type}</strong><small>{event.actor}{event.reason ? ` · ${event.reason}` : ""}</small></span><time>{new Date(event.created_at).toLocaleString("zh-CN")}</time></li>)}</ol> : <p className="fv-model-empty">暂无操作记录</p>}
      </details>
      {version.status !== "archived" && <details className="fv-model-disclosure"><summary><span>版本管理<small>状态变更、别名与归档</small></span><Icon name="ChevronDown" size={16} /></summary>
        <div className="fv-model-management"><p>变更时需填写原因，操作将保存到记录中。</p><p>{version.headType === "image_classifier_v2" ? "发布会合并 LoRA（如有），导出骨干与分类头的完整 .pt 和 ONNX。" : "历史分类头模型仅导出分类头；完整模型需要重新训练。"} 验证输出一致性、SHA-256 和大小后保存到 MinIO，成功才标记已发布，原训练检查点保留。</p><div className="fv-action-list">
          {["candidate", "staging", "production"].includes(version.status) && <button className="primary-button" disabled={busy} onClick={() => setPublishing(true)}>发布模型 · 选择精度</button>}
          {version.status === "production" && !version.aliases.includes("champion") && <button className="secondary-button" disabled={busy} onClick={() => perform("champion")}>设为 Champion</button>}
          {["candidate", "staging"].includes(version.status) && !version.aliases.includes("challenger") && <button className="secondary-button" disabled={busy} onClick={() => perform("challenger")}>设为 Challenger</button>}
          <button className="danger-button" disabled={busy} onClick={() => perform("archive")}>归档版本</button>
        </div></div>
      </details>}
    </div>
    {publishing && <PublishDialog version={version} onClose={() => setPublishing(false)} onPublished={v => { setPublishing(false); showToast?.(`发布成功 · ${v.releaseVersion}`); refresh(); }} />}
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
    setState({ comparison: null, loading: true, error: null });
    compareModelVersions(ids).then((comparison) => {
      const scope = modelScope(comparison.versions[0] ?? {});
      if (!scope || comparison.versions.length !== ids.length || comparison.versions.some(version => modelScope(version) !== scope)) {
        throw new Error("只能比较同一数据集、同一数据版本下的模型，请返回列表重新选择。");
      }
      if (active) setState({ comparison, loading: false, error: null });
    }).catch((error) => active && setState({ comparison: null, loading: false, error }));
    return () => { active = false; };
  }, [ids]);
  if (ids.length < 2) return <EmptyState icon="GitCompareArrows" title="请选择 2–5 个版本" description="回到模型版本列表勾选可比较版本。" />;
  if (state.loading) return <EmptyState icon="LoaderCircle" title="正在校验可比性" description="检查 Dataset Version 与评估协议…" />;
  if (state.error || !state.comparison) return <><Link className="secondary-button" to="/models">返回模型列表</Link><EmptyState icon="TriangleAlert" title="比较不可用" description={state.error?.message} /></>;
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
