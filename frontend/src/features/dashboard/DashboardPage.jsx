import { useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { Icon } from "../../components/icons.jsx";
import { useReviewItems } from "../../hooks/useReviews.js";
import { useTrainingRuns } from "../../hooks/useTrainingRuns.js";
import { useModelVersions } from "../model-versions/useModelVersions.js";
import "./dashboard.css";

const REASON_LABELS = {
  confidence_below_threshold: "置信度低于阈值",
  top1_top2_margin_below_threshold: "前两名分数差低于阈值",
  ood_score_above_threshold: "OOD 分数超过阈值",
};

const RISK_LABELS = {
  ood_candidate: "OOD 候选",
  low_confidence: "置信度偏低",
  low_margin: "前两名分数接近",
  mixed: "多阈值触发",
};

function percent(value, digits = 1) {
  if (value == null || value === "") return "未采集";
  const number = Number(value);
  return Number.isFinite(number) ? `${(number * 100).toFixed(digits)}%` : "未采集";
}

function percentagePoints(value) {
  const formatted = percent(value, 2);
  return formatted.endsWith("%") ? formatted.replace("%", " 个百分点") : formatted;
}

function modelStatusLabel(status) {
  return ({ candidate: "候选", staging: "预发布", production: "已发布", archived: "已归档" })[status] ?? "状态未知";
}

function shortId(value) {
  if (!value) return "未记录";
  const text = String(value);
  return text.length > 14 ? `${text.slice(0, 8)}…${text.slice(-4)}` : text;
}

function formatDateTime(value) {
  if (!value) return "未记录";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "未记录" : date.toLocaleString("zh-CN", { hour12: false });
}

function reasonLabel(value) {
  return REASON_LABELS[value] ?? value?.replaceAll("_", " ") ?? "未记录触发原因";
}

function riskLabel(value) {
  return RISK_LABELS[value] ?? "需人工判断";
}

function StatusBadge({ tone = "neutral", children }) {
  return <span className={`home-badge home-badge--${tone}`}>{children}</span>;
}

function ScopeBar({ modelVersions, selectedModel, onSelectModel, updatedAt }) {
  const datasets = useMemo(() => {
    const map = new Map();
    modelVersions.forEach((version) => {
      if (version.datasetId) map.set(version.datasetId, version.datasetName || version.datasetId);
    });
    return [...map.entries()];
  }, [modelVersions]);
  const datasetId = selectedModel?.datasetId ?? datasets[0]?.[0] ?? "";
  const scopedModels = modelVersions.filter((version) => !datasetId || version.datasetId === datasetId);

  function selectDataset(nextDatasetId) {
    const preferred = modelVersions.find((version) => version.datasetId === nextDatasetId && version.status === "production")
      ?? modelVersions.find((version) => version.datasetId === nextDatasetId);
    if (preferred) onSelectModel(preferred.id);
  }

  return (
    <section className="home-scope" aria-label="所选模型">
      <div className="home-scope-copy">
        <StatusBadge tone="info">当前模型</StatusBadge>
        <span>仅右侧发布信息随模型切换；统计不按模型筛选</span>
      </div>
      <label>
        <span>数据集</span>
        <select value={datasetId} onChange={(event) => selectDataset(event.target.value)}>
          {datasets.length ? datasets.map(([id, name]) => <option key={id} value={id}>{name}</option>) : <option value="">暂无数据</option>}
        </select>
      </label>
      <label>
        <span>模型版本</span>
        <select value={selectedModel?.id ?? ""} onChange={(event) => onSelectModel(event.target.value)}>
          {scopedModels.length ? scopedModels.map((version) => (
            <option key={version.id} value={version.id}>{version.releaseVersion || "未发布"} · {modelStatusLabel(version.status)} · {shortId(version.id)}</option>
          )) : <option value="">暂无模型</option>}
        </select>
      </label>
      <div className="home-scope-time"><Icon name="Clock" size={15} /><span>最近记录更新</span><code>{updatedAt}</code></div>
    </section>
  );
}

function StatusStrip({ reviewTotal, reviewCaption, failedCount, oodLoaded, loadedCount, modelCount, productionCount, candidateCount }) {
  return (
    <section className="home-status-strip" aria-label="平台状态摘要">
      <div><span>待复核总数</span><strong>{reviewTotal}</strong><small>{reviewCaption}</small></div>
      <div><span>失败训练</span><strong className={typeof failedCount === "number" && failedCount > 0 ? "danger" : ""}>{failedCount}</strong><small>已加载任务中的失败数量</small></div>
      <div><span>OOD 候选</span><strong>{oodLoaded}</strong><small>仅统计已加载的 {loadedCount} 条样本</small></div>
      <div><span>模型版本</span><strong>{modelCount}</strong><small>{typeof modelCount === "number" ? `${productionCount} 个已发布 · ${candidateCount} 个未发布` : "发布数量暂不可用"}</small></div>
    </section>
  );
}

function EvidencePanel({ item }) {
  if (!item) return <div className="home-empty">请选择待复核样本查看推理结果。</div>;
  const candidates = item.topK?.slice(0, 3) ?? [];
  const maximum = Math.max(...candidates.map((candidate) => candidate.score), 0.01);
  const reasons = item.reasonCodes?.length ? item.reasonCodes : item.decision?.reasons ?? [];
  return (
    <div className="home-evidence">
      <div className="home-evidence-head"><div><span>当前样本 · {riskLabel(item.riskType)}</span><strong>{item.sampleId || `复核 ${shortId(item.id)}`}</strong></div><StatusBadge tone="warning">优先级 {item.priority ?? "未记录"}</StatusBadge></div>
      <div className="home-evidence-body">
        <div className="home-image-stage">{item.imageUrl ? <img src={item.imageUrl} alt="待复核原图" /> : <div><Icon name="ImageOff" size={26} /><span>原图未返回</span></div>}</div>
        <div className="home-evidence-readings">
          <div className="home-topk">
            {candidates.map((candidate, index) => <div key={`${candidate.label}-${index}`}><span>{index + 1}</span><strong>{candidate.label}</strong><i><b style={{ width: `${Math.max(3, candidate.score / maximum * 100)}%` }} /></i><code>{percent(candidate.score)}</code></div>)}
          </div>
          <div className="home-thresholds">
            <div><span>置信度</span><code>{percent(item.decision?.confidence)}</code><small>阈值 {percent(item.decision?.thresholds?.confidence)}</small></div>
            <div><span>前两名分数差</span><code>{percentagePoints(item.decision?.margin)}</code><small>阈值 {percentagePoints(item.decision?.thresholds?.margin)}</small></div>
          </div>
          <div className="home-reasons">{reasons.slice(0, 2).map((reason) => <span key={reason}>{reasonLabel(reason)}</span>)}</div>
        </div>
      </div>
      <div className="home-evidence-actions"><Link className="primary-button" to={`/review/${item.id}`}>进入人工复核</Link><Link className="ghost-button" to={`/review/${item.id}`}>查看复核详情</Link></div>
    </div>
  );
}

function buildChecks(model) {
  const artifacts = new Set((model?.artifacts ?? []).map((artifact) => artifact.artifact_type ?? artifact.artifactType));
  return {
    system: [
      { label: "模型产物", detail: artifacts.has("model") || artifacts.has("full_pt") ? "已关联模型文件记录" : "未关联模型文件", state: artifacts.has("model") || artifacts.has("full_pt") ? "linked" : "missing" },
      { label: "评估报告", detail: artifacts.has("report") ? "报告已关联当前版本" : "未关联评估报告", state: artifacts.has("report") ? "linked" : "missing" },
      { label: "校准与阈值策略", detail: artifacts.has("calibration") && artifacts.has("threshold_strategy") ? "校准与策略均已关联" : "校准或阈值策略缺失", state: artifacts.has("calibration") && artifacts.has("threshold_strategy") ? "linked" : "missing" },
      { label: "模型状态", detail: model ? `${modelStatusLabel(model.status)} · ${model.releaseVersion || "尚未发布"}` : "未选择模型", state: model?.status === "production" ? "production" : model ? "pending" : "missing" },
    ],
    manual: [
      { label: "检查待处理反馈", detail: "建议检查是否有需要补充的数据或尚未处理的反馈", state: "manual" },
      { label: "确认可用的历史版本", detail: "建议确认发布异常时可使用的历史模型", state: "manual" },
    ],
  };
}

function ReleaseChecklist({ model }) {
  if (!model) return <p className="home-empty">暂无可查看的模型。请选择模型版本后查看关联材料。</p>;
  const checks = buildChecks(model);
  const Row = ({ item }) => {
    const tone = item.state === "linked" || item.state === "production" ? "success" : item.state === "missing" ? "danger" : "warning";
    const label = item.state === "linked" ? "已关联" : item.state === "production" ? "已发布" : item.state === "missing" ? "未关联" : item.state === "manual" ? "建议检查" : "未发布";
    return <div className="home-check-row"><i className={tone} /><div><strong>{item.label}</strong><small>{item.detail}</small></div><StatusBadge tone={tone}>{label}</StatusBadge></div>;
  };
  return (
    <div className="home-checklist">
      <div className="home-check-heading"><strong>模型材料</strong><span>仅表示记录已关联，不代表文件校验通过</span></div>
      {checks.system.map((item) => <Row item={item} key={item.label} />)}
      <div className="home-check-heading manual"><strong>发布前建议</strong><span>参考建议，不是自动检测结果</span></div>
      {checks.manual.map((item) => <Row item={item} key={item.label} />)}
    </div>
  );
}

export function DashboardPage() {
  const { reviewItems, pagination, loading: reviewLoading, error: reviewError } = useReviewItems({ status: "pending", limit: 100 });
  const { trainingRuns, loading: trainingLoading, error: trainingError } = useTrainingRuns();
  const { versions: modelVersions, loading: modelsLoading, error: modelsError } = useModelVersions();
  const [scopeParams, setScopeParams] = useSearchParams();
  const selectedModelId = scopeParams.get("model_version_id") || "";
  const [expanded, setExpanded] = useState("review");

  const selectedReview = reviewItems[0] ?? null;
  const selectedModel = modelVersions.find((version) => version.id === selectedModelId)
    ?? modelVersions.find((version) => version.status === "production")
    ?? modelVersions[0]
    ?? null;
  useEffect(() => {
    if (selectedModelId || !selectedModel?.id) return;
    const next = new URLSearchParams(scopeParams);
    next.set("model_version_id", selectedModel.id);
    if (selectedModel.datasetId) next.set("dataset_id", selectedModel.datasetId);
    setScopeParams(next, { replace: true });
  }, [scopeParams, selectedModel, selectedModelId, setScopeParams]);

  function selectModel(nextModelId) {
    const next = new URLSearchParams(scopeParams);
    const model = modelVersions.find((version) => version.id === nextModelId);
    if (nextModelId) next.set("model_version_id", nextModelId);
    else next.delete("model_version_id");
    if (model?.datasetId) next.set("dataset_id", model.datasetId);
    else next.delete("dataset_id");
    setScopeParams(next, { replace: true });
  }

  const failedRuns = trainingRuns.filter((run) => run.status === "failed");
  const productionCount = modelVersions.filter((version) => version.status === "production").length;
  const candidateCount = modelVersions.filter((version) => ["candidate", "staging"].includes(version.status)).length;
  const oodLoaded = reviewItems.filter((item) => item.riskType === "ood_candidate").length;
  const newestTimestamp = [...reviewItems.map((item) => item.updatedAt || item.createdAt), ...trainingRuns.map((run) => run.updatedAt || run.createdAt), ...modelVersions.map((model) => model.updatedAt || model.createdAt)].filter(Boolean).sort().at(-1);
  const loading = reviewLoading || trainingLoading || modelsLoading;
  const unavailable = reviewError || trainingError || modelsError;
  const firstFailed = failedRuns[0];
  const reviewTotal = reviewError ? "加载失败" : reviewLoading && !reviewItems.length ? "读取中" : pagination.totalKnown ? String(pagination.total) : `已读取 ${reviewItems.length} 条`;
  const reviewCaption = pagination.totalKnown ? "待复核样本总数" : "总数暂不可用，仅显示已加载数量";

  if (loading) return <div className="home-dashboard"><div className="home-api-warning" role="status"><Icon name="LoaderCircle" size={17} />正在加载复核、训练与模型数据…</div></div>;

  const actions = [
    failedRuns.length ? { id: "training", order: "01", eyebrow: "训练任务异常", title: `${failedRuns.length} 个训练任务失败`, object: firstFailed?.name || shortId(firstFailed?.id), reason: firstFailed?.error || "任务失败，暂无错误详情", consequence: "先定位运行故障；只在关联当前模型版本时影响本次发布。", tone: "danger", to: firstFailed ? `/training/${firstFailed.id}` : "/training", cta: "查看失败详情" } : null,
    reviewItems.length ? { id: "review", order: "02", eyebrow: "待人工复核", title: `待复核样本 · ${reviewTotal}`, object: selectedReview?.sampleId || `样本 ${shortId(selectedReview?.id)}`, reason: (selectedReview?.reasonCodes ?? []).slice(0, 2).map(reasonLabel).join(" / ") || "推理已弃权", consequence: "确认后进入反馈池，不自动改写已注册数据版本。", tone: "warning", to: selectedReview ? `/review/${selectedReview.id}` : "/review", cta: "进入人工复核" } : null,
    !modelsError && candidateCount > 0 ? { id: "release", order: "03", eyebrow: "模型发布", title: `${candidateCount} 个未发布模型`, object: "已加载模型中的候选与预发布版本", reason: "选择需要发布的模型，查看其关联材料", consequence: "在模型版本页选择模型并执行发布。", tone: "info", to: "/models", cta: "查看模型列表" } : null,
  ].filter(Boolean);

  return (
    <div className="home-dashboard">
      <ScopeBar modelVersions={modelVersions} selectedModel={selectedModel} onSelectModel={selectModel} updatedAt={loading ? "同步中" : formatDateTime(newestTimestamp)} />
      {unavailable && <div className="home-api-warning"><Icon name="AlertTriangle" size={17} />部分数据加载失败，请稍后刷新。当前统计可能不完整。</div>}
      <StatusStrip reviewTotal={reviewTotal} reviewCaption={reviewError ? "暂时无法获取复核统计" : reviewCaption} failedCount={trainingError ? "加载失败" : failedRuns.length} oodLoaded={reviewError ? "加载失败" : oodLoaded} loadedCount={reviewItems.length} modelCount={modelsError ? "加载失败" : modelVersions.length} productionCount={productionCount} candidateCount={candidateCount} />
      <header className="home-intro"><div><h2>平台概览</h2></div><p>查看待复核样本、失败训练任务及所选模型的发布信息。</p></header>
      <section className="home-command-grid">
        <aside className="home-risk-overview">
          <div><h3>异常与待办</h3><p>统计已加载记录，不按所选模型筛选。</p></div>
          <div className="home-risk-list">
            <button className={expanded === "training" ? "active" : ""} type="button" onClick={() => setExpanded("training")} disabled={!failedRuns.length}><i className="danger" /><span><b>训练失败</b><small>查看失败任务详情</small></span><strong>{trainingError ? "加载失败" : failedRuns.length}</strong></button>
            <button className={expanded === "review" ? "active" : ""} type="button" onClick={() => setExpanded("review")} disabled={!reviewItems.length}><i className="warning" /><span><b>待复核样本</b><small>{reviewCaption}</small></span><strong>{reviewTotal}</strong></button>
            <button className={expanded === "release" ? "active" : ""} type="button" onClick={() => setExpanded("release")}><i className="info" /><span><b>未发布模型</b><small>候选与预发布版本</small></span><strong>{modelsError ? "加载失败" : candidateCount}</strong></button>
          </div>
          <div className="home-risk-note"><span>已加载样本</span><strong>已读取 {reviewItems.length} 条</strong><small>其中 OOD 候选 {oodLoaded} 条；不代表整个队列的分布。</small></div>
        </aside>
        <main className="home-actions">
          <div className="home-section-title"><div><h3>待处理事项</h3></div><small>训练异常、人工复核与模型发布</small></div>
          <div className="home-action-list">
            {!actions.length && <p className="home-empty">{unavailable ? "待处理事项暂不可用，请稍后刷新。" : "已加载记录中暂无待处理事项。"}</p>}
            {actions.map((action) => <article className={`${action.tone} ${expanded === action.id ? "selected" : ""}`} key={action.id}>
              <button type="button" className="home-action-summary" onClick={() => setExpanded((current) => current === action.id ? "" : action.id)} aria-expanded={expanded === action.id}>
                <span className="home-action-index">{action.order}</span><span className="home-action-copy"><small>{action.eyebrow}</small><b>{action.title}</b><em>{action.object}</em></span><span className="home-action-reason"><small>说明</small><b>{action.reason}</b></span><Icon name={expanded === action.id ? "ChevronDown" : "ChevronRight"} size={17} />
              </button>
              {expanded === action.id && <div className="home-action-drawer">
                {action.id === "review" && <EvidencePanel item={selectedReview} />}
                {action.id === "training" && <div className="home-run-evidence"><div><span>错误信息</span><code>{firstFailed?.error || "接口未返回错误详情"}</code></div><div><span>Job ID</span><code>{firstFailed?.jobId || "未返回"}</code></div><div><span>缺失产物</span><code>{[!firstFailed?.modelArtifactId && "Model", !firstFailed?.reportArtifactId && "Report"].filter(Boolean).join(" / ") || "未发现"}</code></div></div>}
                {action.id === "release" && <div className="home-release-note"><p>未发布数量不代表模型已满足发布条件。请在模型列表中选择版本，检查其材料并发布。</p></div>}
                <div className="home-drawer-footer"><span>{action.consequence}</span><Link className="primary-button" to={action.to}>{action.cta} →</Link></div>
              </div>}
            </article>)}
          </div>
        </main>
        <aside className="home-release-check">
          <div className="home-section-title"><div><h3>所选模型发布信息</h3></div></div>
          <div className="home-current-model"><div><span>所属数据集</span><strong>{selectedModel?.datasetName || "未选择数据集"}</strong></div><StatusBadge tone={selectedModel?.status === "production" ? "success" : "info"}>{selectedModel ? modelStatusLabel(selectedModel.status) : "未选择"}</StatusBadge><code>{selectedModel?.releaseVersion || shortId(selectedModel?.id)}</code></div>
          <ReleaseChecklist model={selectedModel} />
          <Link className="home-model-link" to={selectedModel ? `/models/${selectedModel.id}` : "/models"}>查看模型详情 <span>→</span></Link>
        </aside>
      </section>
      <details className="home-lineage">
        <summary><div><Icon name="Route" size={18} /><span><b>数据与模型关联</b><small>流程说明；不代表某次任务的执行进度</small></span></div><span>查看流程</span></summary>
        <div className="home-lineage-track">{[
          ["推理异常", "记录待复核样本", "info"], ["人工复核", "确认样本标签", "info"], ["反馈池", "保存复核结果", "info"], ["数据版本", "选择样本并发布", "info"], ["训练", "使用已发布数据版本", "info"], ["模型版本", "保存训练结果", "info"], ["发布", "导出推理模型", "info"],
        ].map(([label, value, state], index, items) => <div className="home-lineage-node" key={label}><span className={state}>{index + 1}</span><b>{label}</b><small>{value}</small>{index < items.length - 1 && <i className={label === "反馈池" ? "uncertain" : ""} />}</div>)}</div>
        <div className="home-lineage-caption"><StatusBadge tone="warning">数据纳入说明</StatusBadge><span>复核结果先保存到反馈池，由管理员选择样本并发布数据集新版本后，才能用于训练。</span><Link to="/pipelines">查看任务流水线 →</Link></div>
      </details>
    </div>
  );
}
