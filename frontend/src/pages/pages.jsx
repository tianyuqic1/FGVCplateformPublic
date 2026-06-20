import { useEffect, useRef, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { uploadImagefolder } from "../api/datasets.js";
import { runInference, runInferenceUpload } from "../api/inference.js";
import { listReviewItems } from "../api/reviews.js";
import { createTrainingRun } from "../api/trainingRuns.js";
import { useDataset, useDatasetSamplePreviews, useDatasets } from "../hooks/useDatasets.js";
import { useRecentJobs } from "../hooks/useJobs.js";
import { useLLMAssistance, useReviewAssistance } from "../hooks/useLLMAssistance.js";
import { useFeedbackItems, useReviewItem, useReviewItems, useSubmitReviewOutcome } from "../hooks/useReviews.js";
import { useTrainingRun, useTrainingRuns } from "../hooks/useTrainingRuns.js";
import { Icon } from "../components/icons.jsx";
import {
  CandidateBar,
  CurveRow,
  GateRow,
  MetricCard,
  Panel,
  ProgressBar,
  StatusChip,
  TaskItem,
  VisualPlaceholder,
} from "../components/ui.jsx";
import { PageHero } from "../components/AppShell.jsx";

const pipelineNodes = [
  { id: "import", title: "数据导入", description: "生成 dataset version", icon: "FolderInput", progress: 100 },
  { id: "audit", title: "数据审计", description: "坏图、错标、长尾", icon: "BadgeCheck", progress: 100 },
  { id: "features", title: "特征提取", description: "DINOv3 embedding", icon: "Cpu", progress: 100 },
  { id: "training", title: "分类头训练", description: "Linear / MLP", icon: "FlaskConical", progress: 72, running: true },
  { id: "calibration", title: "校准弃权", description: "coverage-risk", icon: "CircleGauge", progress: 38, running: true },
  { id: "release", title: "发布回流", description: "灰度、监控、回流", icon: "Rocket", progress: 0 },
];

const IMAGE_FOLDER_EXTENSIONS = new Set([".jpg", ".jpeg", ".png", ".bmp", ".webp"]);
const IMAGE_FOLDER_SPLITS = new Set(["train", "val", "test"]);

function datasetStatus(dataset) {
  if (dataset.status === "production") return { label: "生产可推理", tone: "default" };
  if (dataset.status === "ready") return { label: "可训练", tone: "default" };
  if (dataset.status === "calibrating") return { label: "待校准", tone: "warn" };
  return { label: "训练中", tone: "info" };
}

function riskTone(route) {
  if (route === "ood") return "risk";
  if (route === "bad-image") return "neutral";
  return "warn";
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
  if (outcome === "uncertain") return [["taxonomy_dispute", "类别争议池"], ["ignore", "忽略池"]];
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

const SAMPLE_IMAGES = {
  bird: "/api/sample-assets/test/bird/bird_001.png",
  ship: "/api/sample-assets/test/ship/ship_001.png",
  deer: "/api/sample-assets/test/deer/deer_001.png",
  automobile: "/api/sample-assets/test/automobile/automobile_001.png",
  frog: "/api/sample-assets/test/frog/frog_001.png",
  ood: "/api/sample-assets/test/ship/ship_003.png",
  defect: "/api/sample-assets/test/automobile/automobile_003.png",
};

function apiAssetUrl(path) {
  const base = import.meta.env?.VITE_API_BASE_URL?.replace(/\/$/, "") ?? "";
  return path?.startsWith("/") ? `${base}${path}` : path;
}

function sampleImageFor(key = "bird") {
  return apiAssetUrl(SAMPLE_IMAGES[key] ?? SAMPLE_IMAGES.bird);
}

function imageFolderRelativePath(file) {
  return (file.webkitRelativePath || file.name || "").replace(/\\/g, "/");
}

function imageFolderExtension(path) {
  const match = path.toLowerCase().match(/\.[^.]+$/);
  return match ? match[0] : "";
}

function ignoredFolderPath(path) {
  return path.split("/").some((part) => part === "__MACOSX" || part.startsWith("."));
}

function commonPathPrefix(paths) {
  if (!paths.length) return [];
  let prefix = paths[0].split("/").filter(Boolean);
  paths.slice(1).forEach((path) => {
    const parts = path.split("/").filter(Boolean);
    const next = [];
    for (let index = 0; index < Math.min(prefix.length, parts.length); index += 1) {
      if (prefix[index] !== parts[index]) break;
      next.push(prefix[index]);
    }
    prefix = next;
  });
  return prefix;
}

function validateImageFolderParts(partsList) {
  if (partsList.some((parts) => parts.length < 2)) return null;
  const topLevel = new Set(partsList.map((parts) => parts[0]));
  const hasSplit = [...topLevel].some((part) => IMAGE_FOLDER_SPLITS.has(part));
  const splitMode = hasSplit && [...topLevel].every((part) => IMAGE_FOLDER_SPLITS.has(part));
  if (splitMode) {
    if (partsList.some((parts) => parts.length < 3)) return null;
    const classes = [...new Set(partsList.map((parts) => parts[1]))].sort();
    if (classes.length < 2) return null;
    return {
      format: "split/class/image",
      classes,
      splits: [...topLevel].sort(),
      imageCount: partsList.length,
    };
  }
  if ([...topLevel].some((part) => IMAGE_FOLDER_SPLITS.has(part))) return null;
  const classes = [...topLevel].sort();
  if (classes.length < 2) return null;
  return {
    format: "class/image",
    classes,
    splits: [],
    imageCount: partsList.length,
  };
}

function analyzeImageFolderFiles(files) {
  const selectedFiles = Array.from(files ?? []);
  if (!selectedFiles.length) {
    return { valid: false, error: "请选择一个包含图片的文件夹。", files: [], rootName: "" };
  }

  const usableFiles = [];
  for (const file of selectedFiles) {
    const relativePath = imageFolderRelativePath(file);
    if (!relativePath || ignoredFolderPath(relativePath)) continue;
    if (!IMAGE_FOLDER_EXTENSIONS.has(imageFolderExtension(relativePath))) {
      return { valid: false, error: `不支持的文件类型：${relativePath}`, files: selectedFiles, rootName: "" };
    }
    usableFiles.push({ file, relativePath });
  }

  if (!usableFiles.length) {
    return { valid: false, error: "文件夹里没有 jpg、jpeg、png、bmp 或 webp 图片。", files: selectedFiles, rootName: "" };
  }

  const prefix = commonPathPrefix(usableFiles.map((item) => item.relativePath));
  let summary = null;
  for (let prefixLength = 0; prefixLength <= prefix.length; prefixLength += 1) {
    const partsList = usableFiles.map((item) => item.relativePath.split("/").filter(Boolean).slice(prefixLength));
    const candidate = validateImageFolderParts(partsList);
    if (candidate) summary = { ...candidate, strippedPrefix: prefix.slice(0, prefixLength) };
  }

  if (!summary) {
    return {
      valid: false,
      error: "未检测到合法 ImageFolder：请使用 class/image 或 train|val|test/class/image 结构，且至少包含两个类别。",
      files: selectedFiles,
      rootName: prefix[0] ?? "",
    };
  }

  return {
    valid: true,
    error: null,
    files: usableFiles.map((item) => item.file),
    rootName: summary.strippedPrefix.at(-1) ?? prefix[0] ?? "local-imagefolder",
    ...summary,
  };
}

function datasetIdFromFolderName(name) {
  const normalized = name
    .trim()
    .toLowerCase()
    .replace(/imagefolder/g, "")
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
  return normalized || "local-dataset";
}

function jobStatus(job) {
  if (job.status === "succeeded") return { label: "完成", tone: "default", icon: "Check" };
  if (job.status === "running") return { label: "运行中", tone: "warn", icon: "LoaderCircle" };
  if (job.status === "failed") return { label: "失败", tone: "risk", icon: "AlertTriangle" };
  if (job.status === "cancelled") return { label: "已取消", tone: "neutral", icon: "Ban" };
  return { label: "排队中", tone: "info", icon: "Clock" };
}

function jobTarget(job) {
  return job.datasetVersionId ?? job.datasetId ?? "未绑定数据集";
}

function trainingStatus(run) {
  if (run.status === "succeeded" || run.status === "done") return { label: "完成", tone: "default", icon: "Check" };
  if (run.status === "running") return { label: "运行中", tone: "warn", icon: "LoaderCircle" };
  if (run.status === "failed") return { label: "失败", tone: "risk", icon: "AlertTriangle" };
  if (run.status === "cancelled") return { label: "已取消", tone: "neutral", icon: "Ban" };
  return { label: "排队中", tone: "info", icon: "Clock" };
}

function inferenceDecisionStatus(decision) {
  if (decision?.value === "accept") return { label: "可直出", tone: "default", icon: "Check" };
  if (decision?.value === "reject_ood") return { label: "OOD 拦截", tone: "risk", icon: "ShieldAlert" };
  return { label: "进入复核", tone: "warn", icon: "UserCheck" };
}

function inferenceDecisionCopy(decision) {
  if (decision?.value === "accept") {
    return {
      title: "模型接受该结果",
      body: "置信度、类别间隔和 OOD 距离都满足当前阈值；系统只记录推理事件，不默认进入人工复核。",
    };
  }
  if (decision?.value === "reject_ood") {
    return {
      title: "模型拒识为 OOD 候选",
      body: "样本离训练特征空间过远，系统会把它送入人工复核；人工确认后才进入 OOD 压力池。",
    };
  }
  return {
    title: "模型选择弃权",
    body: "模型没有达到自动直出的阈值，通常是低置信或 top-1/top-2 太接近；系统会把它送入人工复核。",
  };
}

function reasonLabel(reason) {
  const labels = {
    confidence_below_threshold: "置信度低于阈值",
    top1_top2_margin_below_threshold: "Top-1 / Top-2 间隔不足",
    embedding_distance_above_threshold: "Embedding 距离超过 OOD 阈值",
    meets_acceptance_thresholds: "满足自动直出阈值",
  };
  return labels[reason] ?? reason;
}

function ReviewCard({ item }) {
  const imageKey = item.route === "ood" ? "ood" : item.route === "bad-image" ? "defect" : "bird";
  return (
    <Link className="sample-card clickable" to={`/review/${item.id}`}>
      <SampleImage src={sampleImageFor(imageKey)} label={item.id} compact low={item.route !== "ood"} />
      <div>
        <div className="chips">
          <StatusChip tone={riskTone(item.route)}>{item.risk}</StatusChip>
          <StatusChip tone="info">{item.datasetName}</StatusChip>
        </div>
        <h3>{item.title}</h3>
        <p className="small">
          {item.modelCandidate.label} {item.modelCandidate.score.toFixed(2)} · {item.secondCandidate.label}{" "}
          {item.secondCandidate.score.toFixed(2)}
        </p>
        <p className="small">{item.assistance}</p>
      </div>
    </Link>
  );
}

function SampleImage({ src, label, compact = false, low = false }) {
  return (
    <div className={`sample-image ${compact ? "compact" : ""} ${low ? "low" : ""}`}>
      <img src={src} alt={label} />
      <span>{label}</span>
    </div>
  );
}

function SampleVisualCard({ src, label, meta = "来自当前数据集", low = false }) {
  return (
    <div className="image-card">
      <SampleImage src={src} label={label} low={low} />
      <div className="caption">
        <strong>{label}</strong>
        <div className="row-meta">{meta}</div>
      </div>
    </div>
  );
}

function DatasetSamplePreviewGrid({ samples, loading, error, compact = false }) {
  if (loading) {
    return <div className="route-box"><div><strong>正在加载样本预览</strong><div className="row-meta">从当前 dataset version 读取真实样本图片。</div></div><StatusChip tone="info">loading</StatusChip></div>;
  }
  if (error) {
    return <div className="route-box"><div><strong>样本预览加载失败</strong><div className="row-meta">{error.message}</div></div><StatusChip tone="risk">error</StatusChip></div>;
  }
  if (!samples.length) {
    return <div className="route-box"><div><strong>暂无样本预览</strong><div className="row-meta">当前 dataset version 没有返回可预览图片。</div></div><StatusChip tone="warn">empty</StatusChip></div>;
  }
  return (
    <div className={`image-grid ${compact ? "compact" : ""}`}>
      {samples.map((sample) => (
        <SampleVisualCard
          key={sample.sampleId ?? `${sample.label}-${sample.imageUrl}`}
          src={apiAssetUrl(sample.imageUrl)}
          label={sample.label}
          meta={`${sample.split} · ${sample.sampleId ?? "sample"}`}
        />
      ))}
    </div>
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

function ApiReviewCard({ item, queryString = "" }) {
  const risk = reviewRisk(item);
  const statusInfo = reviewStatus(item);
  const topCandidate = item.topK[0];
  const secondCandidate = item.topK[1];
  const hasLLMAssistance = Boolean(item.assistanceMetadata?.llm_assistance);
  const target = `/review/${item.id}${queryString ? `?${queryString}` : ""}`;
  return (
    <Link className="sample-card clickable" to={target}>
      <ReviewImage item={item} risk={risk} />
      <div>
        <div className="chips">
          <StatusChip tone={risk.tone}>{risk.label}</StatusChip>
          <StatusChip tone={statusInfo.tone}>{statusInfo.label}</StatusChip>
          <StatusChip tone="info">P{item.priority}</StatusChip>
          <StatusChip tone={hasLLMAssistance ? "default" : "neutral"}>{hasLLMAssistance ? "LLM 已生成" : "LLM 未生成"}</StatusChip>
        </div>
        <h3>{item.sampleId || item.id}</h3>
        <p className="small">{item.datasetVersionId} · {item.modelVersionId}</p>
        <p className="small">
          {topCandidate ? `${topCandidate.label} ${topCandidate.score.toFixed(2)}` : "无候选"} ·{" "}
          {secondCandidate ? `${secondCandidate.label} ${secondCandidate.score.toFixed(2)}` : "无 second"}
        </p>
        <p className="small review-reason">{item.reason}</p>
      </div>
    </Link>
  );
}

function DatasetTable({ items = [] }) {
  if (items.length === 0) {
    return (
      <div className="empty-state">
        <Icon name="Database" size={24} />
        <strong>暂无真实数据集</strong>
        <span>导入 ImageFolder 后，Control-plane API 会在这里返回 dataset version。</span>
      </div>
    );
  }

  return (
    <div className="data-table">
      <div className="data-row head">
        <div>数据集</div>
        <div>类别</div>
        <div>样本</div>
        <div>版本</div>
        <div>状态</div>
        <div />
      </div>
      {items.map((dataset) => {
        const state = datasetStatus(dataset);
        return (
          <Link className="data-row clickable" key={dataset.id} to={`/datasets/${dataset.id}`}>
            <div>
              <strong>{dataset.name}</strong>
              <div className="row-meta">{dataset.description}</div>
            </div>
            <div>{dataset.classes} 类</div>
            <div>{dataset.images.toLocaleString()}</div>
            <div>{dataset.version}</div>
            <div>
              <StatusChip tone={state.tone}>{state.label}</StatusChip>
            </div>
            <span className="icon-button">
              <Icon name="ChevronRight" size={16} />
            </span>
          </Link>
        );
      })}
    </div>
  );
}

function RunRow({ run }) {
  const state = trainingStatus(run);
  const done = run.status === "succeeded" || run.status === "done";
  return (
    <Link className="timeline-item clickable" to={`/training/${run.id}`}>
      <div className="timeline-icon">
        <Icon name={state.icon} size={18} />
      </div>
      <div>
        <strong>{run.name}</strong>
        <div className="row-meta">
          {run.datasetName} · {run.metric}
        </div>
        <ProgressBar value={run.progress} fill={done ? "#0f766e" : "#a15c07"} shimmer={!done} />
      </div>
      <StatusChip tone={state.tone}>{state.label}</StatusChip>
    </Link>
  );
}

function JobRow({ job, selected = false }) {
  const state = jobStatus(job);
  return (
    <Link className={`timeline-item clickable ${selected ? "selected" : ""}`} to={`/pipelines?job_id=${encodeURIComponent(job.id)}`}>
      <div className="timeline-icon">
        <Icon name={state.icon} size={18} />
      </div>
      <div>
        <strong>{job.jobType}</strong>
        <div className="row-meta">
          {jobTarget(job)} · {job.message || job.id}
        </div>
        <ProgressBar value={job.progress} fill={job.status === "failed" ? "#b4233c" : job.status === "running" ? "#a15c07" : job.status === "cancelled" ? "#6b7280" : "#0f766e"} shimmer={job.status === "running"} />
      </div>
      <StatusChip tone={state.tone}>{state.label}</StatusChip>
    </Link>
  );
}

function RecentJobsPanel({ limit = 5, selectedJobId = "" }) {
  const { jobs, source, loading } = useRecentJobs(limit);
  const sourceLabel = loading ? "正在连接 Control-plane API" : source === "api" ? "Control-plane API" : "Control-plane API 暂不可用";
  const selectedJob = selectedJobId ? jobs.find((job) => job.id === selectedJobId) : null;

  return (
    <Panel title="任务状态" caption={`${sourceLabel} · queued / running / succeeded / failed / cancelled。`}>
      {selectedJobId && !selectedJob && (
        <div className="timeline-item">
          <div className="timeline-icon"><Icon name="Search" size={18} /></div>
          <div><strong>当前列表没有这个 job</strong><div className="row-meta">{selectedJobId} · 请刷新或扩大任务查询范围。</div></div>
          <StatusChip tone="warn">missing</StatusChip>
        </div>
      )}
      <div className="timeline">{jobs.map((job) => <JobRow job={job} selected={job.id === selectedJobId} key={job.id} />)}</div>
    </Panel>
  );
}

function ModelCard({ model }) {
  const tone = model.state === "production" ? "default" : model.state === "candidate" || model.state === "staging" ? "info" : "warn";
  const accuracy = Number.isFinite(Number(model.accuracy)) ? Math.round(Number(model.accuracy)) : 0;
  return (
    <Link className="card clickable" to={`/models/${model.id}`}>
      <StatusChip tone={tone}>{model.state}</StatusChip>
      <h3>{model.id}</h3>
      <p>{model.description}</p>
      <ProgressBar value={accuracy} fill={model.state === "experiment" ? "#a15c07" : "#0f766e"} />
    </Link>
  );
}

function modelRecordFromRun(run) {
  const metrics = run.metrics ?? {};
  const accuracy = Number.isFinite(Number(metrics.accuracy)) ? Number(metrics.accuracy) * 100 : null;
  const coverage = Number.isFinite(Number(metrics.expected_coverage)) ? Number(metrics.expected_coverage) * 100 : null;
  const selectiveRisk = Number.isFinite(Number(metrics.expected_selective_risk)) ? Number(metrics.expected_selective_risk) * 100 : null;
  return {
    id: run.modelVersionId,
    state: run.status === "succeeded" ? "candidate" : run.status,
    source: "training",
    description: `${run.datasetVersionId ?? "dataset version unknown"} · ${run.metric ?? "metrics pending"}`,
    datasetId: run.datasetId,
    datasetVersionId: run.datasetVersionId,
    featureArtifactId: run.featureArtifactId,
    modelArtifactId: run.modelArtifactId,
    thresholdStrategyId: run.thresholdStrategyArtifactId,
    reportArtifactId: run.reportArtifactId,
    calibrationArtifactId: run.calibrationArtifactId,
    accuracy,
    coverage,
    selectiveRisk,
    runId: run.id,
    jobId: run.jobId,
    error: run.error,
  };
}

function PipelineNode({ node }) {
  return (
    <div className={`pipeline-node ${node.running ? "running" : ""}`}>
      <Icon name={node.icon} size={20} />
      <h3>{node.title}</h3>
      <p className="small">{node.description}</p>
      <ProgressBar value={node.progress} fill={node.running ? "#a15c07" : "#0f766e"} />
      <div className="row-meta">{node.progress}%</div>
    </div>
  );
}

export function DashboardPage({ showToast }) {
  const { datasets: datasetItems } = useDatasets();
  const { reviewItems: pendingReviewItems, loading: reviewLoading } = useReviewItems({ status: "pending", limit: 100 });
  const { trainingRuns: dashboardRuns, source: trainingSource, loading: trainingLoading } = useTrainingRuns();
  const reviewCountLabel = reviewLoading ? "..." : String(pendingReviewItems.length);
  const activeRuns = dashboardRuns.filter((run) => ["queued", "running"].includes(run.status));
  const candidateRuns = dashboardRuns.filter((run) => run.modelVersionId && run.status === "succeeded");
  const failedRuns = dashboardRuns.filter((run) => run.status === "failed");
  const oodReviewCount = pendingReviewItems.filter((item) => item.riskType === "ood_candidate").length;
  const highRiskReviewCount = pendingReviewItems.filter((item) => item.priority <= 40 || item.riskType === "ood_candidate").length;
  const activeRunLabel = trainingLoading ? "..." : String(activeRuns.length);
  const trainingCaption =
    trainingSource === "api"
      ? `${candidateRuns.length} 个候选版本 · 失败 ${failedRuns.length}`
      : "Training API 不可用，未展示本地 mock 训练数据";

  return (
    <>
      <PageHero
        title="先处理风险，再发布模型。"
        description="这里不再是展示页，而是每天打开后能行动的算法平台工作台：看待处理队列、训练状态、数据风险、模型发布门禁和 OOD 告警。"
        actions={
          <>
            <button className="ghost-button" disabled>
              <Icon name="RefreshCw" size={16} />
              刷新待接入
            </button>
            <Link className="primary-button" to="/review">
              <Icon name="UserCheck" size={16} />
              处理复核
            </Link>
          </>
        }
      />
      <div className="grid metrics">
        <MetricCard title="待复核样本" value={reviewCountLabel} caption="Review API · abstain/OOD" fill="#a15c07" percent={Math.min(100, pendingReviewItems.length * 18)} icon="UserCheck" to="/review" />
        <MetricCard title="运行中训练" value={activeRunLabel} caption={trainingCaption} fill="#315fbd" percent={Math.min(100, activeRuns.length * 36)} icon="FlaskConical" to="/training" />
        <MetricCard title="生产覆盖率" value="--" caption="Model Registry API 待接入" fill="#0f766e" percent={0} icon="Gauge" to="/models" />
        <MetricCard title="OOD 告警" value={reviewLoading ? "..." : String(oodReviewCount)} caption="来自待复核 OOD 候选" fill="#b4233c" percent={Math.min(100, oodReviewCount * 34)} icon="ShieldAlert" to="/review" />
      </div>
      <div className="grid two section-gap">
        <Panel
          title="优先任务"
          caption="按风险和阻塞程度排序。"
          action={
            <Link className="ghost-button" to="/pipelines">
              <Icon name="Route" size={16} />
              查看流水线
            </Link>
          }
        >
          <div className="timeline">
            <TaskItem icon={highRiskReviewCount > 0 ? "AlertTriangle" : "CheckCircle2"} title={highRiskReviewCount > 0 ? `处理 ${highRiskReviewCount} 条高风险复核样本` : "当前没有高风险复核样本"} description={highRiskReviewCount > 0 ? `其中 ${oodReviewCount} 条 OOD 候选，人工确认后才进入反馈池。` : "新的 abstain / reject_ood 推理会自动进入复核队列。"} action="打开复核" to="/review" tone={highRiskReviewCount > 0 ? "risk" : "default"} />
            <TaskItem icon={failedRuns.length > 0 ? "AlertTriangle" : "CircleGauge"} title={failedRuns.length > 0 ? `定位 ${failedRuns.length} 条失败训练` : `${candidateRuns.length} 个候选模型可评审`} description={failedRuns.length > 0 ? "训练详情页现在会展示 error、jobId 和缺失产物。" : "候选模型仍需人工评审和发布门禁后才能生产使用。"} action="打开训练" to="/training" tone={failedRuns.length > 0 ? "risk" : "warn"} />
            <TaskItem icon="DatabaseZap" title="反馈池等待数据策展" description="复核结论不会自动写回训练集，需要冻结成新 dataset version。" action="看反馈池" to="/feedback" tone="info" />
          </div>
        </Panel>
        <Panel
          title="最近低置信样本"
          caption="来自真实 Review API；没有样本时不会跳转到 mock 详情。"
          action={
            <Link className="ghost-button" to="/review">
              <Icon name="ListFilter" size={16} />
              全部
            </Link>
          }
        >
          {pendingReviewItems.length > 0 ? (
            <div className="grid">{pendingReviewItems.slice(0, 3).map((item) => <ApiReviewCard item={item} key={item.id} />)}</div>
          ) : (
            <div className="timeline-item">
              <div className="timeline-icon"><Icon name="CheckCircle2" size={18} /></div>
              <div><strong>{reviewLoading ? "正在读取复核队列" : "暂无真实待复核样本"}</strong><div className="row-meta">上传推理产生 abstain / reject_ood 后会进入这里。</div></div>
              <Link className="ghost-button" to="/review">打开队列</Link>
            </div>
          )}
        </Panel>
      </div>
      <div className="grid two section-gap">
        <Panel title="数据集状态" caption="当前系统支持多数据集持续接入。">
          <DatasetTable items={datasetItems} />
        </Panel>
        <Panel title="模型发布门禁" caption="上线前必须通过的检查。">
          <div className="grid">
            <GateRow title="候选模型" description={`${candidateRuns.length} 个训练完成的 candidate model`} result={candidateRuns.length > 0 ? "pass" : "pending"} />
            <GateRow title="反馈池检查" description="需要消费 OOD / 坏图 / 类别争议后才能发布" result="pending" />
            <GateRow title="人工抽检" description={`${pendingReviewItems.length} 条待复核会影响发布判断`} result={pendingReviewItems.length === 0 ? "pass" : "pending"} />
            <GateRow title="回滚配置" description="Model Registry API 尚未接入 production/rollback 状态" result="pending" />
          </div>
        </Panel>
      </div>
    </>
  );
}

export function DatasetsPage({ showToast }) {
  const { datasets: datasetItems, source, loading, refresh } = useDatasets();
  const folderInputRef = useRef(null);
  const [showImport, setShowImport] = useState(false);
  const [importForm, setImportForm] = useState({
    datasetId: "cifar10-mini",
    datasetVersionId: "dataset@cifar10-mini-001",
  });
  const [folderSelection, setFolderSelection] = useState({
    valid: false,
    error: "请选择一个本地 ImageFolder 文件夹。",
    files: [],
    rootName: "",
  });
  const [importState, setImportState] = useState({ status: "idle", result: null, error: null });
  const sourceLabel = source === "api" ? "Control-plane API" : "Control-plane API 暂不可用";
  const canImport =
    importState.status !== "running" &&
    folderSelection.valid &&
    folderSelection.files.length > 0 &&
    importForm.datasetId.trim() &&
    importForm.datasetVersionId.trim();

  function updateImportField(field, value) {
    setImportForm((current) => ({ ...current, [field]: value }));
  }

  function handleFolderSelection(files) {
    const summary = analyzeImageFolderFiles(files);
    setFolderSelection(summary);
    setImportState({ status: "idle", result: null, error: null });
    if (!summary.valid) return;
    const datasetId = datasetIdFromFolderName(summary.rootName);
    setImportForm({
      datasetId,
      datasetVersionId: `dataset@${datasetId}-001`,
    });
  }

  async function handleImportDataset() {
    if (!canImport) return;
    setImportState({ status: "running", result: null, error: null });
    try {
      const result = await uploadImagefolder({
        files: folderSelection.files,
        dataset_id: importForm.datasetId.trim(),
        dataset_version_id: importForm.datasetVersionId.trim(),
      });
      setImportState({ status: "succeeded", result, error: null });
      refresh();
      showToast("数据集导入完成，列表已刷新");
    } catch (error) {
      setImportState({ status: "failed", result: null, error });
      showToast("数据集导入失败");
    }
  }

  return (
    <>
      <PageHero
        title="每个分类任务都是一个独立资产。"
        description="数据集不仅是图片目录，还包括类别体系、样本质量、特征索引、OOD 压力集、阈值策略和模型版本绑定。"
        actions={
          <button className="primary-button" onClick={() => setShowImport((value) => !value)}>
            <Icon name="FolderInput" size={16} />
            导入数据集
          </button>
        }
      />
      {showImport && (
        <Panel
          title="导入本地 ImageFolder"
          caption="选择本地分类图片文件夹，系统会校验结构，通过后复制到项目数据目录并自动导入。"
          action={<StatusChip tone={importState.status === "failed" ? "risk" : importState.status === "succeeded" ? "default" : "info"}>{importState.status}</StatusChip>}
        >
          <div className="field-grid">
            <div className="field full-span">
              <label>本地文件夹</label>
              <div className="file-picker folder-picker">
                <input
                  ref={folderInputRef}
                  type="file"
                  multiple
                  webkitdirectory=""
                  directory=""
                  onChange={(event) => handleFolderSelection(event.target.files)}
                />
                <Icon name="FolderInput" size={18} />
                <span>{folderSelection.valid ? folderSelection.rootName : "选择 ImageFolder 文件夹"}</span>
                <button
                  className="ghost-button"
                  type="button"
                  onClick={(event) => {
                    event.preventDefault();
                    folderInputRef.current?.click();
                  }}
                >
                  选择
                </button>
              </div>
              <div className={`row-meta ${folderSelection.valid ? "" : "error-text"}`}>
                {folderSelection.valid
                  ? `${folderSelection.format} · ${folderSelection.imageCount} images · ${folderSelection.classes.length} classes`
                  : folderSelection.error}
              </div>
            </div>
            <div className="field">
              <label>dataset_id</label>
              <input value={importForm.datasetId} onChange={(event) => updateImportField("datasetId", event.target.value)} placeholder="cifar10-mini" />
            </div>
            <div className="field">
              <label>dataset_version_id</label>
              <input value={importForm.datasetVersionId} onChange={(event) => updateImportField("datasetVersionId", event.target.value)} placeholder="dataset@cifar10-mini-001" />
            </div>
            <div className="field">
              <label>执行</label>
              <button className="primary-button" onClick={handleImportDataset} disabled={!canImport}>
                <Icon name={importState.status === "running" ? "LoaderCircle" : "FolderInput"} size={16} />
                {importState.status === "running" ? "上传导入中" : "上传并导入"}
              </button>
            </div>
          </div>
          {folderSelection.valid && (
            <div className="chips section-gap-small">
              <StatusChip tone="default">格式合法</StatusChip>
              <StatusChip tone="info">{folderSelection.format}</StatusChip>
              {folderSelection.splits?.length > 0 && <StatusChip tone="info">{folderSelection.splits.join(" / ")}</StatusChip>}
              <StatusChip tone="info">{folderSelection.classes.slice(0, 4).join(", ")}{folderSelection.classes.length > 4 ? " ..." : ""}</StatusChip>
            </div>
          )}
          {importState.result?.version && (
            <div className="chips section-gap-small">
              <StatusChip tone={importState.result.version.readiness?.ready ? "default" : "warn"}>
                {importState.result.version.readiness?.ready ? "ready" : "not ready"}
              </StatusChip>
              <StatusChip tone="info">{importState.result.version.sample_count ?? 0} samples</StatusChip>
              <StatusChip tone="info">{importState.result.version.class_count ?? 0} classes</StatusChip>
              {importState.result.upload?.stored_path && <StatusChip tone="info">{importState.result.upload.stored_path}</StatusChip>}
            </div>
          )}
          {importState.error && <div className="row-meta section-gap-small">{importState.error.message}</div>}
        </Panel>
      )}
      <Panel
        title="数据集列表"
        caption={`${loading ? "正在连接 Control-plane API" : sourceLabel} · 点击行进入数据集详情。`}
        action={
          <div className="tabs">
            <button className="tab-button active">全部</button>
            <button className="tab-button">可推理</button>
            <button className="tab-button">待训练</button>
          </div>
        }
      >
        <DatasetTable items={datasetItems} />
      </Panel>
    </>
  );
}

export function DatasetDetailPage({ showToast }) {
  const { datasetId = "bird" } = useParams();
  const [searchParams, setSearchParams] = useSearchParams();
  const { dataset, source, loading } = useDataset(datasetId);
  const tab = searchParams.get("tab") ?? "overview";
  const tabs = [
    ["overview", "概览"],
    ["classes", "类别治理"],
    ["samples", "样本"],
    ["features", "特征库"],
    ["ood", "OOD/弃权"],
  ];

  if (!dataset) {
    return (
      <>
        <PageHero
          title={loading ? "正在加载数据集" : "数据集不存在"}
          description={loading ? "正在连接 Control-plane API。" : `${datasetId} 没有在 Control-plane API 中找到；本页不再回退到本地 mock 数据。`}
          actions={<Link className="ghost-button" to="/datasets"><Icon name="ArrowLeft" size={16} />返回数据集</Link>}
        />
        <Panel title="下一步" caption={source === "api" ? "API 已连接，但没有找到该 dataset_id。" : "Control-plane API 暂不可用。"}>
          <div className="timeline">
            <GateRow title="导入数据集" description="使用 ImageFolder 导入真实 dataset version。" result="pending" />
            <GateRow title="训练分类头" description="数据 ready 后再创建训练任务。" result="pending" />
            <GateRow title="运行推理" description="选择真实 dataset/model version。" result="pending" />
          </div>
        </Panel>
      </>
    );
  }

  return (
    <>
      <PageHero
        title={dataset.name}
        description={`${dataset.description} · ${loading ? "正在连接 Control-plane API" : source === "api" ? "来自 Control-plane API" : "Control-plane API 暂不可用"}`}
        actions={
          <>
            <Link className="ghost-button" to="/datasets">
              <Icon name="ArrowLeft" size={16} />
              返回
            </Link>
            <Link className="secondary-button" to="/training">
              <Icon name="FlaskConical" size={16} />
              训练
            </Link>
            <Link className="primary-button" to="/inference">
              <Icon name="ImageUp" size={16} />
              推理测试
            </Link>
          </>
        }
      />
      <Panel>
        <div className="tabs">
          {tabs.map(([id, label]) => (
            <button className={`tab-button ${tab === id ? "active" : ""}`} key={id} onClick={() => setSearchParams(id === "overview" ? {} : { tab: id })}>
              {label}
            </button>
          ))}
        </div>
      </Panel>
      <DatasetTab dataset={dataset} tab={tab} showToast={showToast} />
    </>
  );
}

function DatasetTab({ dataset, tab, showToast }) {
  const previewLimit = tab === "samples" ? 8 : 3;
  const { samples: previewSamples, loading: previewLoading, error: previewError } = useDatasetSamplePreviews(dataset.datasetVersionId, previewLimit);

  if (tab === "classes") {
    return (
      <div className="grid two section-gap">
        <Panel title="类别治理" caption="细粒度任务里类别边界比模型更重要。">
          <div className="timeline">
            <ClassRow title="黑喉石鵖" description="易混：普通石鵖 · 样本 84 · 准确率 86%" label="需补样" tone="warn" />
            <ClassRow title="普通石鵖" description="易混：黑喉石鵖 · 样本 102 · 准确率 88%" label="正常" />
            <ClassRow title="赭红尾鸲" description="长尾类 · 样本 23 · 准确率 61%" label="高风险" tone="risk" />
            <ClassRow title="未确认类别" description="12 张样本存在标签争议" label="争议池" tone="info" />
          </div>
        </Panel>
        <Panel title="类别定义" caption="给人工复核使用的判别说明。">
          <div className="field">
            <label>判别规则</label>
            <textarea defaultValue="关注喉部色块、胸侧颜色、尾羽形状；不要把背景或拍摄地点作为类别依据。" />
          </div>
          <div className="toolbar section-gap-small">
            <button className="primary-button" disabled>
              <Icon name="Save" size={16} />
              类别保存待接入
            </button>
            <button className="ghost-button" disabled>
              <Icon name="Wand2" size={16} />
              差异点辅助待接入
            </button>
          </div>
        </Panel>
      </div>
    );
  }

  if (tab === "samples") {
    return (
      <Panel
        className="section-gap"
        title="样本浏览"
        caption="支持按类别、质量、预测错误、近邻距离筛选。"
        action={
          <div className="segmented">
            <button className="seg-button active">全部</button>
            <button className="seg-button">错标疑似</button>
            <button className="seg-button">长尾</button>
            <button className="seg-button">坏图</button>
          </div>
        }
      >
        <DatasetSamplePreviewGrid samples={previewSamples} loading={previewLoading} error={previewError} />
      </Panel>
    );
  }

  if (tab === "features") {
    return (
      <div className="grid two section-gap">
        <Panel title="特征索引" caption="DINOv3 embedding、类别原型和近邻索引。">
          <MetricCard title="特征向量" value={dataset.images.toLocaleString()} caption={dataset.featureArtifactId ?? "feature artifact 待生成"} fill="#0891b2" percent={dataset.featureArtifactId ? 100 : 0} icon="DatabaseZap" />
          <div className="code-panel section-gap-small">feature_artifact: {dataset.featureArtifactId ?? "n/a"}<br />dataset_version: {dataset.datasetVersionId ?? "n/a"}<br />index: feature index API 待接入<br />backbone: dinov3_vitl<br />prototype_strategy: class_centroid + hard_negative_bank</div>
        </Panel>
        <Panel title="最近邻检查" caption="当前展示真实样本预览；近邻证据会在特征索引 API 完成后接入。">
          <DatasetSamplePreviewGrid samples={previewSamples} loading={previewLoading} error={previewError} compact />
        </Panel>
      </div>
    );
  }

  if (tab === "ood") {
    return (
      <div className="grid two section-gap">
        <Panel title="弃权策略" caption="把“不知道”作为正式输出。">
          <div className="field-grid">
            <div className="field"><label>最低置信度</label><input defaultValue="0.78" /></div>
            <div className="field"><label>Top-1 / Top-2 Margin</label><input defaultValue="0.12" /></div>
            <div className="field"><label>Embedding Distance</label><input defaultValue="0.42" /></div>
            <div className="field"><label>OOD 压力集</label><select defaultValue="stress@002"><option>stress@002</option></select></div>
          </div>
          <button className="primary-button section-gap-small" disabled>
            <Icon name="Save" size={16} />
            策略保存待接入
          </button>
        </Panel>
        <Panel title="Coverage / Risk" caption="阈值越严格，复核越多，但错误越少。">
          <CurveRow label={`coverage ${dataset.coverage ?? 0}%`} value="risk 待接入" percent={dataset.coverage ?? 0} fill="#0f766e" />
          <CurveRow label="更严格阈值" value="需要校准报告" percent={0} fill="#315fbd" />
          <CurveRow label="更宽松阈值" value="需要验证集评估" percent={0} fill="#a15c07" />
        </Panel>
      </div>
    );
  }

  return (
    <>
      <div className="grid metrics section-gap">
        <MetricCard title="样本质量" value={`${dataset.quality}%`} caption="坏图、错标、重复图综合" fill="#0f766e" percent={dataset.quality} icon="BadgeCheck" />
        <MetricCard title="自动覆盖率" value={`${dataset.coverage}%`} caption="当前阈值策略" fill="#315fbd" percent={dataset.coverage || 0} icon="Gauge" />
        <MetricCard title="OOD 拦截" value={dataset.oodRecall ? `${dataset.oodRecall}%` : "--"} caption="压力集表现" fill="#26804f" percent={dataset.oodRecall || 0} icon="ShieldAlert" />
        <MetricCard title="类别数量" value={dataset.classes} caption="可训练类别" fill="#6750a4" percent={Math.min(100, dataset.classes / 2)} icon="Tags" />
      </div>
      <div className="grid two section-gap">
        <Panel title="训练准备" caption="数据集能否进入训练流水线。">
          <div className="timeline">
            <GateRow title="类别体系" description="易混类别已标注，长尾类仍需补样" result="pending" />
            <GateRow title="特征缓存" description={dataset.featureArtifactId ? `${dataset.featureArtifactId} 已完成` : "等待特征产物"} result={dataset.featureArtifactId ? "pass" : "pending"} />
            <GateRow title="验证集" description="val/test/stress 已划分" />
          </div>
        </Panel>
        <Panel title="样本预览" caption="来自当前 dataset version 的真实图片。">
          <DatasetSamplePreviewGrid samples={previewSamples} loading={previewLoading} error={previewError} compact />
        </Panel>
      </div>
    </>
  );
}

function ClassRow({ title, description, label, tone = "default" }) {
  return (
    <div className="timeline-item">
      <div className="timeline-icon"><Icon name="Tags" size={18} /></div>
      <div><strong>{title}</strong><div className="row-meta">{description}</div></div>
      <StatusChip tone={tone}>{label}</StatusChip>
    </div>
  );
}

function runTimeValue(run) {
  const value = run.updatedAt ?? run.finishedAt ?? run.startedAt ?? run.createdAt ?? "";
  const timestamp = Date.parse(value);
  return Number.isFinite(timestamp) ? timestamp : 0;
}

function filterTrainingRuns(runs, statusFilter, sortMode) {
  const filtered = runs.filter((run) => {
    if (statusFilter === "all") return true;
    if (statusFilter === "failed") return run.status === "failed";
    if (statusFilter === "active") return ["queued", "running"].includes(run.status);
    if (statusFilter === "succeeded") return run.status === "succeeded";
    return true;
  });
  return [...filtered].sort((a, b) => {
    if (sortMode === "failed_first") {
      const failedDelta = Number(b.status === "failed") - Number(a.status === "failed");
      if (failedDelta !== 0) return failedDelta;
    }
    if (sortMode === "oldest") return runTimeValue(a) - runTimeValue(b);
    return runTimeValue(b) - runTimeValue(a);
  });
}

const TRAINING_OUTPUTS = [
  ["featureArtifactId", "特征缓存", "用于复用 frozen backbone 的 feature cache"],
  ["modelArtifactId", "模型权重", "分类头训练产物"],
  ["reportArtifactId", "训练报告", "评估指标和发布判断依据"],
  ["calibrationArtifactId", "校准参数", "置信度校准产物"],
  ["thresholdStrategyArtifactId", "阈值策略", "覆盖率 / selective risk 扫描产物"],
];

function trainingRunOutputItems(run) {
  return TRAINING_OUTPUTS.map(([field, label, description]) => ({
    field,
    label,
    description,
    value: run?.[field] ?? null,
  }));
}

function missingTrainingRunOutputs(run) {
  return trainingRunOutputItems(run).filter((item) => !item.value);
}

function trainingRunNextActions(run, missingOutputs) {
  if (run.status === "cancelled") {
    return ["训练已取消；如需继续，请确认输入数据和参数后重新创建训练运行。"];
  }

  if (run.status === "failed" || run.error) {
    const actions = ["查看 Training worker 日志，优先用 jobId 对齐后端任务。"];
    if (!run.jobId) actions.push("确认 Training API 是否返回 job_id，避免前端无法跳转到任务日志。");
    if (!run.datasetVersionId) actions.push("补齐 datasetVersionId；没有数据快照无法判断训练输入。");
    if (missingOutputs.some((item) => item.field === "featureArtifactId")) actions.push("先确认特征抽取是否完成，必要时重跑 feature cache。");
    if (missingOutputs.some((item) => item.field === "modelArtifactId")) actions.push("定位分类头训练阶段失败原因，再决定是否调整 head_config 后重跑。");
    if (missingOutputs.some((item) => ["calibrationArtifactId", "thresholdStrategyArtifactId"].includes(item.field))) actions.push("若模型权重已生成，补跑校准和阈值扫描以恢复发布判断。");
    return actions;
  }

  if (["queued", "running"].includes(run.status)) {
    return ["等待 worker 写入下一阶段产物；缺失项在运行中只代表尚未生成。"];
  }

  if (missingOutputs.length > 0) {
    return ["训练已结束但产物未齐，检查 API 响应和 artifact store 写入是否一致。"];
  }

  return ["产物链路齐全，可进入候选模型评审或后续推理验证。"];
}

function TrainingRunDiagnostics({ run }) {
  const llm = useLLMAssistance();
  const statusInfo = trainingStatus(run);
  const outputItems = trainingRunOutputItems(run);
  const missingOutputs = missingTrainingRunOutputs(run);
  const hasFailureSignal = run.status === "failed" || Boolean(run.error);
  const hasStoppedSignal = hasFailureSignal || run.status === "cancelled";
  const actions = trainingRunNextActions(run, missingOutputs);
  const summary = hasStoppedSignal
    ? run.status === "cancelled"
      ? "Training API 标记该 run 为 cancelled，产物缺失不代表训练失败。"
      : run.error || "Training API 标记该 run 为 failed，但没有返回 error 文本。"
    : ["queued", "running"].includes(run.status)
      ? "训练仍在进行，产物缺失通常表示该阶段尚未完成。"
      : missingOutputs.length > 0
        ? "训练未报告失败，但产物链路还不完整。"
        : "当前没有失败信号，lineage 和主要产物已可追踪。";

  async function handleGenerateDiagnosis() {
    await llm.generate({
      task: "training_diagnosis",
      context: {
        run_id: run.id,
        job_id: run.jobId,
        status: run.status,
        dataset_version_id: run.datasetVersionId,
        model_version_id: run.modelVersionId,
        error: run.error,
        missing_outputs: missingOutputs.map((item) => item.field),
        metrics: run.metrics ?? {},
      },
    });
  }

  return (
    <Panel
      title={hasFailureSignal ? "失败诊断" : "Lineage 与产物状态"}
      caption={hasFailureSignal ? "从 error、job 和缺失产物定位失败阶段。" : "轻量展示输入、候选输出和 artifact 完整度。"}
      action={<StatusChip tone={hasFailureSignal ? "risk" : statusInfo.tone}>{statusInfo.label}</StatusChip>}
    >
      <div className={`diagnostic-callout ${hasFailureSignal ? "risk" : ""}`}>
        <div className="timeline-icon">
          <Icon name={hasFailureSignal ? "AlertTriangle" : "GitCompare"} size={18} />
        </div>
        <div>
          <strong>{hasFailureSignal ? "训练失败信号" : run.status === "cancelled" ? "训练已取消" : "当前链路状态"}</strong>
          <div className="row-meta">{summary}</div>
        </div>
      </div>
      <div className="lineage-grid section-gap-small">
        <div>
          <span>jobId</span>
          <strong>{run.jobId ?? "未返回"}</strong>
        </div>
        <div>
          <span>datasetVersionId</span>
          <strong>{run.datasetVersionId ?? "未绑定"}</strong>
        </div>
        <div>
          <span>modelVersionId</span>
          <strong>{run.modelVersionId ?? "未生成"}</strong>
        </div>
        <div>
          <span>error</span>
          <strong>{run.error ?? "无"}</strong>
        </div>
      </div>
      <div className="artifact-list section-gap-small">
        {outputItems.map((item) => (
          <div className="artifact-row" key={item.field}>
            <div>
              <strong>{item.label}</strong>
              <div className="row-meta">{item.value ?? item.description}</div>
            </div>
            <StatusChip tone={item.value ? "default" : hasFailureSignal ? "risk" : "warn"}>
              {item.value ? "已生成" : "缺失"}
            </StatusChip>
          </div>
        ))}
      </div>
      <div className="next-actions section-gap-small">
        <strong>建议下一步</strong>
        <ul>
          {actions.map((action) => (
            <li key={action}>{action}</li>
          ))}
        </ul>
      </div>
      <LLMAssistanceBox
        title="LLM 排障建议"
        caption="只分析训练错误和缺失产物，不会重跑任务或修改模型状态。"
        assistance={llm.assistance}
        status={llm.status}
        error={llm.error}
        onGenerate={handleGenerateDiagnosis}
      />
    </Panel>
  );
}

export function TrainingPage({ showToast }) {
  const [trainingSearchParams, setTrainingSearchParams] = useSearchParams();
  const { trainingRuns: runItems, source, loading, refresh } = useTrainingRuns();
  const { datasets: datasetOptions, source: datasetSource, loading: datasetsLoading, refresh: refreshDatasets } = useDatasets();
  const [showCreate, setShowCreate] = useState(false);
  const [queueCollapsed, setQueueCollapsed] = useState(false);
  const [queueStatusFilter, setQueueStatusFilter] = useState("all");
  const [queueSortMode, setQueueSortMode] = useState("recent");
  const [trainingForm, setTrainingForm] = useState({
    datasetVersionId: "",
    extractor: "color_stats",
    ridgeLambda: "0.01",
  });
  const [createState, setCreateState] = useState({ status: "idle", run: null, error: null });
  const sourceLabel = loading ? "正在连接 Training API" : source === "api" ? "Training API" : "Training API 暂不可用";
  const trainingDatasetOptions = datasetOptions.filter((dataset) => dataset.datasetVersionId);
  const datasetVersionOptions = trainingDatasetOptions.map((dataset) => dataset.datasetVersionId).filter(Boolean);
  const filteredRuns = filterTrainingRuns(runItems, queueStatusFilter, queueSortMode);
  const failedRunCount = runItems.filter((run) => run.status === "failed").length;
  const activeRunCount = runItems.filter((run) => ["queued", "running"].includes(run.status)).length;
  const canUseDatasetForTraining = datasetSource === "api" && datasetVersionOptions.length > 0;
  const canCreate =
    canUseDatasetForTraining &&
    createState.status !== "running" &&
    trainingForm.datasetVersionId.trim() &&
    Number(trainingForm.ridgeLambda) > 0;
  const createBlockReason = canUseDatasetForTraining
    ? ""
    : datasetSource === "api"
      ? "当前没有真实 dataset version 可用于训练。"
      : "Control-plane API 暂不可用，不能提交真实训练任务。";

  useEffect(() => {
    if (datasetSource !== "api" || datasetVersionOptions.length === 0) return;
    setTrainingForm((current) => {
      if (datasetVersionOptions.includes(current.datasetVersionId)) return current;
      return { ...current, datasetVersionId: datasetVersionOptions[0] };
    });
  }, [datasetSource, datasetVersionOptions.join("|")]);

  useEffect(() => {
    if (trainingSearchParams.get("create") === "1") setShowCreate(true);
  }, [trainingSearchParams]);

  function updateTrainingField(field, value) {
    setTrainingForm((current) => ({ ...current, [field]: value }));
  }

  async function handleCreateTrainingRun() {
    if (!canCreate) return;
    setCreateState({ status: "running", run: null, error: null });
    try {
      const run = await createTrainingRun({
        dataset_version_id: trainingForm.datasetVersionId.trim(),
        extractor: trainingForm.extractor,
        head_config: {
          head_type: "ridge_linear",
          ridge_lambda: Number(trainingForm.ridgeLambda),
        },
      });
      setCreateState({ status: "succeeded", run, error: null });
      refresh();
      showToast(`训练已创建：${run.id}`);
    } catch (error) {
      setCreateState({ status: "failed", run: null, error });
      showToast("训练创建失败");
    }
  }

  return (
    <>
      <PageHero title="冻结视觉基座，快速训练分类头。" description="训练页聚焦数据版本、backbone、分类头、阈值校准和报告产物，避免把实验结果变成不可追踪的文件。" actions={<button className="primary-button" onClick={() => {
        const nextVisible = !showCreate;
        setShowCreate(nextVisible);
        const next = new URLSearchParams(trainingSearchParams);
        if (nextVisible) next.set("create", "1");
        else next.delete("create");
        setTrainingSearchParams(next);
      }}><Icon name="Plus" size={16} />新建训练</button>} />
      {showCreate && (
        <Panel
          title="创建训练运行"
          caption="只允许 ready 的 dataset version 进入训练，训练任务会由 ml-worker 执行。"
          action={<StatusChip tone={createState.status === "failed" ? "risk" : createState.status === "succeeded" ? "default" : "info"}>{createState.status}</StatusChip>}
        >
          <div className="field-grid">
            <div className="field">
              <label>数据集版本</label>
              <select value={trainingForm.datasetVersionId} onChange={(event) => updateTrainingField("datasetVersionId", event.target.value)} disabled={!canUseDatasetForTraining}>
                {trainingDatasetOptions.map((dataset) => (
                  <option value={dataset.datasetVersionId} key={dataset.datasetVersionId}>
                    {dataset.name} · {dataset.datasetVersionId} · {dataset.images} samples · {dataset.status}
                  </option>
                ))}
              </select>
            </div>
            <div className="field">
              <label>extractor</label>
              <select value={trainingForm.extractor} onChange={(event) => updateTrainingField("extractor", event.target.value)}>
                <option value="color_stats">color_stats</option>
                <option value="dinov3_vitl">dinov3_vitl</option>
              </select>
            </div>
            <div className="field">
              <label>ridge_lambda</label>
              <input type="number" min="0.000001" step="0.001" value={trainingForm.ridgeLambda} onChange={(event) => updateTrainingField("ridgeLambda", event.target.value)} />
            </div>
            <div className="field">
              <label>执行</label>
              <button className="primary-button" onClick={handleCreateTrainingRun} disabled={!canCreate}>
                <Icon name={createState.status === "running" ? "LoaderCircle" : "Play"} size={16} />
                {createState.status === "running" ? "创建中" : "创建训练"}
              </button>
            </div>
          </div>
          <div className="toolbar section-gap-small">
            <button className="ghost-button" onClick={refresh} disabled={loading}>
              <Icon name="RefreshCw" size={16} />
              刷新训练队列
            </button>
            <button className="ghost-button" onClick={refreshDatasets} disabled={datasetsLoading}>
              <Icon name="RefreshCw" size={16} />
              刷新数据集
            </button>
            <StatusChip tone={datasetSource === "api" ? "default" : "warn"}>
              {datasetSource === "api" ? `${trainingDatasetOptions.length} 个可选数据集版本` : "dataset api unavailable"}
            </StatusChip>
          </div>
          {createBlockReason && (
            <div className="route-box section-gap-small">
              <div><strong>创建训练已暂停</strong><div className="row-meta">{createBlockReason}</div></div>
              <StatusChip tone="warn">api required</StatusChip>
            </div>
          )}
          {createState.run && (
            <div className="chips section-gap-small">
              <StatusChip tone="default">{createState.run.id}</StatusChip>
              <Link className="ghost-button" to={`/training/${createState.run.id}`}>
                <Icon name="ExternalLink" size={16} />
                打开详情
              </Link>
            </div>
          )}
          {createState.error && <div className="row-meta section-gap-small">{createState.error.message}</div>}
        </Panel>
      )}
      <div className="grid two">
        <Panel
          title="训练队列"
          caption={`${sourceLabel} · ${filteredRuns.length}/${runItems.length} 条显示 · 失败 ${failedRunCount} · 活跃 ${activeRunCount}`}
          action={<button className="ghost-button" onClick={() => setQueueCollapsed((value) => !value)}><Icon name={queueCollapsed ? "ChevronRight" : "ListFilter"} size={16} />{queueCollapsed ? "展开" : "折叠"}</button>}
        >
          <div className="review-filter-bar">
            <div className="tabs">
              {[
                ["all", "全部"],
                ["failed", "失败"],
                ["active", "运行中"],
                ["succeeded", "完成"],
              ].map(([value, label]) => (
                <button className={`tab-button ${queueStatusFilter === value ? "active" : ""}`} key={value} onClick={() => setQueueStatusFilter(value)}>
                  {label}
                </button>
              ))}
            </div>
            <label className="filter-select">
              <span>排序</span>
              <select value={queueSortMode} onChange={(event) => setQueueSortMode(event.target.value)}>
                <option value="recent">最近更新</option>
                <option value="oldest">最早创建</option>
                <option value="failed_first">失败优先</option>
              </select>
            </label>
          </div>
          {queueCollapsed ? (
            <div className="queue-collapsed">
              <StatusChip tone={failedRunCount ? "risk" : "default"}>{failedRunCount} failed</StatusChip>
              <StatusChip tone={activeRunCount ? "warn" : "neutral"}>{activeRunCount} active</StatusChip>
              <StatusChip tone="info">{runItems.length} total</StatusChip>
            </div>
          ) : (
            <div className="timeline">
              {filteredRuns.length > 0 ? (
                filteredRuns.map((run) => <RunRow run={run} key={run.id} />)
            ) : (
              <div className="timeline-item">
                <div className="timeline-icon"><Icon name="Inbox" size={18} /></div>
                <div><strong>当前筛选下没有训练运行</strong><div className="row-meta">可以切回全部，或新建一条训练任务。</div></div>
                <StatusChip tone="info">empty</StatusChip>
              </div>
              )}
            </div>
          )}
        </Panel>
        <Panel title="训练配置模板" caption="MVP 先支持 frozen backbone + 分类头。">
          <div className="code-panel">backbone: dinov3_vitl<br />feature_cache: true<br />head: linear<br />calibration: temperature_scaling<br />abstention: top1_margin + embedding_distance<br />report: accuracy, macro_f1, coverage_risk</div>
        </Panel>
      </div>
    </>
  );
}

export function TrainingDetailPage({ showToast }) {
  const { runId = "run-042" } = useParams();
  const { trainingRun: run, source, loading } = useTrainingRun(runId);
  if (loading && !run) {
    return (
      <>
        <PageHero title="正在加载训练运行" description="正在连接 Training API。" actions={<Link className="ghost-button" to="/training"><Icon name="ArrowLeft" size={16} />返回训练队列</Link>} />
        <Panel title="运行详情" caption="等待 API 返回。"><div className="empty-state"><Icon name="LoaderCircle" size={24} /><strong>加载中</strong><span>不会显示本地 mock 训练详情。</span></div></Panel>
      </>
    );
  }
  if (!loading && !run) {
    return (
      <>
        <PageHero
          title="训练运行不存在"
          description={`${runId} 没有在 Training API 中找到。请从训练队列打开真实运行，或先创建一条训练。`}
          actions={<Link className="ghost-button" to="/training"><Icon name="ArrowLeft" size={16} />返回训练队列</Link>}
        />
        <Panel title="未找到运行" caption="旧的 mock run id 不会再伪装成真实训练结果。">
          <div className="timeline-item">
            <div className="timeline-icon"><Icon name="AlertTriangle" size={18} /></div>
            <div><strong>{runId}</strong><div className="row-meta">Training API 返回 404。</div></div>
            <StatusChip tone="risk">not found</StatusChip>
          </div>
        </Panel>
      </>
    );
  }

  const metrics = run.metrics ?? {};
  const accuracy = Number.isFinite(Number(metrics.accuracy)) ? Math.round(Number(metrics.accuracy) * 1000) / 10 : null;
  const macroF1 = Number.isFinite(Number(metrics.macro_f1)) ? Math.round(Number(metrics.macro_f1) * 1000) / 10 : null;
  const coverage = Number.isFinite(Number(metrics.expected_coverage)) ? Math.round(Number(metrics.expected_coverage) * 100) : null;
  const reviewCost = Number.isFinite(Number(metrics.expected_selective_risk)) ? `${(Number(metrics.expected_selective_risk) * 100).toFixed(2)}% risk` : "待生成";
  const sourceLabel = loading ? "正在连接 Training API" : source === "api" ? "Training API" : "Training API 暂不可用";
  return (
    <>
      <PageHero title={run.name} description={`${run.datasetName} · ${sourceLabel} · 训练分类头、生成校准报告、准备候选模型版本。`} actions={<><Link className="ghost-button" to="/training"><Icon name="ArrowLeft" size={16} />返回</Link><button className="primary-button" disabled><Icon name="ExternalLink" size={16} />产物 API 待接入</button></>} />
      <div className="grid metrics">
        <MetricCard title="进度" value={`${run.progress}%`} caption={trainingStatus(run).label} fill="#a15c07" percent={run.progress} icon="LoaderCircle" />
        <MetricCard title="Val Acc" value={accuracy === null ? "待生成" : `${accuracy}%`} caption={run.modelVersionId ?? "候选模型待生成"} fill="#0f766e" percent={accuracy ?? 0} icon="Target" />
        <MetricCard title="Macro F1" value={macroF1 === null ? "待生成" : `${macroF1}%`} caption={run.reportArtifactId ?? "报告待生成"} fill="#315fbd" percent={macroF1 ?? 0} icon="BarChart3" />
        <MetricCard title="复核压力" value={reviewCost} caption="selective risk / review cost" fill="#b4233c" percent={coverage ?? 0} icon="UserCheck" />
      </div>
      <div className="grid two section-gap">
        <Panel title="运行步骤" caption="每一步都应有产物和失败恢复点。">
          <div className="timeline"><GateRow title="数据快照" description={run.datasetVersionId ? `${run.datasetVersionId} locked` : "等待绑定数据快照"} result={run.datasetVersionId ? "pass" : "pending"} /><GateRow title="特征缓存" description={run.featureArtifactId ?? "等待特征抽取"} result={run.featureArtifactId ? "pass" : "pending"} /><GateRow title="分类头训练" description={run.modelArtifactId ?? trainingStatus(run).label} result={run.modelArtifactId ? "pass" : "pending"} /><GateRow title="阈值扫描" description={run.thresholdStrategyArtifactId ?? "等待训练完成"} result={run.thresholdStrategyArtifactId ? "pass" : "pending"} /></div>
        </Panel>
        <TrainingRunDiagnostics run={run} />
      </div>
      <div className="grid two section-gap">
        <Panel title="候选发布判断" caption="不要只看 accuracy。">
          <CurveRow label="accuracy" value={accuracy === null ? "待生成" : `${accuracy}%`} percent={accuracy ?? 0} fill="#0f766e" />
          <CurveRow label="coverage" value={coverage === null ? "待生成" : `${coverage}%`} percent={coverage ?? 0} fill="#315fbd" />
          <CurveRow label="review cost" value={reviewCost} percent={coverage ?? 0} fill="#a15c07" />
        </Panel>
        <Panel title="产物元数据" caption="原始字段便于和 API / artifact store 对账。">
          <div className="code-panel">run: {run.id}<br />job: {run.jobId ?? "n/a"}<br />dataset: {run.datasetVersionId ?? "n/a"}<br />model_version: {run.modelVersionId ?? "n/a"}<br />feature: {run.featureArtifactId ?? "n/a"}<br />model_artifact: {run.modelArtifactId ?? "n/a"}<br />report: {run.reportArtifactId ?? "n/a"}<br />calibration: {run.calibrationArtifactId ?? "n/a"}<br />threshold_strategy: {run.thresholdStrategyArtifactId ?? "n/a"}<br />error: {run.error ?? "n/a"}</div>
        </Panel>
      </div>
    </>
  );
}

export function InferencePage({ showToast }) {
  const { datasets: apiDatasets, source: datasetSource } = useDatasets();
  const { trainingRuns: inferenceTrainingRuns, source: trainingSource } = useTrainingRuns();
  const llm = useLLMAssistance();
  const datasetOptions = apiDatasets;
  const [form, setForm] = useState({
    datasetVersionId: datasetOptions[0]?.datasetVersionId ?? "",
    modelVersionId: "",
    imageFile: null,
    imagePath: "",
    sampleId: "",
    topK: 3,
    evidenceK: 3,
  });
  const [state, setState] = useState({ status: "idle", result: null, error: null });
  const [previewUrl, setPreviewUrl] = useState("");
  const datasetVersionOptions = datasetOptions.map((dataset) => dataset.datasetVersionId).filter(Boolean);
  const modelVersionOptions = inferenceTrainingRuns
    .map((run) => run.modelVersionId)
    .filter(Boolean);
  const canUseInferenceInputs = datasetSource === "api" && trainingSource === "api" && datasetVersionOptions.length > 0 && modelVersionOptions.length > 0;
  const canRun =
    canUseInferenceInputs &&
    state.status !== "running" &&
    form.datasetVersionId.trim() &&
    form.modelVersionId.trim() &&
    (form.imageFile || form.imagePath.trim() || form.sampleId.trim());
  const inferenceBlockReason = canUseInferenceInputs
    ? ""
    : datasetSource !== "api"
      ? "Control-plane API 暂不可用，不能运行真实推理。"
      : trainingSource !== "api"
        ? "Training API 暂不可用，不能运行真实推理。"
        : modelVersionOptions.length === 0
          ? "当前没有真实候选模型版本，请先完成训练。"
          : "当前没有真实 dataset version 可用于推理。";

  useEffect(() => {
    if (datasetSource !== "api" || datasetVersionOptions.length === 0) return;
    setForm((current) => {
      const next = { ...current };
      if (!datasetVersionOptions.includes(next.datasetVersionId)) {
        next.datasetVersionId = datasetVersionOptions[0];
      }
      if (modelVersionOptions.length > 0 && !modelVersionOptions.includes(next.modelVersionId)) {
        next.modelVersionId = modelVersionOptions[0];
      }
      return next;
    });
  }, [datasetSource, datasetVersionOptions.join("|"), modelVersionOptions.join("|")]);

  useEffect(() => {
    if (!form.imageFile) {
      setPreviewUrl("");
      return undefined;
    }
    const objectUrl = URL.createObjectURL(form.imageFile);
    setPreviewUrl(objectUrl);
    return () => URL.revokeObjectURL(objectUrl);
  }, [form.imageFile]);

  function updateField(field, value) {
    setForm((current) => ({ ...current, [field]: value }));
  }

  function updateImageFile(file) {
    setForm((current) => ({
      ...current,
      imageFile: file,
      imagePath: file ? "" : current.imagePath,
      sampleId: file ? "" : current.sampleId,
    }));
  }

  async function handleRun() {
    if (!canRun) return;
    setState({ status: "running", result: null, error: null });
    try {
      const commonInput = {
        dataset_version_id: form.datasetVersionId.trim(),
        model_version_id: form.modelVersionId.trim(),
        top_k: Number(form.topK),
        evidence_k: Number(form.evidenceK),
      };
      const result = form.imageFile
        ? await runInferenceUpload({
            ...commonInput,
            image: form.imageFile,
          })
        : await runInference({
            ...commonInput,
            image_path: form.imagePath.trim() || null,
            sample_id: form.sampleId.trim() || null,
          });
      setState({ status: "succeeded", result, error: null });
      showToast("推理完成，结果已更新");
    } catch (error) {
      setState({ status: "failed", result: null, error });
      showToast("Inference API 请求失败");
    }
  }

  const result = state.result;
  const decisionState = inferenceDecisionStatus(result?.decision);
  const decisionCopy = inferenceDecisionCopy(result?.decision);
  const queryLabel = form.imageFile?.name || form.sampleId || form.imagePath || "query image";

  async function handleGenerateInferenceExplanation() {
    if (!result) return;
    await llm.generate({
      task: "inference_explanation",
      context: {
        inference_event_id: result.inferenceEventId,
        review_item_id: result.reviewItemId,
        dataset_id: result.datasetId,
        dataset_version_id: result.datasetVersionId,
        model_version_id: result.modelVersionId,
        decision: result.decision,
        top_k: result.topK,
        nearest_neighbors: result.nearestNeighbors.slice(0, 5),
      },
    });
  }

  return (
    <div className="grid detail">
      <Panel title="输入样本" caption="绑定数据版本和模型版本后运行 scoped inference。" action={<StatusChip tone={state.status === "running" ? "info" : "neutral"}>{state.status === "running" ? "运行中" : "实验室"}</StatusChip>}>
        {previewUrl ? (
          <div className="uploaded-preview">
            <img src={previewUrl} alt={queryLabel} />
            <span>{queryLabel}</span>
          </div>
        ) : (
          <SampleImage src={sampleImageFor("bird")} label={queryLabel} low />
        )}
        <div className="field-grid section-gap-small">
          <div className="field">
            <label>数据版本</label>
            <input list="dataset-version-options" value={form.datasetVersionId} onChange={(event) => updateField("datasetVersionId", event.target.value)} placeholder="dataset@..." />
            <datalist id="dataset-version-options">
              {datasetOptions.map((dataset) => (
                <option value={dataset.datasetVersionId} key={dataset.datasetVersionId}>{dataset.datasetVersionId}</option>
              ))}
            </datalist>
          </div>
          <div className="field">
            <label>模型版本</label>
            <input list="model-version-options" value={form.modelVersionId} onChange={(event) => updateField("modelVersionId", event.target.value)} placeholder="model_version_id" />
            <datalist id="model-version-options">
              {modelVersionOptions.map((modelVersionId) => (
                <option value={modelVersionId} key={modelVersionId}>{modelVersionId}</option>
              ))}
            </datalist>
          </div>
          <div className="field full-span">
            <label>上传图片</label>
            <label className="file-picker">
              <input type="file" accept="image/png,image/jpeg,image/webp,image/bmp" onChange={(event) => updateImageFile(event.target.files?.[0] ?? null)} />
              <Icon name="ImageUp" size={18} />
              <span>{form.imageFile?.name || "选择一张图片作为 query"}</span>
            </label>
          </div>
          <div className="field">
            <label>样本 ID</label>
            <input value={form.sampleId} onChange={(event) => updateField("sampleId", event.target.value)} disabled={Boolean(form.imageFile)} placeholder="feature artifact sample_id" />
          </div>
          <div className="field">
            <label>Top-k</label>
            <input type="number" min="1" max="10" value={form.topK} onChange={(event) => updateField("topK", event.target.value)} />
          </div>
          <div className="field">
            <label>近邻数</label>
            <input type="number" min="0" max="10" value={form.evidenceK} onChange={(event) => updateField("evidenceK", event.target.value)} />
          </div>
        </div>
        <details className="advanced-fields">
          <summary>高级：使用容器内图片路径</summary>
          <div className="field section-gap-small">
            <label>图片路径</label>
            <input value={form.imagePath} onChange={(event) => updateField("imagePath", event.target.value)} disabled={Boolean(form.imageFile)} placeholder="/absolute/path/to/image.png" />
          </div>
        </details>
        <div className="toolbar section-gap-small">
          <button className="primary-button" onClick={handleRun} disabled={!canRun}><Icon name={state.status === "running" ? "LoaderCircle" : "Play"} size={16} />{state.status === "running" ? "推理中" : "运行推理"}</button>
          <button className="ghost-button" onClick={() => updateImageFile(null)} disabled={!form.imageFile || state.status === "running"}><Icon name="RefreshCw" size={16} />清除图片</button>
        </div>
        {inferenceBlockReason && (
          <div className="route-box section-gap-small">
            <div><strong>推理运行已暂停</strong><div className="row-meta">{inferenceBlockReason}</div></div>
            <StatusChip tone="warn">api required</StatusChip>
          </div>
        )}
      </Panel>
      <Panel title="推理结果" caption="MVP 推理实验室：结果会记录为 inference event；abstain / reject_ood 会路由到人工复核。" action={<StatusChip tone={decisionState.tone}>{decisionState.label}</StatusChip>}>
        {state.status === "idle" && (
            <div className="timeline-item">
              <div className="timeline-icon"><Icon name="ScanSearch" size={18} /></div>
            <div><strong>等待推理输入</strong><div className="row-meta">上传图片，或填写样本 ID 后运行推理。</div></div>
            <StatusChip tone="info">idle</StatusChip>
          </div>
        )}
        {state.status === "running" && (
          <div className="timeline-item">
            <div className="timeline-icon"><Icon name="LoaderCircle" size={18} /></div>
            <div><strong>正在运行推理</strong><div className="row-meta">{form.datasetVersionId} · {form.modelVersionId}</div><ProgressBar value={72} fill="#315fbd" shimmer /></div>
            <StatusChip tone="info">running</StatusChip>
          </div>
        )}
        {state.status === "failed" && (
          <div className="timeline-item">
            <div className="timeline-icon"><Icon name="AlertTriangle" size={18} /></div>
            <div><strong>推理失败</strong><div className="row-meta">{state.error?.message ?? "Inference API 请求失败"}</div></div>
            <StatusChip tone="risk">failed</StatusChip>
          </div>
        )}
        {state.status === "succeeded" && result && (
          <>
            <div className="decision-summary">
              <div className="decision-callout">
                <div className="timeline-icon"><Icon name={decisionState.icon} size={18} /></div>
                <div>
                  <strong>{decisionCopy.title}</strong>
                  <div className="row-meta">{decisionCopy.body}</div>
                </div>
                <StatusChip tone={decisionState.tone}>{result.decision.value}</StatusChip>
              </div>
              <div className="evidence-metrics">
                <div><span>confidence</span><strong>{result.decision.confidence.toFixed(4)}</strong></div>
                <div><span>margin</span><strong>{result.decision.margin.toFixed(4)}</strong></div>
                <div><span>ood score</span><strong>{result.decision.oodScore?.toFixed?.(4) ?? "n/a"}</strong></div>
              </div>
              <div className="reason-box">
                <strong>触发原因</strong>
                <span>{result.decision.reasons.length > 0 ? result.decision.reasons.map(reasonLabel).join("，") : "API 未返回阈值原因"}</span>
              </div>
              <div className="route-box">
                <div>
                  <strong>{result.reviewItemId ? "已创建人工复核项" : "未进入人工复核队列"}</strong>
                  <div className="row-meta">
                    {result.reviewItemId ? `${result.reviewItemId} · 可直接打开处理。` : "accept 结果只记录 inference event；需要抽检时可后续增加 accept audit 策略。"}
                  </div>
                </div>
                {result.reviewItemId ? (
                  <Link className="primary-button" to={`/review/${result.reviewItemId}?status=pending&dataset_id=${encodeURIComponent(result.datasetId ?? "")}`}>
                    <Icon name="UserCheck" size={16} />
                    打开复核项
                  </Link>
                ) : (
                  <StatusChip tone="default">event recorded</StatusChip>
                )}
              </div>
            </div>
            <div className="section-gap-small">
              {result.topK.length > 0 ? (
                result.topK.map((candidate, index) => (
                  <CandidateBar label={candidate.label} score={candidate.score} fill={index === 0 ? "#0891b2" : index === 1 ? "#a15c07" : "#315fbd"} key={`${candidate.label}-${index}`} />
                ))
              ) : (
                <div className="row-meta">API 未返回候选类别。</div>
              )}
            </div>
            <details className="evidence-details section-gap-small">
              <summary>
                <span><Icon name="GitCompare" size={16} />高级证据：近邻样本</span>
                <StatusChip tone="neutral">{result.nearestNeighbors.length} 条</StatusChip>
              </summary>
              <div className="neighbor-list">
                {result.nearestNeighbors.length > 0 ? (
                  result.nearestNeighbors.map((neighbor, index) => (
                    <div className="neighbor-row" key={neighbor.sampleId || `${neighbor.label}-${index}`}>
                      <div className="neighbor-rank">{index + 1}</div>
                      <div><strong>{neighbor.label}</strong><div className="row-meta">{neighbor.sampleId || "unknown sample"}</div></div>
                      <div className="neighbor-distance"><span>distance</span><strong>{neighbor.distance?.toFixed(4) ?? "n/a"}</strong></div>
                    </div>
                  ))
                ) : (
                  <div className="empty-evidence">
                    <Icon name="ImageOff" size={18} />
                    <span>暂无近邻证据。当前结果仍可基于 top-k 和阈值原因判断。</span>
                  </div>
                )}
              </div>
            </details>
            <LLMAssistanceBox
              title="LLM 推理解释"
              caption="只解释当前推理证据，不改变 inference event 或复核路由。"
              assistance={llm.assistance}
              status={llm.status}
              error={llm.error}
              onGenerate={handleGenerateInferenceExplanation}
            />
            <details className="advanced-fields section-gap-small">
              <summary>调试信息</summary>
              <div className="code-panel section-gap-small">event: {result.inferenceEventId ?? "n/a"}<br />model: {result.modelVersionId}<br />strategy: {result.thresholdStrategyId}<br />feature: {result.featureArtifactId ?? "n/a"}</div>
            </details>
          </>
        )}
      </Panel>
    </div>
  );
}

export function ReviewPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const requestedStatus = searchParams.get("status") || "pending";
  const statusFilter = REVIEW_STATUS_TABS.some(([value]) => value === requestedStatus) ? requestedStatus : "pending";
  const datasetFilter = searchParams.get("dataset_id") || "";
  const { datasets: datasetItems } = useDatasets();
  const { reviewItems: apiReviewItems, loading, error, refresh } = useReviewItems({
    status: statusFilter,
    datasetId: datasetFilter || undefined,
    limit: 80,
  });
  const oodCount = apiReviewItems.filter((item) => item.riskType === "ood_candidate").length;
  const lowConfidenceCount = apiReviewItems.filter((item) => item.riskType === "low_confidence").length;
  const lowMarginCount = apiReviewItems.filter((item) => item.riskType === "low_margin").length;
  const queryString = searchParams.toString();
  const datasetOptions = Array.from(
    new Map(
      [
        ...datasetItems.map((dataset) => [dataset.id, dataset.name || dataset.id]),
        ...apiReviewItems.map((item) => [item.datasetId, item.datasetId]),
        datasetFilter ? [datasetFilter, datasetFilter] : null,
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
    setSearchParams(next);
  }

  return (
    <>
      <PageHero title="让人工只处理模型真正不确定的样本。" description="模型弃权和 OOD 候选进入复核队列；人工结论只进入反馈池，不直接污染训练集。" actions={<button className="ghost-button" onClick={refresh}><Icon name="RefreshCw" size={16} />刷新</button>} />
      <div className="grid review">
        <Panel title={statusFilter === "feedbacked" ? "历史复核" : statusFilter === "all" ? "全部复核项" : "待复核队列"} caption={loading ? "正在连接 Review API。" : `${apiReviewItems.length} 条样本。`}>
          <div className="review-filter-bar">
            <div className="tabs">
              {REVIEW_STATUS_TABS.map(([value, label]) => (
                <button className={`tab-button ${statusFilter === value ? "active" : ""}`} key={value} onClick={() => updateReviewFilter("status", value)}>
                  {label}
                </button>
              ))}
            </div>
            <label className="filter-select">
              <span>数据集</span>
              <select value={datasetFilter} onChange={(event) => updateReviewFilter("dataset_id", event.target.value)}>
                <option value="">全部数据集</option>
                {datasetOptions.map(([id, label]) => (
                  <option value={id} key={id}>{label}</option>
                ))}
              </select>
            </label>
          </div>
          {error && (
            <div className="timeline-item">
              <div className="timeline-icon"><Icon name="AlertTriangle" size={18} /></div>
              <div><strong>Review API 请求失败</strong><div className="row-meta">{error.message}</div></div>
              <StatusChip tone="risk">error</StatusChip>
            </div>
          )}
          {!error && apiReviewItems.length === 0 && (
            <div className="timeline-item">
              <div className="timeline-icon"><Icon name="CheckCircle2" size={18} /></div>
              <div><strong>{loading ? "正在加载队列" : "当前筛选下没有样本"}</strong><div className="row-meta">{loading ? "Review API 正在返回结果。" : statusFilter === "pending" ? "abstain / reject_ood 推理会自动进入这里。" : "可以切回待复核或全部查看其他记录。"}</div></div>
              <StatusChip tone={loading ? "info" : "default"}>{loading ? "loading" : "clear"}</StatusChip>
            </div>
          )}
          <div className="grid">
            {apiReviewItems.map((item) => (
              <ApiReviewCard item={item} queryString={queryString} key={item.id} />
            ))}
          </div>
        </Panel>
        <Panel title="队列摘要" caption="统计当前筛选结果；历史入口在左侧状态切换中。">
          <div className="timeline">
            <div className="timeline-item"><div className="timeline-icon"><Icon name="ShieldAlert" size={18} /></div><div><strong>{oodCount} 条 OOD 候选</strong><div className="row-meta">只代表模型拒识，需要人工确认后才进入 OOD 压力池。</div></div><StatusChip tone="risk">OOD</StatusChip></div>
            <div className="timeline-item"><div className="timeline-icon"><Icon name="Gauge" size={18} /></div><div><strong>{lowConfidenceCount} 条低置信</strong><div className="row-meta">置信度低于阈值，建议确认最终类别或标记不确定。</div></div><StatusChip tone="warn">conf</StatusChip></div>
            <div className="timeline-item"><div className="timeline-icon"><Icon name="GitCompare" size={18} /></div><div><strong>{lowMarginCount} 条低间隔</strong><div className="row-meta">top-1 与 top-2 接近，优先检查易混类别。</div></div><StatusChip tone="info">margin</StatusChip></div>
          </div>
        </Panel>
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
  const [form, setForm] = useState({
    finalOutcome: "corrected_label",
    destination: "training_candidate",
    finalLabel: "",
    reviewerNote: "",
  });

  useEffect(() => {
    setForm((current) => ({ ...current, destination: destinationForOutcome(current.finalOutcome) }));
  }, [form.finalOutcome]);

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
      const globalNext = sameDatasetNext.length > 0 ? sameDatasetNext : await listReviewItems({ status: "pending", limit: 1 });
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
        <PageHero title="复核详情" description="正在连接 Review API。" actions={<Link className="ghost-button" to="/review"><Icon name="ArrowLeft" size={16} />返回队列</Link>} />
        <Panel title="加载中" caption="正在读取复核上下文。"><ProgressBar value={64} fill="#315fbd" shimmer /></Panel>
      </>
    );
  }

  if (!item || error) {
    return (
      <>
        <PageHero title="复核项不存在" description={error?.message ?? "Review API 没有返回这个复核项。"} actions={<Link className="ghost-button" to="/review"><Icon name="ArrowLeft" size={16} />返回队列</Link>} />
        <Panel title="无法打开复核详情" caption="请从真实队列中选择一个待复核样本。"><StatusChip tone="risk">not found</StatusChip></Panel>
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
  const storedAssistance = assistanceFromMetadata(item.assistanceMetadata);
  const reviewAssistance = reviewAssistant.assistance ?? storedAssistance;
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
      <PageHero title={item.sampleId || item.id} description={`${item.datasetVersionId} · ${item.modelVersionId} · ${item.reason}`} actions={<><Link className="ghost-button" to={queuePath}><Icon name="ArrowLeft" size={16} />返回队列</Link><StatusChip tone={statusInfo.tone}>{statusInfo.label}</StatusChip></>} />
      <div className="grid detail">
        <Panel title="模型证据" caption="保留推理当时的图像、top-k、阈值原因和近邻证据。">
          <ReviewImage item={item} risk={risk} detail />
          <div className="chips section-gap-small">
            <StatusChip tone={risk.tone}>{risk.label}</StatusChip>
            <StatusChip tone="info">priority {item.priority}</StatusChip>
            <StatusChip tone={item.decision.value === "reject_ood" ? "risk" : "warn"}>{item.decision.value}</StatusChip>
          </div>
          <div className="evidence-metrics section-gap-small">
            <div><span>confidence</span><strong>{item.decision.confidence.toFixed(4)}</strong></div>
            <div><span>margin</span><strong>{item.decision.margin.toFixed(4)}</strong></div>
            <div><span>ood score</span><strong>{item.decision.oodScore?.toFixed?.(4) ?? "n/a"}</strong></div>
          </div>
          <div className="reason-box section-gap-small">
            <strong>复核原因</strong>
            <span>{item.reasonCodes.join(", ") || item.reason}</span>
          </div>
          <div className="section-gap-small">
            {item.topK.length > 0 ? (
              item.topK.map((candidate, index) => (
                <CandidateBar label={candidate.label} score={candidate.score} fill={index === 0 ? "#0891b2" : index === 1 ? "#a15c07" : "#315fbd"} key={`${candidate.label}-${index}`} />
              ))
            ) : (
              <div className="row-meta">没有 top-k 候选。</div>
            )}
          </div>
          <details className="evidence-details section-gap-small">
            <summary>
              <span><Icon name="GitCompare" size={16} />高级证据：近邻样本</span>
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
                      <strong>{neighbor.distance?.toFixed(4) ?? "n/a"}</strong>
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
        </Panel>
        <Panel title="LLM 辅助" caption="只读建议，不是最终结论；不会写入真值、不会提交反馈池。">
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
        <Panel title="人工复核" caption="人工结论进入反馈池；后续数据版本构建再决定是否采纳。" action={<StatusChip tone={statusInfo.tone}>{statusInfo.label}</StatusChip>}>
          <div className="grid">
            {item.feedback && (
              <div className="feedback-summary">
                <div><span>最终结论</span><strong>{item.feedback.final_outcome}</strong></div>
                <div><span>反馈池</span><strong>{item.feedback.destination}</strong></div>
                <div><span>最终标签</span><strong>{item.feedback.final_label ?? "n/a"}</strong></div>
                <div><span>备注</span><strong>{item.feedback.reviewer_note ?? "none"}</strong></div>
              </div>
            )}
            {!item.feedback && (
              <>
                <div className="field-grid">
                  <div className="field">
                    <label>最终结论</label>
                    <select value={form.finalOutcome} onChange={(event) => updateReviewField("finalOutcome", event.target.value)}>
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
                    <select value={form.destination} onChange={(event) => updateReviewField("destination", event.target.value)}>
                      {destinationOptions.map(([value, label]) => (
                        <option value={value} key={value}>{label}</option>
                      ))}
                    </select>
                  </div>
                  <div className="field full-span">
                    <label>最终标签</label>
                    <input list="review-label-options" value={form.finalLabel} onChange={(event) => updateReviewField("finalLabel", event.target.value)} disabled={!requiresLabel} placeholder={requiresLabel ? "选择或输入最终类别" : "该结论不需要类别标签"} />
                    <datalist id="review-label-options">
                      {candidateLabels.map((label) => (
                        <option value={label} key={label}>{label}</option>
                      ))}
                    </datalist>
                  </div>
                </div>
                <div className="field">
                  <label>审核备注</label>
                  <textarea value={form.reviewerNote} onChange={(event) => updateReviewField("reviewerNote", event.target.value)} placeholder="记录人工判断依据。" />
                </div>
                {submitState.error && <div className="row-meta">提交失败：{submitState.error.message}</div>}
                <div className="toolbar">
                  <button className="primary-button" onClick={() => handleSubmit()} disabled={!canSubmit}><Icon name={submitState.status === "submitting" ? "LoaderCircle" : "Check"} size={16} />{submitState.status === "submitting" ? "提交中" : "提交并下一张"}</button>
                  <button className="ghost-button" onClick={() => handleSubmit({ stay: true })} disabled={!canSubmit}><Icon name="CheckCircle2" size={16} />提交后留在此页</button>
                  <button className="ghost-button" onClick={refresh}><Icon name="RefreshCw" size={16} />刷新</button>
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
  const visualKey =
    item.destination === "ood_stress" ? "ood" : item.destination === "bad_image" ? "defect" : item.finalLabel || item.sampleId || "bird";
  return (
    <div className="feedback-card">
      {item.imageUrl ? (
        <div className="review-image">
          <img src={item.imageUrl} alt={item.sampleId || item.id} />
          <span>{item.sampleId || item.id}</span>
        </div>
      ) : (
        <SampleImage src={sampleImageFor(visualKey)} label={item.sampleId || item.id} compact low={item.destination !== "ood_stress"} />
      )}
      <div className="feedback-card-body">
        <div className="chips">
          <StatusChip tone={item.destination === "ood_stress" ? "risk" : item.destination === "training_candidate" ? "default" : "warn"}>
            {feedbackDestinationLabel(item.destination)}
          </StatusChip>
          <StatusChip tone="info">{feedbackOutcomeLabel(item.finalOutcome)}</StatusChip>
        </div>
        <h3>{item.finalLabel || item.sampleId || item.id}</h3>
        <p className="small">{item.datasetVersionId || item.datasetId || "unknown dataset"} · {item.modelVersionId || "unknown model"}</p>
        <p className="small review-reason">{item.reviewerNote || "暂无人工备注。"}</p>
        <div className="toolbar spread section-gap-small">
          <span className="row-meta">{item.createdBy || "local-reviewer"} · {item.createdAt ? new Date(item.createdAt).toLocaleString() : "unknown time"}</span>
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
    inspectionNotes: raw.inspectionNotes ?? raw.inspection_notes ?? [],
    suggestedActions: raw.suggestedActions ?? raw.suggested_actions ?? [],
    riskFlags: raw.riskFlags ?? raw.risk_flags ?? [],
    model: raw.model ?? null,
    createdAt: raw.createdAt ?? raw.created_at ?? null,
    confidence: raw.confidence ?? "unknown",
  };
}

function LLMAssistanceBox({ title = "LLM 辅助", caption, assistance, status = "idle", error, onGenerate, disabled = false }) {
  const isGenerating = status === "generating";
  const hasAssistance = Boolean(assistance?.summary);
  return (
    <div className="llm-assistance">
      <div className="llm-assistance-head">
        <div>
          <strong>{title}</strong>
          <div className="row-meta">{caption || "LLM 仅提供辅助建议，不写入真值、不提交反馈池。"}</div>
        </div>
        <button className="ghost-button" onClick={onGenerate} disabled={disabled || isGenerating}>
          <Icon name={isGenerating ? "LoaderCircle" : "Wand2"} size={16} />
          {isGenerating ? "生成中" : hasAssistance ? "重新生成" : "生成建议"}
        </button>
      </div>
      {error && (
        <div className="route-box risk section-gap-small">
          <div><strong>LLM 辅助暂不可用</strong><div className="row-meta">{error.message}</div></div>
          <StatusChip tone="risk">error</StatusChip>
        </div>
      )}
      {!error && !hasAssistance && (
        <div className="route-box section-gap-small">
          <div><strong>尚未生成辅助建议</strong><div className="row-meta">这不会影响人工复核、训练或反馈池操作。</div></div>
          <StatusChip tone="info">optional</StatusChip>
        </div>
      )}
      {hasAssistance && (
        <div className="section-gap-small">
          <div className="reason-box">
            <strong>建议摘要</strong>
            <span>{assistance.summary}</span>
          </div>
          <div className="timeline section-gap-small">
            <LLMListItem icon="ScanSearch" title="检查点" items={assistance.inspectionNotes} empty="没有返回检查点。" />
            <LLMListItem icon="CheckCircle2" title="建议动作" items={assistance.suggestedActions} empty="没有返回建议动作。" />
            <LLMListItem icon="ShieldAlert" title="风险提示" items={assistance.riskFlags} empty="没有额外风险提示。" tone="warn" />
          </div>
          <div className="chips">
            <StatusChip tone="info">advisory only</StatusChip>
            {assistance.model && <StatusChip tone="neutral">{assistance.model}</StatusChip>}
            {assistance.createdAt && <StatusChip tone="neutral">{new Date(assistance.createdAt).toLocaleString()}</StatusChip>}
          </div>
        </div>
      )}
    </div>
  );
}

function LLMListItem({ icon, title, items = [], empty, tone = "info" }) {
  return (
    <div className="timeline-item">
      <div className="timeline-icon"><Icon name={icon} size={18} /></div>
      <div>
        <strong>{title}</strong>
        {items.length > 0 ? (
          <ul className="llm-list">
            {items.map((item, index) => (
              <li key={`${title}-${index}`}>{item}</li>
            ))}
          </ul>
        ) : (
          <div className="row-meta">{empty}</div>
        )}
      </div>
      <StatusChip tone={tone}>{items.length}</StatusChip>
    </div>
  );
}

export function FeedbackPage() {
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
        description="复核结论在这里作为下一轮数据集版本的候选输入；当前不会自动写回训练集，也不会自动触发训练。"
        actions={<><Link className="ghost-button" to="/review?status=feedbacked"><Icon name="UserCheck" size={16} />复核历史</Link><button className="ghost-button" onClick={refresh}><Icon name="RefreshCw" size={16} />刷新</button></>}
      />
      <div className="grid detail">
        <Panel title="反馈池列表" caption={loading ? "正在连接 Feedback API。" : `${feedbackItems.length} 条反馈。`}>
          <div className="review-filter-bar">
            <div className="tabs">
              {FEEDBACK_DESTINATIONS.map(([value, label]) => (
                <button className={`tab-button ${destinationFilter === value ? "active" : ""}`} key={value} onClick={() => updateFeedbackFilter("destination", value)}>
                  {label}
                </button>
              ))}
            </div>
            <label className="filter-select">
              <span>数据集</span>
              <select value={datasetFilter} onChange={(event) => updateFeedbackFilter("dataset_id", event.target.value)}>
                <option value="">全部数据集</option>
                {datasetOptions.map(([id, label]) => (
                  <option value={id} key={id}>{label}</option>
                ))}
              </select>
            </label>
          </div>
          {error && (
            <div className="timeline-item">
              <div className="timeline-icon"><Icon name="AlertTriangle" size={18} /></div>
              <div><strong>Feedback API 请求失败</strong><div className="row-meta">{error.message}</div></div>
              <StatusChip tone="risk">error</StatusChip>
            </div>
          )}
          {!error && feedbackItems.length === 0 && (
            <div className="timeline-item">
              <div className="timeline-icon"><Icon name="DatabaseZap" size={18} /></div>
              <div><strong>{loading ? "正在加载反馈池" : "当前筛选下没有反馈"}</strong><div className="row-meta">完成人工复核后，反馈会按目的地进入这里。</div></div>
              <StatusChip tone={loading ? "info" : "default"}>{loading ? "loading" : "empty"}</StatusChip>
            </div>
          )}
          <div className="feedback-list">
            {feedbackItems.map((item) => <FeedbackCard item={item} key={item.id} />)}
          </div>
        </Panel>
        <Panel title="策展门禁" caption="MVP 只做候选池可见，不自动生成新数据集版本。">
          <div className="feedback-summary">
            {poolCounts.map(([value, label, count]) => (
              <div key={value}><span>{label}</span><strong>{count}</strong></div>
            ))}
          </div>
          <div className="timeline section-gap">
            <div className="timeline-item"><div className="timeline-icon"><Icon name="ShieldCheck" size={18} /></div><div><strong>不会直接污染训练集</strong><div className="row-meta">训练仍只能选择不可变 dataset_version。</div></div><StatusChip tone="default">guarded</StatusChip></div>
            <div className="timeline-item"><div className="timeline-icon"><Icon name="Database" size={18} /></div><div><strong>下一步：数据策展</strong><div className="row-meta">后续会把已采纳反馈冻结成新的 dataset version。</div></div><StatusChip tone="warn">deferred</StatusChip></div>
            <div className="timeline-item"><div className="timeline-icon"><Icon name="Route" size={18} /></div><div><strong>发布前再消费</strong><div className="row-meta">模型发布门禁应检查 OOD 压力池、坏图池和争议池处理状态。</div></div><StatusChip tone="info">gate</StatusChip></div>
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

export function ModelsPage() {
  const { trainingRuns: modelRuns, source, loading } = useTrainingRuns();
  const apiModels = modelRuns.filter((run) => run.modelVersionId).map(modelRecordFromRun);
  const calibratedModels = apiModels.filter((model) => model.calibrationArtifactId && model.thresholdStrategyId);
  const sourceLabel = loading ? "正在连接 Training API" : source === "api" ? "Training API 派生候选模型" : "Training API 暂不可用";
  return (
    <>
      <div className="grid metrics">
        <MetricCard title="Production" value="--" caption="Model Registry API 待接入" fill="#0f766e" percent={0} icon="Rocket" to="/models" />
        <MetricCard title="候选版本" value={loading ? "..." : String(apiModels.length)} caption={sourceLabel} fill="#315fbd" percent={Math.min(100, apiModels.length * 28)} icon="GitCompare" to="/training" />
        <MetricCard title="已校准候选" value={String(calibratedModels.length)} caption="calibration + threshold strategy" fill="#26804f" percent={apiModels.length ? Math.round((calibratedModels.length / apiModels.length) * 100) : 0} icon="Thermometer" to="/training" />
        <MetricCard title="发布门禁" value="待接入" caption="需要 registry/promote/rollback API" fill="#a15c07" percent={0} icon="WalletCards" to="/feedback" />
      </div>
      <div className="grid two section-gap">
        <Panel title="模型候选" caption={apiModels.length > 0 ? "来自已完成训练运行；不是 production registry。" : "暂无真实候选模型；完成训练后会出现在这里。"}>
          {apiModels.length > 0 ? (
            <div className="grid three">{apiModels.map((model) => <ModelCard model={model} key={model.id} />)}</div>
          ) : (
            <div className="empty-state"><Icon name="Boxes" size={24} /><strong>没有可展示的真实模型版本</strong><span>这里不再显示本地 mock 模型。请先在训练页完成一次训练。</span></div>
          )}
        </Panel>
        <Panel title="发布门禁" caption="生产系统不允许只凭 accuracy 上线。"><div className="timeline"><GateRow title="离线评估" description={apiModels.length > 0 ? "读取 training report / metrics" : "等待真实候选模型"} result={apiModels.length > 0 ? "pass" : "pending"} /><GateRow title="OOD 压力集" description="等待反馈池策展和压力集冻结" result="pending" /><GateRow title="人工抽检" description="等待复核队列和反馈池清理" result="pending" /><GateRow title="回滚策略" description="Model Registry promote/rollback API 待接入" result="pending" /></div></Panel>
      </div>
    </>
  );
}

export function ModelDetailPage({ showToast }) {
  const { modelId = "" } = useParams();
  const { trainingRuns: modelRuns, source, loading } = useTrainingRuns();
  const apiModel = modelRuns.filter((run) => run.modelVersionId).map(modelRecordFromRun).find((item) => item.id === modelId);
  if (!apiModel) {
    return (
      <>
        <PageHero title="模型版本不可用" description={loading ? "正在连接 Training API。" : `${modelId || "unknown"} 没有匹配的真实候选模型；本页不再回退到本地 mock 模型。`} actions={<Link className="ghost-button" to="/models"><Icon name="ArrowLeft" size={16} />返回</Link>} />
        <Panel title="下一步" caption={source === "api" ? "Training API 已连接，但没有找到该 model_version_id。" : "Training API 暂不可用。"}>
          <div className="timeline">
            <GateRow title="完成训练" description="训练成功后会生成候选 model_version_id。" result="pending" />
            <GateRow title="补齐产物" description="需要 model artifact、calibration report 和 threshold strategy。" result="pending" />
            <GateRow title="发布注册表" description="Model Registry API 后续接入 promote / rollback。" result="pending" />
          </div>
        </Panel>
      </>
    );
  }
  const model = apiModel;
  const accuracy = Number.isFinite(Number(model.accuracy)) ? `${Number(model.accuracy).toFixed(1)}%` : "待生成";
  const coverage = Number.isFinite(Number(model.coverage)) ? `${Number(model.coverage).toFixed(1)}%` : "待生成";
  const selectiveRisk = Number.isFinite(Number(model.selectiveRisk)) ? `${Number(model.selectiveRisk).toFixed(2)}%` : "待生成";
  const sourceLabel = loading ? "正在连接 Training API" : "Training API candidate";
  return (
    <>
      <PageHero title={model.id} description={`${sourceLabel} · 模型详情页把权重、数据版本、阈值策略、评估报告、发布门禁和回滚配置放在一起。`} actions={<><Link className="ghost-button" to="/models"><Icon name="ArrowLeft" size={16} />返回</Link><button className="primary-button" disabled><Icon name="Rocket" size={16} />发布流程待接入</button></>} />
      <div className="grid two">
        <Panel title="版本元数据" caption="来自训练运行和 artifact metadata。"><div className="code-panel">model: {model.id}<br />run: {model.runId ?? "n/a"}<br />job: {model.jobId ?? "n/a"}<br />dataset: {model.datasetVersionId ?? "n/a"}<br />features: {model.featureArtifactId ?? "n/a"}<br />model_artifact: {model.modelArtifactId ?? "n/a"}<br />calibration: {model.calibrationArtifactId ?? "n/a"}<br />threshold: {model.thresholdStrategyId ?? "n/a"}<br />report: {model.reportArtifactId ?? "n/a"}<br />source: {model.source}</div></Panel>
        <Panel title="评估指标" caption="包含自动覆盖和弃权后的准确率；缺失时不填假数。"><CurveRow label="top-1 accuracy" value={accuracy} percent={Number(model.accuracy) || 0} fill="#0f766e" /><CurveRow label="coverage" value={coverage} percent={Number(model.coverage) || 0} fill="#315fbd" /><CurveRow label="selective risk" value={selectiveRisk} percent={Number(model.selectiveRisk) || 0} fill="#a15c07" /></Panel>
      </div>
    </>
  );
}

export function PipelinesPage() {
  const [searchParams] = useSearchParams();
  const selectedJobId = searchParams.get("job_id") || "";
  return (
    <>
      <PageHero title="把数据、训练、弃权和发布串成可重跑流程。" description="流水线视图面向工程实现：每个节点都有输入产物、输出产物、日志和失败恢复点。" actions={<button className="primary-button" disabled><Icon name="Play" size={16} />流水线执行待接入</button>} />
      <Panel title="模板预览：DINOv3 分类头训练" caption="这是只读流程模板；真实任务状态请看下方 Control-plane jobs。">
        <div className="pipeline">{pipelineNodes.map((node) => <PipelineNode node={node} key={node.id} />)}</div>
      </Panel>
      <div className="grid two section-gap">
        <RecentJobsPanel selectedJobId={selectedJobId} />
        <Panel title="Worker 边界" caption="MVP 阶段先查询任务状态，不在浏览器里直接触发模型计算。">
          <div className="code-panel">control-plane: /api/jobs<br />selected_job: {selectedJobId || "none"}<br />worker: feature extraction / train / calibration<br />storage: artifacts + metadata store<br />frontend: poll job status only</div>
        </Panel>
      </div>
    </>
  );
}

export function PipelineRunPage({ showToast }) {
  const { pipelineRunId = "" } = useParams();
  return (
    <>
      <PageHero title="流水线运行详情待接入" description={`${pipelineRunId || "unknown"} · 当前请在流水线页通过 job_id 查看真实 Control-plane job 状态。`} actions={<><Link className="ghost-button" to="/pipelines"><Icon name="ArrowLeft" size={16} />返回</Link><button className="primary-button" disabled><Icon name="Pause" size={16} />暂停待接入</button></>} />
      <div className="grid two">
        <Panel title="运行节点" caption="不展示静态假日志；等待 Pipeline Run API 接入。"><div className="timeline"><GateRow title="解析运行 ID" description={pipelineRunId || "n/a"} result="pending" /><GateRow title="读取 job 状态" description="请使用 /pipelines?job_id=<job_id>" result="pending" /><GateRow title="读取产物链接" description="artifact store API 待接入" result="pending" /><GateRow title="失败重试" description="pipeline orchestration 待接入" result="pending" /></div></Panel>
        <Panel title="真实状态入口" caption="当前 MVP 已接入 /api/jobs 列表和查询。"><div className="code-panel">pipeline_run_id: {pipelineRunId || "n/a"}<br />source: not connected<br />job_status_route: /pipelines?job_id=&lt;job_id&gt;<br />static_demo_log: disabled</div></Panel>
      </div>
    </>
  );
}
