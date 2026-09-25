import { useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { Icon } from "../../components/icons.jsx";
import { useReviewItems } from "../../hooks/useReviews.js";
import { useTrainingRuns } from "../../hooks/useTrainingRuns.js";
import { useModelVersions } from "../model-versions/useModelVersions.js";
import "./dashboard.css";

const REASON_LABELS = {
  confidence_below_threshold: "置信度低于阈值",
  top1_top2_margin_below_threshold: "Top-1 与 Top-2 间隔过小",
  ood_score_above_threshold: "OOD 分数超过阈值",
};

const RISK_LABELS = {
  ood_candidate: "OOD 候选",
  low_confidence: "低置信",
  low_margin: "低间隔",
  mixed: "多阈值触发",
};

function percent(value, digits = 1) {
  const number = Number(value);
  return Number.isFinite(number) ? `${(number * 100).toFixed(digits)}%` : "未采集";
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
    <section className="home-scope" aria-label="工作台作用域">
      <div className="home-scope-copy">
        <StatusBadge tone="info">模型作用域</StatusBadge>
        <span>所有发布读数都绑定当前模型版本</span>
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
            <option key={version.id} value={version.id}>{version.releaseVersion || "未发布"} · {version.status} · {shortId(version.id)}</option>
          )) : <option value="">暂无模型</option>}
        </select>
      </label>
      <div className="home-scope-time"><Icon name="Clock" size={15} /><span>数据更新</span><code>{updatedAt}</code></div>
    </section>
  );
}

function StatusStrip({ reviewTotal, reviewCaption, failedCount, oodLoaded, loadedCount, modelCount, productionCount, candidateCount }) {
  return (
    <section className="home-status-strip" aria-label="平台状态摘要">
      <div><span>待复核总数</span><strong>{reviewTotal}</strong><small>{reviewCaption}</small></div>
      <div><span>失败训练</span><strong className={failedCount ? "danger" : ""}>{failedCount}</strong><small>可直达具体运行</small></div>
      <div><span>OOD 候选</span><strong>{oodLoaded}</strong><small>已读取 {loadedCount} 条中的分布</small></div>
      <div><span>模型注册表</span><strong>{modelCount}</strong><small>{productionCount} 个 Production · {candidateCount} 个待验证</small></div>
    </section>
  );
}

function EvidencePanel({ item }) {
  if (!item) return <div className="home-empty">当前没有可展示的复核证据。</div>;
  const candidates = item.topK?.slice(0, 3) ?? [];
  const maximum = Math.max(...candidates.map((candidate) => candidate.score), 0.01);
  const reasons = item.reasonCodes?.length ? item.reasonCodes : item.decision?.reasons ?? [];
  return (
    <div className="home-evidence">
      <div className="home-evidence-head"><div><span>选中异常 · {riskLabel(item.riskType)}</span><strong>{item.sampleId || `复核 ${shortId(item.id)}`}</strong></div><StatusBadge tone="warning">优先级 {item.priority ?? "--"}</StatusBadge></div>
      <div className="home-evidence-body">
        <div className="home-image-stage">{item.imageUrl ? <img src={item.imageUrl} alt="待复核原图" /> : <div><Icon name="ImageOff" size={26} /><span>原图未返回</span></div>}</div>
        <div className="home-evidence-readings">
          <div className="home-topk">
            {candidates.map((candidate, index) => <div key={`${candidate.label}-${index}`}><span>{index + 1}</span><strong>{candidate.label}</strong><i><b style={{ width: `${Math.max(3, candidate.score / maximum * 100)}%` }} /></i><code>{percent(candidate.score)}</code></div>)}
          </div>
          <div className="home-thresholds">
            <div><span>置信度</span><code>{percent(item.decision?.confidence)}</code><small>阈值 {percent(item.decision?.thresholds?.confidence)}</small></div>
            <div><span>Top-2 间隔</span><code>{percent(item.decision?.margin)}</code><small>阈值 {percent(item.decision?.thresholds?.margin)}</small></div>
          </div>
          <div className="home-reasons">{reasons.slice(0, 2).map((reason) => <span key={reason}>{reasonLabel(reason)}</span>)}</div>
        </div>
      </div>
      <div className="home-evidence-actions"><Link className="primary-button" to={`/review/${item.id}`}>进入人工复核</Link><Link className="ghost-button" to={`/review/${item.id}`}>查看原图与近邻</Link></div>
    </div>
  );
}

function buildChecks(model) {
  const artifacts = new Set((model?.artifacts ?? []).map((artifact) => artifact.artifact_type ?? artifact.artifactType));
  return {
    system: [
      { label: "模型产物", detail: artifacts.has("model") || artifacts.has("full_pt") ? "文件与 SHA 已登记" : "缺少可验证模型产物", state: artifacts.has("model") || artifacts.has("full_pt") ? "verified" : "missing" },
      { label: "评估报告", detail: artifacts.has("report") ? "报告已关联当前版本" : "当前版本缺少 report", state: artifacts.has("report") ? "verified" : "missing" },
      { label: "阈值材料", detail: artifacts.has("calibration") && artifacts.has("threshold_strategy") ? "校准与策略均已关联" : "校准或阈值策略缺失", state: artifacts.has("calibration") && artifacts.has("threshold_strategy") ? "verified" : "missing" },
      { label: "注册表状态", detail: model ? `${model.status} · ${model.releaseVersion || "尚未发布"}` : "未选择模型", state: model?.status === "production" ? "production" : model ? "pending" : "missing" },
    ],
    manual: [
      { label: "反馈池检查", detail: "建议人工核查；当前无实时门禁结论", state: "manual" },
      { label: "回滚配置", detail: "建议确认回滚目标；当前无实时验证结果", state: "manual" },
    ],
  };
}

function ReleaseChecklist({ model }) {
  const checks = buildChecks(model);
  const Row = ({ item }) => {
    const tone = item.state === "verified" || item.state === "production" ? "success" : item.state === "missing" ? "danger" : "warning";
    const label = item.state === "verified" ? "已验证" : item.state === "production" ? "Production" : item.state === "missing" ? "缺失" : "待人工核查";
    return <div className="home-check-row"><i className={tone} /><div><strong>{item.label}</strong><small>{item.detail}</small></div><StatusBadge tone={tone}>{label}</StatusBadge></div>;
  };
  return (
    <div className="home-checklist">
      <div className="home-check-heading"><strong>系统验证结果</strong><span>来自当前版本的真实记录</span></div>
      {checks.system.map((item) => <Row item={item} key={item.label} />)}
      <div className="home-check-heading manual"><strong>建议人工核查项</strong><span>不是系统许可</span></div>
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
  const activeRuns = trainingRuns.filter((run) => ["queued", "running"].includes(run.status));
  const productionCount = modelVersions.filter((version) => version.status === "production").length;
  const candidateCount = modelVersions.filter((version) => ["candidate", "staging"].includes(version.status)).length;
  const oodLoaded = reviewItems.filter((item) => item.riskType === "ood_candidate").length;
  const newestTimestamp = [...reviewItems.map((item) => item.updatedAt || item.createdAt), ...trainingRuns.map((run) => run.updatedAt || run.createdAt), ...modelVersions.map((model) => model.updatedAt || model.createdAt)].filter(Boolean).sort().at(-1);
  const loading = reviewLoading || trainingLoading || modelsLoading;
  const unavailable = reviewError || trainingError || modelsError;
  const firstFailed = failedRuns[0];
  const reviewTotal = reviewLoading && !reviewItems.length ? "读取中" : pagination.totalKnown ? String(pagination.total) : `已读取 ${reviewItems.length} 条`;
  const reviewCaption = pagination.totalKnown ? "接口 pagination.total" : "接口未返回队列总数";

  const actions = [
    failedRuns.length ? { id: "training", order: "01", eyebrow: "阻塞发布的技术风险", title: `${failedRuns.length} 条训练运行失败`, object: firstFailed?.name || shortId(firstFailed?.id), reason: firstFailed?.error || "运行失败，尚未记录可读错误", consequence: "先定位运行故障；只在关联当前模型版本时影响本次发布。", tone: "danger", to: firstFailed ? `/training/${firstFailed.id}` : "/training", cta: "打开运行诊断" } : null,
    reviewItems.length ? { id: "review", order: "02", eyebrow: "需要人的判断", title: `待复核队列 · ${reviewTotal}`, object: selectedReview?.sampleId || `样本 ${shortId(selectedReview?.id)}`, reason: (selectedReview?.reasonCodes ?? []).slice(0, 2).map(reasonLabel).join(" / ") || "推理已弃权", consequence: "确认后进入反馈池，不自动改写已注册数据版本。", tone: "warning", to: selectedReview ? `/review/${selectedReview.id}` : "/review", cta: "进入人工复核" } : null,
    { id: "release", order: "03", eyebrow: "发布材料核查", title: `${candidateCount} 个 candidate / staging`, object: selectedModel?.releaseVersion || shortId(selectedModel?.id), reason: "系统证据与建议人工核查项必须分别确认", consequence: "首页只汇总材料，正式发布仍在模型版本页执行。", tone: "info", to: selectedModel ? `/models/${selectedModel.id}` : "/models", cta: "查看模型版本" },
  ].filter(Boolean);

  return (
    <div className="home-dashboard">
      <ScopeBar modelVersions={modelVersions} selectedModel={selectedModel} onSelectModel={selectModel} updatedAt={loading ? "同步中" : formatDateTime(newestTimestamp)} />
      {unavailable && <div className="home-api-warning"><Icon name="AlertTriangle" size={17} />部分接口暂不可用；对应位置不推断系统总量或发布结论。</div>}
      <StatusStrip reviewTotal={reviewTotal} reviewCaption={reviewCaption} failedCount={failedRuns.length} oodLoaded={oodLoaded} loadedCount={reviewItems.length} modelCount={modelVersions.length} productionCount={productionCount} candidateCount={candidateCount} />
      <header className="home-intro"><div><span>Daily command center</span><h2>今天先处理什么，依据是什么。</h2></div><p>行动队列负责排序，证据抽屉负责解释；发布检查始终绑定当前选中的模型版本。</p></header>
      <section className="home-command-grid">
        <aside className="home-risk-overview">
          <div><span>Risk overview</span><h3>风险概览</h3><p>红色仅表示真实失败，琥珀表示需要人工判断。</p></div>
          <div className="home-risk-list">
            <button className={expanded === "training" ? "active" : ""} type="button" onClick={() => setExpanded("training")} disabled={!failedRuns.length}><i className="danger" /><span><b>训练失败</b><small>可直达具体运行</small></span><strong>{failedRuns.length}</strong></button>
            <button className={expanded === "review" ? "active" : ""} type="button" onClick={() => setExpanded("review")} disabled={!reviewItems.length}><i className="warning" /><span><b>人工待核</b><small>{reviewCaption}</small></span><strong>{pagination.totalKnown ? pagination.total : reviewItems.length}</strong></button>
            <button className={expanded === "release" ? "active" : ""} type="button" onClick={() => setExpanded("release")}><i className="info" /><span><b>待验证模型</b><small>candidate + staging</small></span><strong>{candidateCount}</strong></button>
          </div>
          <div className="home-risk-note"><span>抽样分布</span><strong>已读取 {reviewItems.length} 条</strong><small>其中 OOD 候选 {oodLoaded} 条；不外推全队列。</small></div>
        </aside>
        <main className="home-actions">
          <div className="home-section-title"><div><span>Today’s actions</span><h3>今日行动队列</h3></div><small>按阻塞程度排序</small></div>
          <div className="home-action-list">
            {actions.map((action) => <article className={`${action.tone} ${expanded === action.id ? "selected" : ""}`} key={action.id}>
              <button type="button" className="home-action-summary" onClick={() => setExpanded((current) => current === action.id ? "" : action.id)} aria-expanded={expanded === action.id}>
                <span className="home-action-index">{action.order}</span><span className="home-action-copy"><small>{action.eyebrow}</small><b>{action.title}</b><em>{action.object}</em></span><span className="home-action-reason"><small>为什么</small><b>{action.reason}</b></span><Icon name={expanded === action.id ? "ChevronDown" : "ChevronRight"} size={17} />
              </button>
              {expanded === action.id && <div className="home-action-drawer">
                {action.id === "review" && <EvidencePanel item={selectedReview} />}
                {action.id === "training" && <div className="home-run-evidence"><div><span>Error</span><code>{firstFailed?.error || "接口未返回错误详情"}</code></div><div><span>Job ID</span><code>{firstFailed?.jobId || "未返回"}</code></div><div><span>缺失产物</span><code>{[!firstFailed?.modelArtifactId && "Model", !firstFailed?.reportArtifactId && "Report"].filter(Boolean).join(" / ") || "未发现"}</code></div></div>}
                {action.id === "release" && <div className="home-release-note"><div><span>当前模型</span><strong>{selectedModel?.releaseVersion || "尚未发布"}</strong><code>{shortId(selectedModel?.id)}</code></div><p>发布判断只使用当前模型版本的产物、评估协议与状态，不把其他成功训练运行混入结论。</p></div>}
                <div className="home-drawer-footer"><span>{action.consequence}</span><Link className="primary-button" to={action.to}>{action.cta} →</Link></div>
              </div>}
            </article>)}
          </div>
        </main>
        <aside className="home-release-check">
          <div className="home-section-title"><div><span>Release check</span><h3>当前模型发布检查</h3></div></div>
          <div className="home-current-model"><div><span>当前作用域</span><strong>{selectedModel?.datasetName || "未选择数据集"}</strong></div><StatusBadge tone={selectedModel?.status === "production" ? "success" : "info"}>{selectedModel?.status || "未选择"}</StatusBadge><code>{selectedModel?.releaseVersion || shortId(selectedModel?.id)}</code></div>
          <ReleaseChecklist model={selectedModel} />
          <Link className="home-model-link" to={selectedModel ? `/models/${selectedModel.id}` : "/models"}>查看完整发布材料 <span>→</span></Link>
        </aside>
      </section>
      <details className="home-lineage">
        <summary><div><Icon name="Route" size={18} /><span><b>追踪完整证据链</b><small>推理异常如何经过复核、反馈、数据版本和训练到达发布</small></span></div><span>展开链路</span></summary>
        <div className="home-lineage-track">{[
          ["推理异常", reviewTotal, "warning"], ["人工复核", "待处理", "warning"], ["反馈池", "已记录", "complete"], ["数据版本", "需策展", "manual"], ["训练", failedRuns.length ? `${failedRuns.length} 失败` : activeRuns.length ? `${activeRuns.length} 进行中` : "可追踪", failedRuns.length ? "danger" : "info"], ["模型版本", `${modelVersions.length} 可见`, "info"], ["发布", selectedModel?.status === "production" ? "Production" : "待判断", selectedModel?.status === "production" ? "complete" : "manual"],
        ].map(([label, value, state], index, items) => <div className="home-lineage-node" key={label}><span className={state}>{index + 1}</span><b>{label}</b><small>{value}</small>{index < items.length - 1 && <i className={label === "反馈池" ? "uncertain" : ""} />}</div>)}</div>
        <div className="home-lineage-caption"><StatusBadge tone="warning">关键语义</StatusBadge><span>复核进入反馈池，并不自动成为训练数据；必须经过数据策展并发布新的不可变数据版本。</span><Link to="/pipelines">进入流水线追踪 →</Link></div>
      </details>
    </div>
  );
}
