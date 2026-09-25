import "./review-queue.css";
import { pageNumbers } from "../../design-system/components/pagination.js";
import "../../design-system/components/pagination.css";
import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { pathWithSearch } from "../../utils/urls.js";
import { PaginatedSelect } from "../../design-system/components/PaginatedSelect.jsx";
import { listReviewItems } from "../../api/reviews.js";
import { useDatasets } from "../../hooks/useDatasets.js";
import {
  useAbstentionPolicies,
  useAbstentionShadowDecisions,
  useActivateAbstentionPolicy,
  useDeactivateAbstentionPolicy,
  useProposeAbstentionPolicy,
} from "../../hooks/useAbstentionPolicies.js";
import { useLLMAssistance, useReviewAssistance } from "../../hooks/useLLMAssistance.js";
import { useFeedbackItems, useReviewItem, useReviewItems, useSubmitReviewOutcome } from "../../hooks/useReviews.js";
import { Icon } from "../../components/icons.jsx";
import { CandidateBar, Panel, ProgressBar, StatusChip, VisualPlaceholder } from "../../components/ui.jsx";
import { PageHero } from "../../components/AppShell.jsx";
import { LLMAssistanceBox } from "../llm/LLMAssistanceBox.jsx";

function decisionValueLabel(value) {
  const labels = {
    accept: "自动通过",
    abstain: "模型弃权",
    reject_ood: "OOD 拦截",
  };
  return labels[value] ?? value;
}

function compactStatusLabel(value) {
  const labels = {
    loading: "加载中",
    clear: "已清空",
    empty: "空",
    error: "错误",
    failed: "失败",
    event: "事件",
    guarded: "已隔离",
    deferred: "待策展",
    gate: "门禁",
    "not found": "未找到",
    missing: "缺失",
  };
  return labels[value] ?? value;
}

function reviewReasonLabel(value) {
  const labels = {
    "Model abstained for multiple threshold reasons.": "模型触发多项弃权阈值，需人工确认。",
    confidence_below_threshold: "置信度低于阈值",
    top1_top2_margin_below_threshold: "前两名类别间隔过小",
    embedding_distance_above_threshold: "特征距离超过 OOD 阈值",
  };
  return labels[value] ?? value ?? "未记录原因";
}

function formatPolicyPercent(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return "--";
  return `${(number * 100).toFixed(1)}%`;
}

function formatPolicyNumber(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return "--";
  return number.toFixed(4);
}

function shadowDiffLabel(value) {
  const labels = {
    same: "一致",
    changed: "有变化",
    abstain_to_accept: "弃权转通过",
    accept_to_abstain: "通过转弃权",
    accept_to_reject_ood: "通过转 OOD",
    abstain_to_reject_ood: "弃权转 OOD",
    new_accepts_old_abstains: "新策略放行",
    new_abstains_old_accepts: "新策略弃权",
    new_rejects_ood: "新策略 OOD",
    other_change: "其他变化",
  };
  return labels[value] ?? value;
}

function policyStatusLabel(status) {
  const labels = {
    active: "已启用",
    shadow: "影子",
    candidate: "候选",
    superseded: "已替代",
    deactivated: "已停用",
    archived: "已归档",
  };
  return labels[status] ?? status ?? "未知";
}

function policyStatusTone(status) {
  if (status === "active") return "default";
  if (status === "superseded" || status === "deactivated" || status === "archived") return "neutral";
  if (status === "candidate") return "warn";
  return "info";
}

function policyStatusDescription(status) {
  if (status === "active") return "会影响真实推理阈值。";
  if (status === "superseded") return "被后续 active 策略替代，可人工回滚启用。";
  if (status === "deactivated") return "已人工停用，可在重新通过门禁后回滚启用。";
  if (status === "archived") return "已归档，不可再启用。";
  return "不会影响真实推理，仅用于回放评估。";
}

function reviewRisk(item) {
  if (item?.riskType === "ood_candidate") return { label: "OOD 候选", tone: "risk", visualType: "ood" };
  if (item?.riskType === "low_confidence") return { label: "低置信", tone: "warn", visualType: "bird" };
  if (item?.riskType === "low_margin") return { label: "低间隔", tone: "warn", visualType: "bird" };
  return { label: "需人工判断", tone: "info", visualType: "bird" };
}

function reviewStatus(item) {
  if (item?.status === "feedbacked") return { label: "已入反馈池", tone: "default" };
  if (item?.status === "submitted") return { label: "已提交", tone: "info" };
  if (item?.status === "skipped") return { label: "已跳过", tone: "neutral" };
  if (item?.status === "disputed") return { label: "争议", tone: "warn" };
  return { label: "待复核", tone: "warn" };
}

function destinationForOutcome(outcome) {
  if (outcome === "ood") return "ood_stress";
  if (outcome === "bad_image") return "bad_image";
  if (outcome === "uncertain") return "taxonomy_dispute";
  if (outcome === "ignore") return "ignore";
  return "training_candidate";
}

function destinationOptionsForOutcome(outcome) {
  if (outcome === "ood") return [["ood_stress", "OOD 压力池"]];
  if (outcome === "bad_image") return [["bad_image", "坏图池"]];
  if (outcome === "uncertain")
    return [
      ["taxonomy_dispute", "类别争议池"],
      ["ignore", "忽略池"],
    ];
  if (outcome === "ignore") return [["ignore", "忽略池"]];
  return [["training_candidate", "训练候选池"]];
}

function feedbackDestinationLabel(destination) {
  return FEEDBACK_DESTINATIONS.find(([value]) => value === destination)?.[1] ?? destination;
}

function feedbackOutcomeLabel(outcome) {
  const labels = {
    confirmed_label: "确认类别",
    corrected_label: "纠正类别",
    ood: "确认 OOD",
    bad_image: "坏图",
    uncertain: "仍不确定",
    ignore: "忽略",
  };
  return labels[outcome] ?? outcome;
}

const REVIEW_STATUS_TABS = [
  ["pending", "待复核"],
  ["feedbacked", "已完成"],
  ["all", "全部"],
];

const FEEDBACK_DESTINATIONS = [
  ["all", "全部反馈", "人工复核后的完整反馈池"],
  ["training_candidate", "训练候选", "可进入下一轮数据集策展，但不会自动训练"],
  ["ood_stress", "OOD 压力池", "用于构造拒识/压力测试候选"],
  ["bad_image", "坏图池", "用于数据清洗和采集质量回溯"],
  ["taxonomy_dispute", "类别争议", "用于 taxonomy 讨论和标注规范修正"],
  ["ignore", "忽略池", "明确不进入后续数据版本的记录"],
];

function displayValue(value, fallback = "未返回") {
  return value === null || value === undefined || value === "" ? fallback : value;
}

function TechnicalDetails({ summary = "技术详情", children }) {
  return (
    <details className="advanced-fields section-gap-small">
      <summary>{summary}</summary>
      <div className="code-panel section-gap-small">{children}</div>
    </details>
  );
}

function ReviewImage({ item, risk, detail = false }) {
  const label = item.sampleId || item.inputRef || item.id;
  if (item.imageUrl) {
    return (
      <div className={`review-image ${detail ? "detail" : ""}`}>
        <img src={item.imageUrl} alt={label} />
        <span>{label}</span>
      </div>
    );
  }
  return <VisualPlaceholder type={risk.visualType} label={label} low={item.riskType !== "ood_candidate"} />;
}

function ApiReviewCard({ item, queryString = "", datasetName }) {
  const risk = reviewRisk(item);
  const statusInfo = reviewStatus(item);
  const topCandidate = item.topK[0];
  const secondCandidate = item.topK[1];
  const hasLLMAssistance = Boolean(item.assistanceMetadata?.llm_assistance);
  const target = `/review/${item.id}${queryString ? `?${queryString}` : ""}`;
  return (
    <Link
      className="review-queue-card"
      to={target}
      aria-label={`复核 ${topCandidate?.label || item.sampleId || item.id}`}
    >
      <QueueThumbnail key={item.imageUrl} item={item} />
      <div className="review-queue-card__body">
        <div className="chips">
          <StatusChip tone={risk.tone}>{risk.label}</StatusChip>
          <StatusChip tone={statusInfo.tone}>{statusInfo.label}</StatusChip>
        </div>
        <h3 title={topCandidate?.label}>
          {topCandidate?.label || "待确认类别"}
          <span>{topCandidate ? `${(topCandidate.score * 100).toFixed(1)}%` : "—"}</span>
        </h3>
        <p className="review-queue-card__secondary">
          次选：{secondCandidate ? `${secondCandidate.label} · ${(secondCandidate.score * 100).toFixed(1)}%` : "暂无"}
        </p>
        <p className="review-queue-card__dataset" title={datasetName || item.datasetId}>
          {datasetName || item.datasetId || "未关联数据集"}
        </p>
        <div className="review-queue-card__footer">
          <span title={`复核编号：${item.id}\n数据版本：${item.datasetVersionId}\n模型：${item.modelVersionId}`}>
            {hasLLMAssistance ? "AI 建议已就绪" : "人工确认"} · 优先级 {item.priority}
          </span>
          <span>
            查看
            <Icon name="ChevronRight" size={14} />
          </span>
        </div>
      </div>
    </Link>
  );
}

function QueueThumbnail({ item }) {
  const [failed, setFailed] = useState(false);
  return (
    <div className="review-queue-card__image">
      {item.imageUrl && !failed ? (
        <img
          src={item.imageUrl}
          alt={item.topK[0]?.label || "待复核样本"}
          loading="lazy"
          decoding="async"
          onError={() => setFailed(true)}
        />
      ) : (
        <div className="review-queue-card__missing">
          <Icon name="Image" size={24} />
          <span>{failed ? "图片暂不可用" : "暂无图片"}</span>
        </div>
      )}
    </div>
  );
}

export function ReviewPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const requestedStatus = searchParams.get("status") || "pending";
  const statusFilter = REVIEW_STATUS_TABS.some(([value]) => value === requestedStatus) ? requestedStatus : "pending";
  const datasetFilter = searchParams.get("dataset_id") || "";
  const pageSize = 6;
  const requestedPage = Number(searchParams.get("page") || "1");
  const currentPage =
    Number.isSafeInteger(requestedPage) && requestedPage > 0 && requestedPage < 100000000 ? requestedPage : 1;
  const offset = (currentPage - 1) * pageSize;
  const { datasets: datasetItems } = useDatasets();
  const {
    reviewItems: apiReviewItems,
    pagination,
    loading,
    error,
    refresh,
  } = useReviewItems({
    status: statusFilter,
    datasetId: datasetFilter || undefined,
    limit: pageSize,
    offset,
  });
  const totalItems = pagination?.total ?? apiReviewItems.length;
  const totalPages = Math.max(1, Math.ceil(totalItems / pageSize));
  const safeCurrentPage = Math.min(currentPage, totalPages);
  useEffect(() => {
    if (!loading && !error && pagination?.offset === offset && currentPage > totalPages) {
      const next = new URLSearchParams(searchParams);
      if (totalPages > 1) next.set("page", String(totalPages));
      else next.delete("page");
      setSearchParams(next, { replace: true });
    }
  }, [loading, error, pagination?.offset, offset, currentPage, totalPages, searchParams, setSearchParams]);
  const oodCount = apiReviewItems.filter((item) => item.riskType === "ood_candidate").length;
  const lowConfidenceCount = apiReviewItems.filter((item) => item.riskType === "low_confidence").length;
  const lowMarginCount = apiReviewItems.filter((item) => item.riskType === "low_margin").length;
  const queryString = searchParams.toString();
  const datasetOptions = Array.from(
    new Map(
      [
        ...apiReviewItems.map((item) => [item.datasetId, item.datasetId]),
        datasetFilter ? [datasetFilter, datasetFilter] : null,
        ...datasetItems.map((dataset) => [dataset.id, dataset.name || dataset.id]),
      ].filter((entry) => entry?.[0]),
    ),
  );

  function updateReviewFilter(field, value) {
    const next = new URLSearchParams(searchParams);
    if (field === "status") {
      next.set("status", value || "pending");
    }
    if (field === "dataset_id") {
      if (value) next.set("dataset_id", value);
      else next.delete("dataset_id");
    }
    next.delete("page");
    setSearchParams(next);
  }

  function updateReviewPage(page) {
    const boundedPage = Math.max(1, Math.min(page, totalPages));
    const next = new URLSearchParams(searchParams);
    if (boundedPage > 1) next.set("page", String(boundedPage));
    else next.delete("page");
    setSearchParams(next);
  }

  return (
    <>
      <PageHero
        title="让人工只处理模型真正不确定的样本。"
        description="模型弃权和 OOD 候选进入复核队列；人工结论只进入反馈池，不直接污染训练集。"
        actions={
          <button className="ghost-button" onClick={refresh}>
            <Icon name="RefreshCw" size={16} />
            刷新
          </button>
        }
      />
      <div className="grid review review-queue-layout">
        <Panel
          title={statusFilter === "feedbacked" ? "历史复核" : statusFilter === "all" ? "全部复核项" : "待复核队列"}
          caption={
            loading
              ? "正在读取复核队列。"
              : `第 ${safeCurrentPage}/${totalPages} 页，本页 ${apiReviewItems.length} 条，共 ${totalItems} 条。`
          }
        >
          <div className="review-filter-bar">
            <div className="tabs">
              {REVIEW_STATUS_TABS.map(([value, label]) => (
                <button
                  className={`tab-button ${statusFilter === value ? "active" : ""}`}
                  key={value}
                  onClick={() => updateReviewFilter("status", value)}
                >
                  {label}
                </button>
              ))}
            </div>
            <label className="filter-select">
              <span>数据集</span>
              <PaginatedSelect
                aria-label="复核数据集"
                value={datasetFilter}
                onChange={(event) => updateReviewFilter("dataset_id", event.target.value)}
              >
                <option value="">全部数据集</option>
                {datasetOptions.map(([id, label]) => (
                  <option value={id} key={id}>
                    {label}
                  </option>
                ))}
              </PaginatedSelect>
            </label>
          </div>
          {error && (
            <div className="timeline-item">
              <div className="timeline-icon">
                <Icon name="AlertTriangle" size={18} />
              </div>
              <div>
                <strong>复核队列读取失败</strong>
                <div className="row-meta">{error.message}</div>
              </div>
              <StatusChip tone="risk">{compactStatusLabel("error")}</StatusChip>
            </div>
          )}
          {!error && apiReviewItems.length === 0 && (
            <div className="timeline-item">
              <div className="timeline-icon">
                <Icon name="CheckCircle2" size={18} />
              </div>
              <div>
                <strong>{loading ? "正在加载队列" : "当前筛选下没有样本"}</strong>
                <div className="row-meta">
                  {loading
                    ? "复核队列正在返回结果。"
                    : statusFilter === "pending"
                      ? "模型弃权和 OOD 拦截会自动进入这里。"
                      : "可以切回待复核或全部查看其他记录。"}
                </div>
              </div>
              <StatusChip tone={loading ? "info" : "default"}>
                {compactStatusLabel(loading ? "loading" : "clear")}
              </StatusChip>
            </div>
          )}
          <div className="review-queue-cards" aria-busy={loading}>
            {apiReviewItems.map((item) => (
              <ApiReviewCard
                item={item}
                datasetName={datasetOptions.find(([id]) => id === item.datasetId)?.[1]}
                queryString={queryString}
                key={item.id}
              />
            ))}
          </div>
          {!error && (
            <nav className="dataset-pagination" aria-label="复核队列分页">
              <div className="dataset-pagination__summary" aria-live="polite">
                <span>
                  {totalItems ? (safeCurrentPage - 1) * pageSize + 1 : 0}–
                  {Math.min(safeCurrentPage * pageSize, totalItems)} / 共 {totalItems} 条
                </span>
                <span className="dataset-pagination__size">6 条 / 页</span>
              </div>
              <div className="dataset-pagination__pages">
                <button
                  aria-label="上一页"
                  onClick={() => updateReviewPage(safeCurrentPage - 1)}
                  disabled={safeCurrentPage <= 1 || loading}
                >
                  <Icon name="ChevronLeft" size={16} />
                  上一页
                </button>
                {pageNumbers(safeCurrentPage, totalPages).map((page, index) =>
                  page === null ? (
                    <span key={`gap-${index}`} className="dataset-pagination__ellipsis">
                      …
                    </span>
                  ) : (
                    <button
                      key={page}
                      aria-label={`第 ${page} 页`}
                      aria-current={page === safeCurrentPage ? "page" : undefined}
                      disabled={loading}
                      onClick={() => updateReviewPage(page)}
                    >
                      {page}
                    </button>
                  ),
                )}
                <button
                  aria-label="下一页"
                  onClick={() => updateReviewPage(safeCurrentPage + 1)}
                  disabled={safeCurrentPage >= totalPages || loading}
                >
                  下一页
                  <Icon name="ChevronRight" size={16} />
                </button>
              </div>
            </nav>
          )}
        </Panel>
        <div className="review-side-stack">
          <Panel title="队列摘要" caption="统计当前页样本；历史入口在左侧状态切换中。">
            <div className="timeline">
              <div className="timeline-item">
                <div className="timeline-icon">
                  <Icon name="ShieldAlert" size={18} />
                </div>
                <div>
                  <strong>{oodCount} 条 OOD 候选</strong>
                  <div className="row-meta">只代表模型拒识，需要人工确认后才进入 OOD 压力池。</div>
                </div>
                <StatusChip tone="risk">OOD</StatusChip>
              </div>
              <div className="timeline-item">
                <div className="timeline-icon">
                  <Icon name="Gauge" size={18} />
                </div>
                <div>
                  <strong>{lowConfidenceCount} 条低置信</strong>
                  <div className="row-meta">置信度低于阈值，建议确认最终类别或标记不确定。</div>
                </div>
                <StatusChip tone="warn">低置信</StatusChip>
              </div>
              <div className="timeline-item">
                <div className="timeline-icon">
                  <Icon name="GitCompare" size={18} />
                </div>
                <div>
                  <strong>{lowMarginCount} 条低间隔</strong>
                  <div className="row-meta">top-1 与 top-2 接近，优先检查易混类别。</div>
                </div>
                <StatusChip tone="info">低间隔</StatusChip>
              </div>
            </div>
          </Panel>
        </div>
      </div>
    </>
  );
}

export function ReviewDetailPage({ showToast }) {
  const { reviewItemId = "" } = useParams();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const { reviewItem: item, loading, error, refresh } = useReviewItem(reviewItemId);
  const submitState = useSubmitReviewOutcome(reviewItemId);
  const reviewAssistant = useReviewAssistance(reviewItemId);
  const storedAssistance = assistanceFromMetadata(item?.assistanceMetadata);
  const reviewAssistance = reviewAssistant.assistance ?? storedAssistance;
  const [form, setForm] = useState({
    finalOutcome: "corrected_label",
    destination: "training_candidate",
    finalLabel: "",
    reviewerNote: "",
  });

  useEffect(() => {
    setForm((current) => ({ ...current, destination: destinationForOutcome(current.finalOutcome) }));
  }, [form.finalOutcome]);

  useEffect(() => {
    if (storedAssistance?.summary && reviewAssistant.status === "failed") {
      reviewAssistant.reset();
    }
  }, [storedAssistance?.summary, reviewAssistant.status, reviewAssistant.reset]);

  function updateReviewField(field, value) {
    setForm((current) => {
      const next = { ...current, [field]: value };
      if (field === "finalOutcome") next.destination = destinationForOutcome(value);
      return next;
    });
  }

  async function handleSubmit({ stay = false } = {}) {
    try {
      await submitState.submit({
        final_outcome: form.finalOutcome,
        destination: form.destination,
        final_label: form.finalLabel.trim() || null,
        reviewer_note: form.reviewerNote.trim() || null,
        reviewer: "local-reviewer",
      });
      if (stay) {
        showToast("复核结果已进入反馈池");
        refresh();
        return;
      }

      const preferredDatasetId = searchParams.get("dataset_id") || item?.datasetId || "";
      const sameDatasetNext = preferredDatasetId
        ? await listReviewItems({ status: "pending", datasetId: preferredDatasetId, limit: 1 })
        : [];
      const globalNext =
        sameDatasetNext.length > 0 ? sameDatasetNext : await listReviewItems({ status: "pending", limit: 1 });
      const nextItem = globalNext[0];
      if (nextItem) {
        const nextParams = new URLSearchParams();
        nextParams.set("status", "pending");
        if (nextItem.datasetId) nextParams.set("dataset_id", nextItem.datasetId);
        showToast("复核结果已进入反馈池，已打开下一张");
        navigate(`/review/${nextItem.id}?${nextParams.toString()}`);
        return;
      }

      const queueParams = new URLSearchParams();
      queueParams.set("status", "pending");
      if (preferredDatasetId) queueParams.set("dataset_id", preferredDatasetId);
      showToast("复核结果已进入反馈池，当前队列已清空");
      navigate(`/review?${queueParams.toString()}`);
    } catch (submitError) {
      showToast(submitError?.message ?? "复核提交失败");
    }
  }

  if (loading) {
    return (
      <>
        <PageHero
          title="复核详情"
          description="正在读取复核上下文。"
          actions={
            <Link className="ghost-button" to="/review">
              <Icon name="ArrowLeft" size={16} />
              返回队列
            </Link>
          }
        />
        <Panel title="加载中" caption="正在读取复核上下文。">
          <ProgressBar value={64} fill="#315fbd" shimmer />
        </Panel>
      </>
    );
  }

  if (!item || error) {
    return (
      <>
        <PageHero
          title="复核项不存在"
          description={error?.message ?? "没有找到这个复核项。"}
          actions={
            <Link className="ghost-button" to="/review">
              <Icon name="ArrowLeft" size={16} />
              返回队列
            </Link>
          }
        />
        <Panel title="无法打开复核详情" caption="请从真实队列中选择一个待复核样本。">
          <StatusChip tone="risk">{compactStatusLabel("not found")}</StatusChip>
        </Panel>
      </>
    );
  }

  const risk = reviewRisk(item);
  const statusInfo = reviewStatus(item);
  const queueSearch = searchParams.toString();
  const queuePath = `/review${queueSearch ? `?${queueSearch}` : ""}`;
  const candidateLabels = Array.from(new Set(item.topK.map((candidate) => candidate.label).filter(Boolean)));
  const destinationOptions = destinationOptionsForOutcome(form.finalOutcome);
  const requiresLabel = ["confirmed_label", "corrected_label"].includes(form.finalOutcome);
  const canSubmit =
    item.status === "pending" &&
    submitState.status !== "submitting" &&
    form.finalOutcome &&
    form.destination &&
    (!requiresLabel || form.finalLabel.trim());

  async function handleGenerateReviewAssistance() {
    try {
      await reviewAssistant.generate({ question: form.reviewerNote.trim() || null });
      showToast("LLM 辅助建议已生成");
      refresh();
    } catch (assistError) {
      showToast(assistError?.message ?? "LLM 辅助生成失败");
    }
  }

  return (
    <>
      <PageHero
        title={item.sampleId || item.id}
        description={`${item.datasetVersionId} · ${item.modelVersionId} · ${reviewReasonLabel(item.reason)}`}
        actions={
          <>
            <Link className="ghost-button" to={queuePath}>
              <Icon name="ArrowLeft" size={16} />
              返回队列
            </Link>
            <StatusChip tone={statusInfo.tone}>{statusInfo.label}</StatusChip>
          </>
        }
      />
      <div className="grid detail">
        <Panel title="模型证据" caption="保留推理当时的图像、top-k、阈值原因和近邻证据。">
          <ReviewImage item={item} risk={risk} detail />
          <div className="chips section-gap-small">
            <StatusChip tone={risk.tone}>{risk.label}</StatusChip>
            <StatusChip tone="info">优先级 {item.priority}</StatusChip>
            <StatusChip tone={item.decision.value === "reject_ood" ? "risk" : "warn"}>
              {decisionValueLabel(item.decision.value)}
            </StatusChip>
            <StatusChip tone="neutral">
              {item.inferenceRunId ? `运行 ${item.inferenceRunId}` : "历史记录无运行 ID"}
            </StatusChip>
          </div>
          <div className="evidence-metrics section-gap-small">
            <div>
              <span>confidence</span>
              <strong>{item.decision.confidence.toFixed(4)}</strong>
            </div>
            <div>
              <span>margin</span>
              <strong>{item.decision.margin.toFixed(4)}</strong>
            </div>
            <div>
              <span>ood score</span>
              <strong>{item.decision.oodScore?.toFixed?.(4) ?? "未返回"}</strong>
            </div>
          </div>
          <div className="reason-box section-gap-small">
            <strong>复核原因</strong>
            <span>{item.reasonCodes.map(reviewReasonLabel).join("、") || reviewReasonLabel(item.reason)}</span>
          </div>
          <div className="section-gap-small">
            {item.topK.length > 0 ? (
              item.topK.map((candidate, index) => (
                <CandidateBar
                  label={candidate.label}
                  score={candidate.score}
                  fill={index === 0 ? "#0891b2" : index === 1 ? "#a15c07" : "#315fbd"}
                  key={`${candidate.label}-${index}`}
                />
              ))
            ) : (
              <div className="row-meta">没有 top-k 候选。</div>
            )}
          </div>
          <details className="evidence-details section-gap-small">
            <summary>
              <span>
                <Icon name="GitCompare" size={16} />
                高级证据：近邻样本
              </span>
              <StatusChip tone="neutral">{item.nearestNeighbors.length} 条</StatusChip>
            </summary>
            <div className="neighbor-list">
              {item.nearestNeighbors.length > 0 ? (
                item.nearestNeighbors.map((neighbor, index) => (
                  <div className="neighbor-row" key={neighbor.sampleId || `${neighbor.label}-${index}`}>
                    <div className="neighbor-rank">{index + 1}</div>
                    <div>
                      <strong>{neighbor.label}</strong>
                      <div className="row-meta">{neighbor.sampleId || "unknown sample"}</div>
                    </div>
                    <div className="neighbor-distance">
                      <span>distance</span>
                      <strong>{neighbor.distance?.toFixed(4) ?? "未返回"}</strong>
                    </div>
                  </div>
                ))
              ) : (
                <div className="empty-evidence">
                  <Icon name="ImageOff" size={18} />
                  <span>暂无近邻证据。当前复核仍可基于原图、top-k 和阈值原因完成。</span>
                </div>
              )}
            </div>
          </details>
          <TechnicalDetails summary="来源追踪">
            run: {displayValue(item.inferenceRunId)}
            <br />
            event: {displayValue(item.inferenceEventId)}
            <br />
            review: {displayValue(item.id)}
            <br />
            dataset: {displayValue(item.datasetVersionId)}
            <br />
            model: {displayValue(item.modelVersionId)}
          </TechnicalDetails>
        </Panel>
        <Panel title="通用 LLM 辅助" caption="解释已有证据，不会替代人工判断或提交反馈池。">
          <LLMAssistanceBox
            title="复核辅助建议"
            caption="基于 top-k、阈值原因和近邻证据生成；人工仍必须独立提交最终结论。"
            assistance={reviewAssistance}
            status={reviewAssistant.status}
            error={reviewAssistant.error}
            onGenerate={handleGenerateReviewAssistance}
            disabled={item.status !== "pending"}
          />
        </Panel>
        <Panel
          title="人工复核"
          caption="人工结论进入反馈池；后续数据版本构建再决定是否采纳。"
          action={<StatusChip tone={statusInfo.tone}>{statusInfo.label}</StatusChip>}
        >
          <div className="grid">
            {item.feedback && (
              <>
                <div className="feedback-summary">
                  <div>
                    <span>最终结论</span>
                    <strong>{item.feedback.final_outcome}</strong>
                  </div>
                  <div>
                    <span>反馈池</span>
                    <strong>{item.feedback.destination}</strong>
                  </div>
                  <div>
                    <span>最终标签</span>
                    <strong>{item.feedback.final_label ?? "未填写"}</strong>
                  </div>
                  <div>
                    <span>备注</span>
                    <strong>{item.feedback.reviewer_note ?? "none"}</strong>
                  </div>
                </div>
                <div className="next-step-card">
                  <div>
                    <div className="chips">
                      <StatusChip tone="default">已进入反馈池</StatusChip>
                      <StatusChip
                        tone={
                          item.feedback.destination === "ood_stress"
                            ? "risk"
                            : item.feedback.destination === "training_candidate"
                              ? "default"
                              : "warn"
                        }
                      >
                        {feedbackDestinationLabel(item.feedback.destination)}
                      </StatusChip>
                    </div>
                    <strong>复核已完成，继续处理队列或检查反馈池</strong>
                    <div className="row-meta">
                      反馈池只是下一轮数据策展候选，不会自动写回训练集；继续下一张会回到当前筛选的待复核队列。
                    </div>
                  </div>
                  <div className="next-step-actions">
                    <Link
                      className="primary-button"
                      to={pathWithSearch("/review", [
                        ["status", "pending"],
                        ["dataset_id", item.datasetId],
                      ])}
                    >
                      <Icon name="UserCheck" size={16} />
                      继续下一张
                    </Link>
                    <Link
                      className="ghost-button"
                      to={pathWithSearch("/feedback", [
                        ["destination", item.feedback.destination],
                        ["dataset_id", item.datasetId],
                      ])}
                    >
                      <Icon name="DatabaseZap" size={16} />
                      打开反馈池
                    </Link>
                  </div>
                </div>
              </>
            )}
            {!item.feedback && (
              <>
                <div className="field-grid">
                  <div className="field">
                    <label>最终结论</label>
                    <select
                      value={form.finalOutcome}
                      onChange={(event) => updateReviewField("finalOutcome", event.target.value)}
                    >
                      <option value="corrected_label">纠正类别</option>
                      <option value="confirmed_label">确认模型类别</option>
                      <option value="ood">确认 OOD</option>
                      <option value="bad_image">坏图</option>
                      <option value="uncertain">仍不确定</option>
                      <option value="ignore">忽略</option>
                    </select>
                  </div>
                  <div className="field">
                    <label>反馈池</label>
                    <select
                      value={form.destination}
                      onChange={(event) => updateReviewField("destination", event.target.value)}
                    >
                      {destinationOptions.map(([value, label]) => (
                        <option value={value} key={value}>
                          {label}
                        </option>
                      ))}
                    </select>
                  </div>
                  <div className="field full-span">
                    <label>最终标签</label>
                    <input
                      list="review-label-options"
                      value={form.finalLabel}
                      onChange={(event) => updateReviewField("finalLabel", event.target.value)}
                      disabled={!requiresLabel}
                      placeholder={requiresLabel ? "选择或输入最终类别" : "该结论不需要类别标签"}
                    />
                    <datalist id="review-label-options">
                      {candidateLabels.map((label) => (
                        <option value={label} key={label}>
                          {label}
                        </option>
                      ))}
                    </datalist>
                  </div>
                </div>
                <div className="field">
                  <label>审核备注</label>
                  <textarea
                    value={form.reviewerNote}
                    onChange={(event) => updateReviewField("reviewerNote", event.target.value)}
                    placeholder="记录人工判断依据。"
                  />
                </div>
                {submitState.error && <div className="row-meta">提交失败：{submitState.error.message}</div>}
                <div className="toolbar">
                  <button className="primary-button" onClick={() => handleSubmit()} disabled={!canSubmit}>
                    <Icon name={submitState.status === "submitting" ? "LoaderCircle" : "Check"} size={16} />
                    {submitState.status === "submitting" ? "提交中" : "提交并下一张"}
                  </button>
                  <button className="ghost-button" onClick={() => handleSubmit({ stay: true })} disabled={!canSubmit}>
                    <Icon name="CheckCircle2" size={16} />
                    提交后留在此页
                  </button>
                  <button className="ghost-button" onClick={refresh}>
                    <Icon name="RefreshCw" size={16} />
                    刷新
                  </button>
                </div>
              </>
            )}
          </div>
        </Panel>
      </div>
    </>
  );
}

function FeedbackCard({ item }) {
  return (
    <div className="feedback-card">
      {item.imageUrl ? (
        <div className="review-image">
          <img src={item.imageUrl} alt={item.sampleId || item.id} />
          <span>{item.sampleId || item.id}</span>
        </div>
      ) : (
        <div className="feedback-missing-image">
          <Icon name="ImageOff" size={22} />
          <strong>无真实图片</strong>
          <span>{item.sampleId || item.id}</span>
        </div>
      )}
      <div className="feedback-card-body">
        <div className="chips">
          <StatusChip
            tone={
              item.destination === "ood_stress"
                ? "risk"
                : item.destination === "training_candidate"
                  ? "default"
                  : "warn"
            }
          >
            {feedbackDestinationLabel(item.destination)}
          </StatusChip>
          <StatusChip tone="info">{feedbackOutcomeLabel(item.finalOutcome)}</StatusChip>
        </div>
        <h3>{item.finalLabel || item.sampleId || item.id}</h3>
        <p className="small">
          {item.datasetVersionId || item.datasetId || "未知数据集"} · {item.modelVersionId || "未知模型"}
        </p>
        <p className="small">
          运行 {displayValue(item.inferenceRunId)} · 事件 {displayValue(item.inferenceEventId)}
        </p>
        <p className="small review-reason">{item.reviewerNote || "暂无人工备注。"}</p>
        <div className="toolbar spread section-gap-small">
          <span className="row-meta">
            {item.createdBy || "本地复核员"} ·{" "}
            {item.createdAt ? new Date(item.createdAt).toLocaleString() : "时间未记录"}
          </span>
          {item.destination === "training_candidate" && item.datasetId && (
            <Link
              className="secondary-button"
              to={`/datasets/${encodeURIComponent(item.datasetId)}#training-expansion`}
            >
              纳入训练集新版本
            </Link>
          )}
          {item.reviewItemId && (
            <Link className="ghost-button" to={`/review/${item.reviewItemId}?status=feedbacked`}>
              <Icon name="ExternalLink" size={16} />
              复核记录
            </Link>
          )}
        </div>
      </div>
    </div>
  );
}

function assistanceFromMetadata(metadata) {
  const raw = metadata?.llm_assistance;
  if (!raw) return null;
  return {
    advisoryOnly: raw.advisoryOnly ?? raw.advisory_only ?? true,
    summary: raw.summary ?? "",
    holisticAnalysis: raw.holisticAnalysis ?? raw.holistic_analysis ?? "",
    finalCategorySuggestion: raw.finalCategorySuggestion ??
      raw.final_category_suggestion ?? {
        label: "unknown",
        rationale: "",
      },
    inspectionNotes: raw.inspectionNotes ?? raw.inspection_notes ?? [],
    suggestedActions: raw.suggestedActions ?? raw.suggested_actions ?? [],
    riskFlags: raw.riskFlags ?? raw.risk_flags ?? [],
    model: raw.model ?? null,
    createdAt: raw.createdAt ?? raw.created_at ?? null,
    confidence: raw.confidence ?? "unknown",
  };
}

function AbstentionPolicyPanel({ feedbackItems, feedbackLoading = false, showToast }) {
  const proposeState = useProposeAbstentionPolicy();
  const activateState = useActivateAbstentionPolicy();
  const deactivateState = useDeactivateAbstentionPolicy();
  const [optimisticPolicy, setOptimisticPolicy] = useState(null);
  const [selectedPolicyId, setSelectedPolicyId] = useState("");
  const scopeOptions = useMemo(
    () =>
      Array.from(
        new Map(
          feedbackItems
            .filter((item) => item.datasetVersionId && item.modelVersionId)
            .map((item) => [
              `${item.datasetVersionId}::${item.modelVersionId}`,
              {
                key: `${item.datasetVersionId}::${item.modelVersionId}`,
                datasetVersionId: item.datasetVersionId,
                modelVersionId: item.modelVersionId,
                label: `${item.datasetVersionId} · ${item.modelVersionId}`,
              },
            ]),
        ).values(),
      ),
    [feedbackItems],
  );
  const scopeKeys = scopeOptions.map((item) => item.key).join("|");
  const [selectedScope, setSelectedScope] = useState(scopeOptions[0]?.key ?? "");
  const [targetRisk, setTargetRisk] = useState("0.05");
  useEffect(() => {
    if (scopeOptions.length === 0) {
      if (selectedScope) setSelectedScope("");
      return;
    }
    if (!selectedScope || !scopeOptions.some((item) => item.key === selectedScope))
      setSelectedScope(scopeOptions[0].key);
  }, [selectedScope, scopeKeys, scopeOptions]);
  const activeScope = scopeOptions.find((item) => item.key === selectedScope) ?? scopeOptions[0] ?? null;
  const { policies, loading, error, refresh } = useAbstentionPolicies({
    datasetVersionId: activeScope?.datasetVersionId,
    modelVersionId: activeScope?.modelVersionId,
    status: "all",
    limit: 12,
    enabled: Boolean(activeScope),
  });
  const policyMatchesActiveScope = (policy) =>
    Boolean(activeScope) &&
    policy?.datasetVersionId === activeScope.datasetVersionId &&
    policy?.modelVersionId === activeScope.modelVersionId;
  const scopedPolicies = useMemo(() => {
    const merged = new Map();
    policies.filter(policyMatchesActiveScope).forEach((policy) => merged.set(policy.id, policy));
    if (policyMatchesActiveScope(optimisticPolicy)) merged.set(optimisticPolicy.id, optimisticPolicy);
    return Array.from(merged.values()).sort((left, right) => {
      if (left.status === "active" && right.status !== "active") return -1;
      if (right.status === "active" && left.status !== "active") return 1;
      return String(right.createdAt ?? "").localeCompare(String(left.createdAt ?? ""));
    });
  }, [policies, optimisticPolicy, activeScope?.datasetVersionId, activeScope?.modelVersionId]);
  const policyIds = scopedPolicies.map((policy) => policy.id).join("|");
  useEffect(() => {
    if (scopedPolicies.length === 0) {
      if (selectedPolicyId) setSelectedPolicyId("");
      return;
    }
    if (!selectedPolicyId || !scopedPolicies.some((policy) => policy.id === selectedPolicyId))
      setSelectedPolicyId(scopedPolicies[0].id);
  }, [policyIds, scopedPolicies, selectedPolicyId]);
  const selectedPolicy = scopedPolicies.find((policy) => policy.id === selectedPolicyId) ?? scopedPolicies[0] ?? null;
  const {
    shadowDecisions,
    loading: shadowLoading,
    error: shadowError,
  } = useAbstentionShadowDecisions(selectedPolicy?.id, {
    diff: "all",
    limit: 12,
  });
  const diffCounts = selectedPolicy?.metrics?.decision_diff_counts ?? {};
  const parsedTargetRisk = Number(targetRisk);
  const targetRiskError =
    targetRisk.trim() === ""
      ? "请输入 0 到 1 之间的目标风险。"
      : !Number.isFinite(parsedTargetRisk) || parsedTargetRisk < 0 || parsedTargetRisk > 1
        ? "目标风险必须是 0 到 1 之间的数字。"
        : null;
  const canPropose = Boolean(activeScope) && !targetRiskError && proposeState.status !== "submitting";

  async function handleProposePolicy() {
    if (!activeScope) return;
    if (targetRiskError) {
      showToast?.(targetRiskError);
      return;
    }
    try {
      const policy = await proposeState.propose({
        dataset_version_id: activeScope.datasetVersionId,
        model_version_id: activeScope.modelVersionId,
        target_selective_risk: parsedTargetRisk,
        review_cost_per_item: 1.0,
        created_by: "local-operator",
      });
      setOptimisticPolicy(policy);
      setSelectedPolicyId(policy.id);
      refresh();
      showToast?.("已生成影子弃权策略");
    } catch {
      showToast?.("生成弃权策略失败");
    }
  }

  const gateChecks = selectedPolicy
    ? [
        {
          label: "反馈样本",
          ok: selectedPolicy.sourceFeedbackCount >= 5,
          detail: `${selectedPolicy.sourceFeedbackCount} / 5 条可评估反馈`,
        },
        {
          label: "风险约束",
          ok: selectedPolicy.estimatedSelectiveRisk <= selectedPolicy.targetSelectiveRisk,
          detail: `${formatPolicyPercent(selectedPolicy.estimatedSelectiveRisk)} / 目标 ${formatPolicyPercent(selectedPolicy.targetSelectiveRisk)}`,
        },
        {
          label: "策略状态",
          ok: selectedPolicy.status !== "active" && selectedPolicy.status !== "archived",
          detail: policyStatusDescription(selectedPolicy.status),
        },
      ]
    : [];
  const canActivate =
    Boolean(selectedPolicy) &&
    selectedPolicy.status !== "active" &&
    selectedPolicy.status !== "archived" &&
    gateChecks.every((item) => item.ok) &&
    activateState.status !== "submitting";
  const canDeactivate =
    Boolean(selectedPolicy) && selectedPolicy.status === "active" && deactivateState.status !== "submitting";

  async function handleActivatePolicy() {
    if (!selectedPolicy) return;
    try {
      const result = await activateState.activate(selectedPolicy.id, {
        activated_by: "local-operator",
        activation_reason: "manual activation from feedback pool gate",
        min_feedback_count: 5,
      });
      setOptimisticPolicy(result.policy);
      setSelectedPolicyId(result.policy.id);
      refresh();
      showToast?.("弃权策略已启用");
    } catch {
      showToast?.("启用弃权策略失败");
    }
  }

  async function handleDeactivatePolicy() {
    if (!selectedPolicy) return;
    try {
      const result = await deactivateState.deactivate(selectedPolicy.id, {
        deactivated_by: "local-operator",
        deactivation_reason: "manual deactivation from feedback pool gate",
      });
      setOptimisticPolicy(result.policy);
      setSelectedPolicyId(result.policy.id);
      refresh();
      showToast?.("弃权策略已停用");
    } catch {
      showToast?.("停用弃权策略失败");
    }
  }

  const activationDetail = activateState.error?.payload?.detail;
  const deactivationDetail = deactivateState.error?.payload?.detail;

  return (
    <Panel
      title="弃权策略注册表"
      caption="候选策略先做回放评估，只有 active 策略会影响真实推理阈值。"
      action={
        <StatusChip tone={scopedPolicies.some((policy) => policy.status === "active") ? "default" : "info"}>
          {scopedPolicies.some((policy) => policy.status === "active") ? "active gate" : "shadow only"}
        </StatusChip>
      }
    >
      <div className="review-filter-bar">
        <label className="filter-select wide">
          <span>反馈范围</span>
          <PaginatedSelect
            aria-label="反馈范围"
            value={selectedScope}
            onChange={(event) => setSelectedScope(event.target.value)}
            disabled={scopeOptions.length === 0}
          >
            {scopeOptions.length === 0 ? (
              <option value="">{feedbackLoading ? "正在读取反馈范围" : "等待反馈样本"}</option>
            ) : (
              scopeOptions.map((item) => (
                <option value={item.key} key={item.key}>
                  {item.label}
                </option>
              ))
            )}
          </PaginatedSelect>
        </label>
        <label className="filter-select compact">
          <span>目标风险</span>
          <input
            value={targetRisk}
            onChange={(event) => setTargetRisk(event.target.value)}
            inputMode="decimal"
            aria-invalid={Boolean(targetRiskError)}
          />
        </label>
        <button className="secondary-button" onClick={handleProposePolicy} disabled={!canPropose}>
          <Icon name={proposeState.status === "submitting" ? "LoaderCircle" : "Gauge"} size={16} />
          生成候选策略
        </button>
      </div>
      {targetRiskError && <div className="field-error">{targetRiskError}</div>}
      {proposeState.error && (
        <div className="timeline-item">
          <div className="timeline-icon">
            <Icon name="AlertTriangle" size={18} />
          </div>
          <div>
            <strong>策略生成失败</strong>
            <div className="row-meta">{proposeState.error.message}</div>
          </div>
          <StatusChip tone="risk">错误</StatusChip>
        </div>
      )}
      {error && (
        <div className="timeline-item">
          <div className="timeline-icon">
            <Icon name="AlertTriangle" size={18} />
          </div>
          <div>
            <strong>策略列表读取失败</strong>
            <div className="row-meta">{error.message}</div>
          </div>
          <StatusChip tone="risk">错误</StatusChip>
        </div>
      )}
      {!selectedPolicy && !error && (
        <div className="timeline-item">
          <div className="timeline-icon">
            <Icon name={loading ? "LoaderCircle" : "ShieldCheck"} size={18} />
          </div>
          <div>
            <strong>{loading ? "正在读取候选策略" : "还没有候选弃权策略"}</strong>
            <div className="row-meta">先完成一批人工复核，再从反馈池生成 shadow policy。</div>
          </div>
          <StatusChip tone={loading ? "info" : "warn"}>{loading ? "加载中" : "待生成"}</StatusChip>
        </div>
      )}
      {selectedPolicy && (
        <>
          <div className="policy-registry section-gap">
            {scopedPolicies.map((policy) => (
              <button
                className={`policy-row ${policy.id === selectedPolicy.id ? "selected" : ""}`}
                key={policy.id}
                type="button"
                onClick={() => setSelectedPolicyId(policy.id)}
              >
                <span>
                  <strong>{policy.id}</strong>
                  <small>
                    {formatPolicyPercent(policy.estimatedSelectiveRisk)} risk ·{" "}
                    {formatPolicyPercent(policy.estimatedCoverage)} coverage · {policy.sourceFeedbackCount} feedback
                  </small>
                </span>
                <StatusChip tone={policyStatusTone(policy.status)}>{policyStatusLabel(policy.status)}</StatusChip>
              </button>
            ))}
          </div>
          <div className="feedback-summary section-gap">
            <div>
              <span>coverage</span>
              <strong>{formatPolicyPercent(selectedPolicy.estimatedCoverage)}</strong>
            </div>
            <div>
              <span>selective risk</span>
              <strong>{formatPolicyPercent(selectedPolicy.estimatedSelectiveRisk)}</strong>
            </div>
            <div>
              <span>feedback</span>
              <strong>{selectedPolicy.sourceFeedbackCount}</strong>
            </div>
            <div>
              <span>review cost</span>
              <strong>{formatPolicyNumber(selectedPolicy.estimatedReviewCost)}</strong>
            </div>
          </div>
          <div className="timeline section-gap">
            <div className="timeline-item">
              <div className="timeline-icon">
                <Icon name="SlidersHorizontal" size={18} />
              </div>
              <div>
                <strong>阈值</strong>
                <div className="row-meta">
                  conf {formatPolicyNumber(selectedPolicy.tauConf)} · margin{" "}
                  {formatPolicyNumber(selectedPolicy.tauMargin)} · ood{" "}
                  {selectedPolicy.tauOod == null ? "未启用" : formatPolicyNumber(selectedPolicy.tauOod)}
                </div>
              </div>
              <StatusChip tone={policyStatusTone(selectedPolicy.status)}>
                {policyStatusLabel(selectedPolicy.status)}
              </StatusChip>
            </div>
            <div className="timeline-item">
              <div className="timeline-icon">
                <Icon name={selectedPolicy.status === "active" ? "RadioTower" : "ShieldAlert"} size={18} />
              </div>
              <div>
                <strong>{selectedPolicy.status === "active" ? "真实推理已启用" : "评估态策略"}</strong>
                <div className="row-meta">{policyStatusDescription(selectedPolicy.status)}</div>
              </div>
              <StatusChip tone={selectedPolicy.status === "active" ? "default" : "info"}>
                {selectedPolicy.status === "active" ? "会影响推理" : "不影响推理"}
              </StatusChip>
            </div>
          </div>
          <div className="gate-checks section-gap">
            {gateChecks.map((item) => (
              <div className={`gate-check ${item.ok ? "passed" : "blocked"}`} key={item.label}>
                <Icon name={item.ok ? "CheckCircle2" : "AlertTriangle"} size={16} />
                <span>
                  <strong>{item.label}</strong>
                  <small>{item.detail}</small>
                </span>
              </div>
            ))}
            <button className="secondary-button" onClick={handleActivatePolicy} disabled={!canActivate}>
              <Icon name={activateState.status === "submitting" ? "LoaderCircle" : "Power"} size={16} />
              人工启用
            </button>
            <button className="ghost-button" onClick={handleDeactivatePolicy} disabled={!canDeactivate}>
              <Icon name={deactivateState.status === "submitting" ? "LoaderCircle" : "PowerOff"} size={16} />
              停用 active
            </button>
          </div>
          {activateState.error && (
            <div className="timeline-item">
              <div className="timeline-icon">
                <Icon name="AlertTriangle" size={18} />
              </div>
              <div>
                <strong>启用失败</strong>
                <div className="row-meta">
                  {typeof activationDetail === "string" ? activationDetail : activateState.error.message}
                </div>
              </div>
              <StatusChip tone="risk">门禁未通过</StatusChip>
            </div>
          )}
          {deactivateState.error && (
            <div className="timeline-item">
              <div className="timeline-icon">
                <Icon name="AlertTriangle" size={18} />
              </div>
              <div>
                <strong>停用失败</strong>
                <div className="row-meta">
                  {typeof deactivationDetail === "string" ? deactivationDetail : deactivateState.error.message}
                </div>
              </div>
              <StatusChip tone="risk">操作失败</StatusChip>
            </div>
          )}
          {activateState.result?.gate && (
            <div className="timeline-item">
              <div className="timeline-icon">
                <Icon name="ShieldCheck" size={18} />
              </div>
              <div>
                <strong>后端门禁</strong>
                <div className="row-meta">
                  {activateState.result.message ?? JSON.stringify(activateState.result.gate)}
                </div>
              </div>
              <StatusChip tone="default">通过</StatusChip>
            </div>
          )}
          <div className="feedback-summary section-gap">
            {Object.entries(diffCounts).length === 0 ? (
              <div>
                <span>decision diff</span>
                <strong>暂无回放差异</strong>
              </div>
            ) : (
              Object.entries(diffCounts).map(([key, count]) => (
                <div key={key}>
                  <span>{shadowDiffLabel(key)}</span>
                  <strong>{count}</strong>
                </div>
              ))
            )}
          </div>
          <div className="timeline section-gap">
            {shadowError && (
              <div className="timeline-item">
                <div className="timeline-icon">
                  <Icon name="AlertTriangle" size={18} />
                </div>
                <div>
                  <strong>影子决策读取失败</strong>
                  <div className="row-meta">{shadowError.message}</div>
                </div>
                <StatusChip tone="risk">错误</StatusChip>
              </div>
            )}
            {!shadowError && shadowDecisions.length === 0 && (
              <div className="timeline-item">
                <div className="timeline-icon">
                  <Icon name={shadowLoading ? "LoaderCircle" : "Route"} size={18} />
                </div>
                <div>
                  <strong>{shadowLoading ? "正在读取影子决策" : "暂无影子决策"}</strong>
                  <div className="row-meta">生成策略时会回放已有反馈；后续推理也会追加 shadow row。</div>
                </div>
                <StatusChip tone="info">{shadowLoading ? "加载中" : "空"}</StatusChip>
              </div>
            )}
            {shadowDecisions.map((item) => (
              <div className="timeline-item" key={item.id}>
                <div className="timeline-icon">
                  <Icon name={item.decisionDiff === "same" ? "CheckCircle2" : "GitCompare"} size={18} />
                </div>
                <div>
                  <strong>{item.inferenceEventId}</strong>
                  <div className="row-meta">
                    {decisionValueLabel(item.currentDecision)} 到 {decisionValueLabel(item.shadowDecision)} · conf{" "}
                    {formatPolicyNumber(item.scoreSnapshot.confidence)} · margin{" "}
                    {formatPolicyNumber(item.scoreSnapshot.margin)}
                  </div>
                </div>
                <StatusChip tone={item.decisionDiff === "same" ? "default" : "warn"}>
                  {shadowDiffLabel(item.decisionDiff)}
                </StatusChip>
              </div>
            ))}
          </div>
        </>
      )}
    </Panel>
  );
}

export function FeedbackPage({ showToast }) {
  const [searchParams, setSearchParams] = useSearchParams();
  const llm = useLLMAssistance();
  const destinationFilter = searchParams.get("destination") || "all";
  const datasetFilter = searchParams.get("dataset_id") || "";
  const { datasets: datasetItems } = useDatasets();
  const { feedbackItems, loading, error, refresh } = useFeedbackItems({
    destination: destinationFilter,
    datasetId: datasetFilter || undefined,
    limit: 120,
  });
  const { feedbackItems: policyScopeFeedbackItems, loading: policyScopeLoading } = useFeedbackItems({
    destination: "all",
    limit: 300,
  });
  const poolCounts = FEEDBACK_DESTINATIONS.filter(([value]) => value !== "all").map(([value, label]) => [
    value,
    label,
    feedbackItems.filter((item) => item.destination === value).length,
  ]);
  const datasetOptions = Array.from(
    new Map(
      [
        ...datasetItems.map((dataset) => [dataset.id, dataset.name || dataset.id]),
        ...feedbackItems.map((item) => [item.datasetId, item.datasetId]),
        datasetFilter ? [datasetFilter, datasetFilter] : null,
      ].filter((entry) => entry?.[0]),
    ),
  );

  function updateFeedbackFilter(field, value) {
    const next = new URLSearchParams(searchParams);
    if (field === "destination") next.set("destination", value || "all");
    if (field === "dataset_id") {
      if (value) next.set("dataset_id", value);
      else next.delete("dataset_id");
    }
    setSearchParams(next);
  }

  async function handleGenerateCurationAdvice() {
    await llm.generate({
      task: "feedback_curation",
      context: {
        destination_filter: destinationFilter,
        dataset_filter: datasetFilter || null,
        pool_counts: Object.fromEntries(poolCounts.map(([value, , count]) => [value, count])),
        sample_items: feedbackItems.slice(0, 12).map((item) => ({
          feedback_item_id: item.id,
          destination: item.destination,
          final_outcome: item.finalOutcome,
          final_label: item.finalLabel,
          dataset_version_id: item.datasetVersionId,
          reviewer_note: item.reviewerNote,
        })),
      },
    });
  }

  return (
    <>
      <PageHero
        title="反馈池"
        description="人工确认的候选可在待发布区补充到来源版本的训练集；生成新版本，不修改历史标签，不自动触发训练。"
        actions={
          <>
            <Link className="ghost-button" to="/annotation?tab=publish&source=feedback">
              回流待发布区
            </Link>
            <Link className="ghost-button" to="/review?status=feedbacked">
              <Icon name="UserCheck" size={16} />
              复核历史
            </Link>
            <button className="ghost-button" onClick={refresh}>
              <Icon name="RefreshCw" size={16} />
              刷新
            </button>
          </>
        }
      />
      <div className="grid detail">
        <Panel title="反馈池列表" caption={loading ? "正在读取反馈池。" : `${feedbackItems.length} 条反馈。`}>
          <div className="review-filter-bar">
            <div className="tabs">
              {FEEDBACK_DESTINATIONS.map(([value, label]) => (
                <button
                  className={`tab-button ${destinationFilter === value ? "active" : ""}`}
                  key={value}
                  onClick={() => updateFeedbackFilter("destination", value)}
                >
                  {label}
                </button>
              ))}
            </div>
            <label className="filter-select">
              <span>数据集</span>
              <PaginatedSelect
                aria-label="反馈数据集"
                value={datasetFilter}
                onChange={(event) => updateFeedbackFilter("dataset_id", event.target.value)}
              >
                <option value="">全部数据集</option>
                {datasetOptions.map(([id, label]) => (
                  <option value={id} key={id}>
                    {label}
                  </option>
                ))}
              </PaginatedSelect>
            </label>
          </div>
          {error && (
            <div className="timeline-item">
              <div className="timeline-icon">
                <Icon name="AlertTriangle" size={18} />
              </div>
              <div>
                <strong>反馈池读取失败</strong>
                <div className="row-meta">{error.message}</div>
              </div>
              <StatusChip tone="risk">{compactStatusLabel("error")}</StatusChip>
            </div>
          )}
          {!error && feedbackItems.length === 0 && (
            <div className="timeline-item">
              <div className="timeline-icon">
                <Icon name="DatabaseZap" size={18} />
              </div>
              <div>
                <strong>{loading ? "正在加载反馈池" : "当前筛选下没有反馈"}</strong>
                <div className="row-meta">完成人工复核后，反馈会按目的地进入这里。</div>
              </div>
              <StatusChip tone={loading ? "info" : "default"}>
                {compactStatusLabel(loading ? "loading" : "empty")}
              </StatusChip>
            </div>
          )}
          <div className="feedback-list">
            {feedbackItems.map((item) => (
              <FeedbackCard item={item} key={item.id} />
            ))}
          </div>
        </Panel>
        <AbstentionPolicyPanel
          feedbackItems={policyScopeFeedbackItems}
          feedbackLoading={policyScopeLoading}
          showToast={showToast}
        />
        <Panel title="策展门禁" caption="当前只展示候选池，不会自动生成新数据集版本。">
          <div className="feedback-summary">
            {poolCounts.map(([value, label, count]) => (
              <div key={value}>
                <span>{label}</span>
                <strong>{count}</strong>
              </div>
            ))}
          </div>
          <div className="timeline section-gap">
            <div className="timeline-item">
              <div className="timeline-icon">
                <Icon name="ShieldCheck" size={18} />
              </div>
              <div>
                <strong>不会直接污染训练集</strong>
                <div className="row-meta">训练仍只能选择不可变 dataset_version。</div>
              </div>
              <StatusChip tone="default">{compactStatusLabel("guarded")}</StatusChip>
            </div>
            <div className="timeline-item">
              <div className="timeline-icon">
                <Icon name="Database" size={18} />
              </div>
              <div>
                <strong>下一步：数据策展</strong>
                <div className="row-meta">后续会把已采纳反馈冻结成新的 dataset version。</div>
              </div>
              <StatusChip tone="warn">{compactStatusLabel("deferred")}</StatusChip>
            </div>
            <div className="timeline-item">
              <div className="timeline-icon">
                <Icon name="Route" size={18} />
              </div>
              <div>
                <strong>发布前再消费</strong>
                <div className="row-meta">模型发布门禁应检查 OOD 压力池、坏图池和争议池处理状态。</div>
              </div>
              <StatusChip tone="info">{compactStatusLabel("gate")}</StatusChip>
            </div>
          </div>
          <LLMAssistanceBox
            title="LLM 策展建议"
            caption="只根据当前反馈池聚合给出策展建议，不会创建 dataset version 或触发训练。"
            assistance={llm.assistance}
            status={llm.status}
            error={llm.error}
            onGenerate={handleGenerateCurationAdvice}
            disabled={feedbackItems.length === 0}
          />
        </Panel>
      </div>
    </>
  );
}
