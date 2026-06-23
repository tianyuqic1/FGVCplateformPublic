import { useEffect, useRef, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { generateDatasetCard, updateDatasetCard, uploadImagefolder } from "../api/datasets.js";
import { runInference, runInferenceUpload, runInferenceUploadFolder } from "../api/inference.js";
import { deleteModelWeight } from "../api/modelWeights.js";
import { listReviewItems } from "../api/reviews.js";
import { cancelTrainingRun, createTrainingRun, deleteTrainingRun, pauseTrainingRun, resumeTrainingRun } from "../api/trainingRuns.js";
import { useDataset, useDatasetSamplePreviews, useDatasets } from "../hooks/useDatasets.js";
import { useAbstentionPolicies, useAbstentionShadowDecisions, useProposeAbstentionPolicy } from "../hooks/useAbstentionPolicies.js";
import { useRecentJobs } from "../hooks/useJobs.js";
import { useLLMAssistance, useReviewAssistance } from "../hooks/useLLMAssistance.js";
import { useModelWeights } from "../hooks/useModelWeights.js";
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
  { id: "import", title: "数据导入", description: "生成不可变 dataset version", icon: "FolderInput" },
  { id: "audit", title: "数据审计", description: "质量、类别、样本清单检查", icon: "BadgeCheck" },
  { id: "features", title: "特征提取", description: "生成 feature artifact", icon: "Cpu" },
  { id: "training", title: "分类头训练", description: "生成 candidate model", icon: "FlaskConical" },
  { id: "calibration", title: "校准弃权", description: "生成 calibration / threshold artifact", icon: "CircleGauge" },
  { id: "handoff", title: "候选评估", description: "汇总人工评估和风险材料", icon: "ClipboardCheck" },
];

const IMAGE_FOLDER_EXTENSIONS = new Set([".jpg", ".jpeg", ".png", ".bmp", ".webp"]);
const IMAGE_FOLDER_SPLITS = new Set(["train", "val", "test"]);

function datasetStatus(dataset) {
  if (dataset.status === "production") return { label: "生产可推理", tone: "default" };
  if (dataset.status === "ready") return { label: "可训练", tone: "default" };
  if (dataset.status === "calibrating") return { label: "待校准", tone: "warn" };
  return { label: "训练中", tone: "info" };
}

function datasetStatusLabel(status) {
  return datasetStatus({ status }).label;
}

function datasetFilterMatch(dataset, filter) {
  if (filter === "production") return dataset.status === "production";
  if (filter === "ready") return dataset.status === "ready";
  return true;
}

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
    new_accepts_old_abstains: "新策略放行",
    new_abstains_old_accepts: "新策略弃权",
    new_rejects_ood: "新策略 OOD",
    other_change: "其他变化",
  };
  return labels[value] ?? value;
}

function modelStateLabel(value) {
  const labels = {
    candidate: "候选",
    staging: "预发布",
    production: "生产",
    experiment: "实验",
    succeeded: "完成",
    running: "运行中",
    failed: "失败",
    queued: "排队中",
  };
  return labels[value] ?? value;
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

function uiStateLabel(status) {
  const labels = {
    idle: "空闲",
    loading: "加载中",
    running: "运行中",
    submitting: "提交中",
    succeeded: "已完成",
    failed: "失败",
    empty: "空",
    clear: "清空",
  };
  return labels[status] ?? status;
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

function pathWithSearch(path, entries = []) {
  const params = new URLSearchParams();
  entries.forEach(([key, value]) => {
    if (value !== undefined && value !== null && String(value).trim()) {
      params.set(key, String(value).trim());
    }
  });
  const query = params.toString();
  return query ? `${path}?${query}` : path;
}

function jobStatus(job) {
  if (job.status === "succeeded") return { label: "完成", tone: "default", icon: "Check" };
  if (job.status === "running") return { label: "运行中", tone: "warn", icon: "LoaderCircle" };
  if (job.status === "paused") return { label: "已暂停", tone: "neutral", icon: "Pause" };
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
  if (run.status === "paused") return { label: "已暂停", tone: "neutral", icon: "Pause" };
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
    return <div className="route-box"><div><strong>正在加载样本预览</strong><div className="row-meta">从当前 dataset version 读取真实样本图片。</div></div><StatusChip tone="info">加载中</StatusChip></div>;
  }
  if (error) {
    return <div className="route-box"><div><strong>样本预览加载失败</strong><div className="row-meta">{error.message}</div></div><StatusChip tone="risk">错误</StatusChip></div>;
  }
  if (!samples.length) {
    return <div className="route-box"><div><strong>暂无样本预览</strong><div className="row-meta">当前 dataset version 没有返回可预览图片。</div></div><StatusChip tone="warn">暂无样本</StatusChip></div>;
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
          <StatusChip tone="info">优先级 {item.priority}</StatusChip>
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

function RunRow({ run, onAction, busy = false, highlighted = false, legacy = false }) {
  const state = trainingStatus(run);
  const done = run.status === "succeeded" || run.status === "done";
  const canPause = ["queued", "running"].includes(run.status);
  const canResume = run.status === "paused";
  const canCancel = ["queued", "paused", "running"].includes(run.status);
  const canDelete = !["running", "succeeded"].includes(run.status) && !run.featureArtifactId && !run.modelVersionId;
  return (
    <div className={`timeline-item queue-row ${highlighted ? "recommended" : ""} ${legacy ? "legacy" : ""}`}>
      <Link className="queue-row-main" to={`/training/${run.id}`}>
        <div className="timeline-icon">
          <Icon name={state.icon} size={18} />
        </div>
        <div>
          <strong>{run.name}</strong>
          <div className="row-meta">
            {run.datasetName} · {extractorShortLabel(run.backboneId)} · {run.metric} · {featurePoolLabel(run.featurePool)}
          </div>
          <ProgressBar value={run.progress} fill={done ? "#0f766e" : run.status === "paused" ? "#6b7280" : "#a15c07"} shimmer={run.status === "running"} />
        </div>
      </Link>
      <div className="queue-row-actions">
        {canPause && <button className="icon-button" title="请求暂停任务" onClick={() => onAction("pause", run)} disabled={busy}><Icon name="Pause" size={16} /></button>}
        {canResume && <button className="icon-button" title="恢复排队任务" onClick={() => onAction("resume", run)} disabled={busy}><Icon name="Play" size={16} /></button>}
        {canCancel && <button className="icon-button" title="取消任务" onClick={() => onAction("cancel", run)} disabled={busy}><Icon name="Ban" size={16} /></button>}
        {canDelete && <button className="icon-button danger" title="删除队列记录" onClick={() => onAction("delete", run)} disabled={busy}><Icon name="Trash2" size={16} /></button>}
      </div>
      {highlighted && <StatusChip tone="default">推荐 CLS</StatusChip>}
      {legacy && <StatusChip tone="warn">旧特征</StatusChip>}
      <StatusChip tone={state.tone}>{state.label}</StatusChip>
    </div>
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
    <Panel title="真实任务状态" caption={`${sourceLabel} · queued / running / succeeded / failed / cancelled。`}>
      {selectedJobId && !selectedJob && (
        <div className="timeline-item">
          <div className="timeline-icon"><Icon name="Search" size={18} /></div>
          <div><strong>当前列表没有这个 job</strong><div className="row-meta">{selectedJobId} · 请刷新或扩大任务查询范围。</div></div>
          <StatusChip tone="warn">{compactStatusLabel("missing")}</StatusChip>
        </div>
      )}
      {jobs.length > 0 ? (
        <div className="timeline">{jobs.map((job) => <JobRow job={job} selected={job.id === selectedJobId} key={job.id} />)}</div>
      ) : (
        <div className="empty-state"><Icon name="ListChecks" size={24} /><strong>{loading ? "正在读取任务" : "没有可展示的真实 job"}</strong><span>{loading ? "任务列表加载完成后会显示真实进度。" : "不会用静态模板补假运行记录；启动训练或导入任务后再看这里。"}</span></div>
      )}
    </Panel>
  );
}

function CandidateOnlyGuard({ title = "实验候选，不是生产发布", description, action }) {
  return (
    <div className="candidate-guard">
      <div className="timeline-icon">
        <Icon name="ShieldAlert" size={18} />
      </div>
      <div>
        <strong>{title}</strong>
        <div className="row-meta">
          {description ?? "当前页面只展示 Training API 派生的候选版本；promote / rollback / production registry 尚未接入。"}
        </div>
      </div>
      {action ?? <StatusChip tone="warn">仅候选</StatusChip>}
    </div>
  );
}

function ModelCard({ model }) {
  const tone = model.state === "production" ? "default" : model.state === "candidate" || model.state === "staging" ? "info" : "warn";
  const accuracy = Number.isFinite(Number(model.accuracy)) ? Math.round(Number(model.accuracy)) : 0;
  const isRecommended = model.featurePool === "cls" && isDinoExtractor(model.backboneId);
  const isLegacy = isDinoExtractor(model.backboneId) && model.featurePool !== "cls";
  return (
    <Link className="card clickable" to={`/models/${model.id}`}>
      <div className="chips">
        <StatusChip tone={tone}>{modelStateLabel(model.state)}</StatusChip>
        {isRecommended && <StatusChip tone="default">CLS 基线</StatusChip>}
        {isLegacy && <StatusChip tone="warn">旧特征</StatusChip>}
      </div>
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
    backboneId: run.backboneId,
    featurePool: run.featurePool,
    imageSize: run.imageSize,
    featureBatchSize: run.featureBatchSize,
    headType: run.headConfig?.head_type ?? run.headConfig?.headType ?? null,
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
    <div className="pipeline-node template">
      <Icon name={node.icon} size={20} />
      <h3>{node.title}</h3>
      <p className="small">{node.description}</p>
      <StatusChip tone="neutral">模板节点</StatusChip>
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
  const [datasetFilter, setDatasetFilter] = useState("all");
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
  const visibleDatasets = datasetItems.filter((dataset) => datasetFilterMatch(dataset, datasetFilter));
  const canImport =
    importState.status !== "running" &&
    folderSelection.valid &&
    folderSelection.files.length > 0 &&
    importForm.datasetId.trim() &&
    importForm.datasetVersionId.trim();
  const importedDatasetId =
    importState.result?.dataset?.id ??
    importState.result?.dataset?.dataset_id ??
    importForm.datasetId.trim();
  const importedVersionId =
    importState.result?.version?.datasetVersionId ??
    importState.result?.version?.dataset_version_id ??
    importState.result?.version?.id ??
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
          action={<StatusChip tone={importState.status === "failed" ? "risk" : importState.status === "succeeded" ? "default" : "info"}>{uiStateLabel(importState.status)}</StatusChip>}
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
            <div className="next-step-card section-gap-small">
              <div>
                <div className="chips">
                  <StatusChip tone={importState.result.version.readiness?.ready ? "default" : "warn"}>
                    {importState.result.version.readiness?.ready ? "可训练" : "未就绪"}
                  </StatusChip>
                  <StatusChip tone="info">{importState.result.version.sample_count ?? 0} 张样本</StatusChip>
                  <StatusChip tone="info">{importState.result.version.class_count ?? 0} 类</StatusChip>
                  <StatusChip tone="info">{importedVersionId}</StatusChip>
                </div>
                <strong>导入成功，下一步选择数据资产或训练任务</strong>
                <div className="row-meta">
                  数据集已写入 Control-plane；训练会使用这个不可变 dataset version，不会直接读取本地文件夹。
                </div>
                {importState.result.upload?.stored_path && (
                  <div className="row-meta">stored_path: {importState.result.upload.stored_path}</div>
                )}
              </div>
              <div className="next-step-actions">
                <Link className="ghost-button" to={`/datasets/${encodeURIComponent(importedDatasetId)}`}>
                  <Icon name="Database" size={16} />
                  打开数据集
                </Link>
                <Link
                  className="primary-button"
                  to={pathWithSearch("/training", [
                    ["create", "1"],
                    ["dataset_version_id", importedVersionId],
                  ])}
                >
                  <Icon name="FlaskConical" size={16} />
                  创建训练
                </Link>
              </div>
            </div>
          )}
          {importState.error && <div className="row-meta section-gap-small">{importState.error.message}</div>}
        </Panel>
      )}
      <Panel
        title="数据集列表"
        caption={`${loading ? "正在连接 Control-plane API" : sourceLabel} · ${visibleDatasets.length}/${datasetItems.length} 个显示 · 点击行进入数据集详情。`}
        action={
          <div className="tabs">
            {[
              ["all", "全部"],
              ["production", "可推理"],
              ["ready", "可训练"],
            ].map(([value, label]) => (
              <button className={`tab-button ${datasetFilter === value ? "active" : ""}`} key={value} onClick={() => setDatasetFilter(value)}>
                {label}
              </button>
            ))}
          </div>
        }
      >
        <DatasetTable items={visibleDatasets} />
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
            <Link
              className="secondary-button"
              to={pathWithSearch("/training", [
                ["create", "1"],
                ["dataset_version_id", dataset.datasetVersionId],
              ])}
            >
              <Icon name="FlaskConical" size={16} />
              训练
            </Link>
            <Link className="primary-button" to={pathWithSearch("/inference", [["dataset_version_id", dataset.datasetVersionId]])}>
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

function formatClassName(item) {
  if (typeof item === "string") return item;
  if (item && typeof item === "object") {
    return item.name ?? item.label ?? item.id ?? item.class_name ?? "";
  }
  return "";
}

function DatasetTab({ dataset, tab, showToast }) {
  const previewLimit = tab === "samples" ? 8 : 3;
  const { samples: previewSamples, loading: previewLoading, error: previewError } = useDatasetSamplePreviews(dataset.datasetVersionId, previewLimit);
  const classNames = Array.isArray(dataset.classNames) ? dataset.classNames.map(formatClassName).filter(Boolean) : [];
  const visibleClassNames = classNames.slice(0, 10);
  const hiddenClassCount = Math.max(0, classNames.length - visibleClassNames.length);
  const hasThresholdStrategy = Boolean(dataset.thresholdStrategyId);
  const hasOodStressAsset = Boolean(dataset.oodStressAssetId);

  if (tab === "classes") {
    return (
      <div className="grid two section-gap">
        <Panel title="类别清单" caption="只展示当前 Dataset API 返回的类别；混淆对、长尾风险和争议池不在前端推断。">
          {visibleClassNames.length > 0 ? (
            <div className="timeline">
              {visibleClassNames.map((name) => (
                <ClassRow title={name} description={`${dataset.datasetVersionId ?? "dataset version unknown"} · 类别定义来自数据集元数据`} label="已接入" key={name} />
              ))}
              {hiddenClassCount > 0 && (
                <ClassRow title={`还有 ${hiddenClassCount} 个类别`} description="类别过多时只预览前 10 个；完整治理视图等待类别统计 API。" label="more" tone="neutral" />
              )}
            </div>
          ) : (
            <div className="empty-state"><Icon name="Tags" size={24} /><strong>类别清单未返回</strong><span>当前数据集只返回了 class count；不再用静态鸟类类别填充治理面板。</span></div>
          )}
        </Panel>
        <Panel title="类别治理状态" caption="这里保留治理上下文，但不展示可编辑的静态判别规则。">
          <div className="code-panel">dataset: {dataset.id ?? "n/a"}<br />dataset_version: {dataset.datasetVersionId ?? "n/a"}<br />class_count: {dataset.classes ?? "n/a"}<br />class_names: {classNames.length > 0 ? "api" : "not returned"}<br />confusion_pairs: not connected<br />taxonomy_notes: not connected</div>
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
          <div className="code-panel section-gap-small">feature_artifact: {dataset.featureArtifactId ?? "n/a"}<br />dataset_version: {dataset.datasetVersionId ?? "n/a"}<br />index: feature index API 待接入<br />backbone: selected training extractor<br />prototype_strategy: class_centroid + hard_negative_bank</div>
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
        <Panel title="弃权策略状态" caption="只展示已绑定产物；具体阈值来自校准报告，不在前端填默认值。">
          <div className="timeline">
            <GateRow title="阈值策略" description={dataset.thresholdStrategyId ?? "未绑定 threshold strategy artifact"} result={hasThresholdStrategy ? "pass" : "pending"} />
            <GateRow title="OOD 压力集" description={dataset.oodStressAssetId ?? "未绑定 OOD stress asset"} result={hasOodStressAsset ? "pass" : "pending"} />
            <GateRow title="人工复核回流" description="OOD 候选需要人工确认后才进入反馈池或压力集材料。" result="pending" />
          </div>
          <div className="code-panel section-gap-small">threshold_strategy: {dataset.thresholdStrategyId ?? "n/a"}<br />ood_stress_asset: {dataset.oodStressAssetId ?? "n/a"}<br />coverage: {hasThresholdStrategy ? `${dataset.coverage ?? 0}%` : "not available"}<br />editable_thresholds: disabled</div>
        </Panel>
        <Panel title="Coverage / Risk" caption="有校准产物时才展示覆盖率；缺失时不补静态风险曲线。">
          <CurveRow label="current coverage" value={hasThresholdStrategy ? `${dataset.coverage ?? 0}%` : "未绑定阈值策略"} percent={hasThresholdStrategy ? dataset.coverage ?? 0 : 0} fill="#0f766e" />
          <CurveRow label="selective risk" value="等待 calibration report" percent={0} fill="#315fbd" />
          <CurveRow label="OOD recall" value={hasOodStressAsset && dataset.oodRecall ? `${dataset.oodRecall}%` : "等待压力集评估"} percent={hasOodStressAsset ? dataset.oodRecall || 0 : 0} fill="#a15c07" />
        </Panel>
      </div>
    );
  }

  return (
    <>
      <DatasetCardPanel dataset={dataset} showToast={showToast} />
      <div className="grid metrics section-gap">
        <MetricCard title="样本质量" value={`${dataset.quality}%`} caption="坏图、错标、重复图综合" fill="#0f766e" percent={dataset.quality} icon="BadgeCheck" />
        <MetricCard title="自动覆盖率" value={hasThresholdStrategy ? `${dataset.coverage}%` : "--"} caption={hasThresholdStrategy ? "来自阈值策略产物" : "未绑定阈值策略"} fill="#315fbd" percent={hasThresholdStrategy ? dataset.coverage || 0 : 0} icon="Gauge" />
        <MetricCard title="OOD 拦截" value={hasOodStressAsset && dataset.oodRecall ? `${dataset.oodRecall}%` : "--"} caption={hasOodStressAsset ? "压力集评估" : "OOD 压力集未绑定"} fill="#26804f" percent={hasOodStressAsset ? dataset.oodRecall || 0 : 0} icon="ShieldAlert" />
        <MetricCard title="类别数量" value={dataset.classes} caption="可训练类别" fill="#6750a4" percent={Math.min(100, dataset.classes / 2)} icon="Tags" />
      </div>
      <div className="grid two section-gap">
        <Panel title="训练准备" caption="数据集能否进入训练流水线。">
          <div className="timeline">
            <GateRow title="类别体系" description={classNames.length > 0 ? `${classNames.length} 个类别名已返回` : `${dataset.classes ?? 0} 个类别；类别名未返回`} result={dataset.classes > 0 ? "pass" : "pending"} />
            <GateRow title="特征缓存" description={dataset.featureArtifactId ? `${dataset.featureArtifactId} 已完成` : "等待特征产物"} result={dataset.featureArtifactId ? "pass" : "pending"} />
            <GateRow title="阈值策略" description={dataset.thresholdStrategyId ?? "等待校准和阈值扫描产物"} result={hasThresholdStrategy ? "pass" : "pending"} />
          </div>
        </Panel>
        <Panel title="样本预览" caption="来自当前 dataset version 的真实图片。">
          <DatasetSamplePreviewGrid samples={previewSamples} loading={previewLoading} error={previewError} compact />
        </Panel>
      </div>
    </>
  );
}

function DatasetCardPanel({ dataset, showToast }) {
  const emptyCard = {
    task: "image_classification",
    domain: "general",
    summary: "",
    knownConfusions: [],
    oodPolicy: "",
    reviewGuidance: "",
  };
  const [card, setCard] = useState(dataset.datasetCard ?? emptyCard);
  const [status, setStatus] = useState("idle");
  const [error, setError] = useState(null);

  useEffect(() => {
    setCard(dataset.datasetCard ?? emptyCard);
    setStatus("idle");
    setError(null);
  }, [dataset.datasetVersionId]);

  function updateField(field, value) {
    setCard((current) => ({ ...current, [field]: value }));
  }

  async function handleSave() {
    setStatus("saving");
    setError(null);
    try {
      const saved = await updateDatasetCard(dataset.datasetVersionId, card);
      setCard(saved);
      setStatus("saved");
      showToast("数据集摘要已保存");
    } catch (saveError) {
      setError(saveError);
      setStatus("failed");
      showToast("数据集摘要保存失败");
    }
  }

  async function handleGenerate() {
    setStatus("generating");
    setError(null);
    try {
      const generated = await generateDatasetCard(dataset.datasetVersionId);
      setCard(generated);
      setStatus("generated");
      showToast("LLM 已按类别标签生成数据集摘要");
    } catch (generateError) {
      setError(generateError);
      setStatus("failed");
      showToast("LLM 生成数据集摘要失败");
    }
  }

  const busy = status === "saving" || status === "generating";

  return (
    <Panel
      title="数据集摘要"
      caption="导入时生成草稿；可让 LLM 读取类别标签后补充领域说明，推理解释和复核建议会优先使用这里的上下文。"
      action={<StatusChip tone={status === "failed" ? "risk" : status === "saved" || status === "generated" ? "default" : "info"}>{status === "saving" ? "保存中" : status === "generating" ? "生成中" : status === "saved" ? "已保存" : status === "generated" ? "已生成" : "可编辑"}</StatusChip>}
    >
      <div className="field-grid">
        <div className="field">
          <label>任务</label>
          <input value={card.task ?? ""} onChange={(event) => updateField("task", event.target.value)} />
        </div>
        <div className="field">
          <label>领域</label>
          <input value={card.domain ?? ""} onChange={(event) => updateField("domain", event.target.value)} />
        </div>
        <div className="field full-span">
          <label>摘要</label>
          <textarea value={card.summary ?? ""} onChange={(event) => updateField("summary", event.target.value)} rows={3} />
        </div>
        <div className="field full-span">
          <label>易混点</label>
          <input
            value={(card.knownConfusions ?? []).join("; ")}
            onChange={(event) => updateField("knownConfusions", event.target.value.split(";").map((item) => item.trim()).filter(Boolean))}
            placeholder="ship vs boat; deer vs horse"
          />
        </div>
        <div className="field full-span">
          <label>OOD 策略</label>
          <textarea value={card.oodPolicy ?? ""} onChange={(event) => updateField("oodPolicy", event.target.value)} rows={2} />
        </div>
        <div className="field full-span">
          <label>复核指引</label>
          <textarea value={card.reviewGuidance ?? ""} onChange={(event) => updateField("reviewGuidance", event.target.value)} rows={2} />
        </div>
      </div>
      <div className="chips section-gap-small">
        <StatusChip tone="info">{card.classCount ?? dataset.classes} 类</StatusChip>
        <StatusChip tone="info">{card.sampleCount ?? dataset.images} 张样本</StatusChip>
        {Object.entries(card.splitTotals ?? {}).map(([split, count]) => (
          <StatusChip tone="neutral" key={split}>{split}: {count}</StatusChip>
        ))}
      </div>
      {Array.isArray(card.classPreview) && card.classPreview.length > 0 && (
        <div className="row-meta section-gap-small">
          class preview: {card.classPreview.slice(0, 12).join(", ")}{card.classPreviewTruncated ? " ..." : ""}
        </div>
      )}
      {error && <div className="row-meta error-text section-gap-small">{error.message}</div>}
      <div className="toolbar section-gap-small">
        <button className="secondary-button" onClick={handleGenerate} disabled={busy}>
          <Icon name={status === "generating" ? "LoaderCircle" : "Wand2"} size={16} />
          LLM 生成摘要
        </button>
        <button className="primary-button" onClick={handleSave} disabled={busy}>
          <Icon name={status === "saving" ? "LoaderCircle" : "Save"} size={16} />
          保存摘要
        </button>
      </div>
    </Panel>
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

function isDinoExtractor(extractor) {
  return String(extractor).startsWith("dinov3_");
}

function formatBytes(bytes) {
  const value = Number(bytes);
  if (!Number.isFinite(value) || value <= 0) return "0 MB";
  if (value >= 1024 ** 3) return `${(value / 1024 ** 3).toFixed(1)} GB`;
  return `${(value / 1024 ** 2).toFixed(1)} MB`;
}

function modelWeightTone(state) {
  if (state === "cached") return "default";
  if (state === "partial") return "warn";
  return "neutral";
}

function modelWeightLabel(state) {
  if (state === "cached") return "已缓存";
  if (state === "partial") return "下载中/未完成";
  return "未缓存";
}

function extractorShortLabel(extractor) {
  if (extractor === "dinov3_vits") return "ViT-S";
  if (extractor === "dinov3_vitb") return "ViT-B";
  if (extractor === "dinov3_vitl") return "ViT-L";
  return extractor;
}

function featurePoolLabel(featurePool) {
  if (featurePool === "cls") return "CLS token";
  if (featurePool === "model") return "legacy model output";
  return "legacy/unknown";
}

function metricNumber(run, key) {
  const value = Number(run?.metrics?.[key]);
  return Number.isFinite(value) ? value : null;
}

function metricPercent(run, key) {
  const value = metricNumber(run, key);
  return value === null ? null : value * 100;
}

function formatRunMetricPercent(run, key, digits = 1) {
  const value = metricPercent(run, key);
  return value === null ? "n/a" : `${value.toFixed(digits)}%`;
}

function isSucceededModelRun(run) {
  return run?.status === "succeeded" && Boolean(run.modelVersionId);
}

function isDinoTrainingRun(run) {
  return isDinoExtractor(run?.backboneId);
}

function isRecommendedClsRun(run) {
  return isSucceededModelRun(run) && isDinoTrainingRun(run) && run.featurePool === "cls";
}

function isLegacyFeatureRun(run) {
  return isSucceededModelRun(run) && isDinoTrainingRun(run) && run.featurePool !== "cls";
}

function trainingRunRankValue(run) {
  return metricNumber(run, "accuracy") ?? metricNumber(run, "macro_f1") ?? -1;
}

function sortTrainingRunsForSelection(runs) {
  return [...runs].sort((a, b) => {
    const clsDelta = Number(isRecommendedClsRun(b)) - Number(isRecommendedClsRun(a));
    if (clsDelta !== 0) return clsDelta;
    const legacyDelta = Number(isLegacyFeatureRun(a)) - Number(isLegacyFeatureRun(b));
    if (legacyDelta !== 0) return legacyDelta;
    const rankDelta = trainingRunRankValue(b) - trainingRunRankValue(a);
    if (rankDelta !== 0) return rankDelta;
    return runTimeValue(b) - runTimeValue(a);
  });
}

function selectRecommendedClsRun(runs, datasetVersionId = "") {
  const scopedRuns = datasetVersionId
    ? runs.filter((run) => run.datasetVersionId === datasetVersionId)
    : runs;
  return sortTrainingRunsForSelection(scopedRuns).find(isRecommendedClsRun) ?? null;
}

function weightUsageLabel(extractor) {
  if (extractor === "dinov3_vits") return "当前 MVP 推荐默认，用于快速 CLS 特征训练和日常验证。";
  if (extractor === "dinov3_vitb") return "中等规模对照模型，适合后续做速度/精度折中评估。";
  if (extractor === "dinov3_vitl") return "重型精度优先模型，适合 GPU 资源充足时跑更强基线。";
  return "仅支持 FineVision 已登记的 DINOv3 权重。";
}

function filterTrainingRuns(runs, statusFilter, sortMode) {
  const filtered = runs.filter((run) => {
    if (statusFilter === "all") return true;
    if (statusFilter === "failed") return run.status === "failed";
    if (statusFilter === "active") return ["queued", "running"].includes(run.status);
    if (statusFilter === "paused") return run.status === "paused";
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

export function WeightManagementPage({ showToast }) {
  const { weights, source, loading, error, refresh } = useModelWeights();
  const [deleteState, setDeleteState] = useState({ status: "idle", preset: null, error: null });
  const totalCachedBytes = weights.reduce((sum, weight) => sum + (weight.state === "cached" ? weight.cacheBytes : 0), 0);
  const cachedCount = weights.filter((weight) => weight.state === "cached").length;
  const partialCount = weights.filter((weight) => weight.state === "partial").length;
  const sourceLabel = loading ? "正在读取权重缓存" : source === "api" ? "Model weight API" : "权重 API 暂不可用";

  async function handleDelete(weight) {
    if (!weight?.extractor || deleteState.status === "running") return;
    const confirmed = window.confirm(`删除 ${extractorShortLabel(weight.extractor)} 的本地权重缓存？下一次训练会重新下载。`);
    if (!confirmed) return;
    setDeleteState({ status: "running", preset: weight.extractor, error: null });
    try {
      const result = await deleteModelWeight(weight.extractor);
      setDeleteState({ status: "succeeded", preset: null, error: null });
      await refresh();
      showToast(result.deleted ? `已删除权重：${extractorShortLabel(weight.extractor)}` : `没有可删除的权重：${extractorShortLabel(weight.extractor)}`);
    } catch (deleteError) {
      setDeleteState({ status: "failed", preset: weight.extractor, error: deleteError });
      showToast("权重删除失败");
    }
  }

  return (
    <>
      <PageHero
        title="权重管理"
        description="查看 DINOv3 ViT-S/B/L 的本地 Hugging Face 权重缓存；删除后不会影响已生成的 feature/model artifact，但下一次训练会重新下载。"
        actions={<button className="ghost-button" onClick={refresh} disabled={loading}><Icon name="RefreshCw" size={16} />刷新</button>}
      />
      <div className="grid metrics">
        <MetricCard title="已缓存" value={`${cachedCount}/3`} caption={sourceLabel} fill="#0f766e" percent={(cachedCount / 3) * 100} icon="HardDrive" />
        <MetricCard title="缓存体积" value={formatBytes(totalCachedBytes)} caption="complete blobs" fill="#315fbd" percent={cachedCount ? 100 : 0} icon="DatabaseZap" />
        <MetricCard title="未完成" value={`${partialCount}`} caption="partial downloads" fill="#a15c07" percent={(partialCount / 3) * 100} icon="LoaderCircle" />
        <MetricCard title="训练特征" value="CLS" caption="feature_pool 默认 cls" fill="#0f766e" percent={100} icon="Target" />
      </div>
      <div className="grid two section-gap">
        <Panel title="DINOv3 权重缓存" caption="只管理预训练 backbone 权重；分类头和训练报告仍在 artifact store。">
          {error && <div className="route-box"><strong>权重 API 不可用</strong><div className="row-meta">{error.message}</div></div>}
          <div className="timeline">
            {weights.map((weight) => {
              const state = weight.state ?? "missing";
              const busy = deleteState.status === "running" && deleteState.preset === weight.extractor;
              const sizeLabel = state === "cached" ? formatBytes(weight.cacheBytes) : state === "partial" ? `${formatBytes(weight.partialBytes)} partial` : "未下载";
              return (
                <div className="timeline-item" key={weight.extractor}>
                  <div className="timeline-icon"><Icon name="HardDrive" size={18} /></div>
                  <div>
                    <strong>{extractorShortLabel(weight.extractor)} · {weight.backboneId}</strong>
                    <div className="row-meta">{weight.modelName} · {sizeLabel} · {weight.completeFileCount} files</div>
                    <div className="row-meta">{weight.description || weightUsageLabel(weight.extractor)}</div>
                  </div>
                  <div className="queue-row-actions">
                    <StatusChip tone={modelWeightTone(state)}>{modelWeightLabel(state)}</StatusChip>
                    <button className="icon-button danger" title="删除本地权重缓存" onClick={() => handleDelete(weight)} disabled={busy || state === "missing"}>
                      <Icon name={busy ? "LoaderCircle" : "Trash2"} size={16} />
                    </button>
                  </div>
                </div>
              );
            })}
            {!loading && weights.length === 0 && (
              <div className="timeline-item">
                <div className="timeline-icon"><Icon name="AlertTriangle" size={18} /></div>
                <div><strong>没有权重记录</strong><div className="row-meta">请确认 API 服务已启动。</div></div>
                <StatusChip tone="warn">空</StatusChip>
              </div>
            )}
          </div>
          {deleteState.error && <div className="row-meta section-gap-small">{deleteState.error.message}</div>}
        </Panel>
        <Panel title="权重说明" caption="权重文件、特征缓存、分类头产物不要混淆。">
          <div className="timeline">
            <GateRow title="预训练权重" description="Hugging Face/timm 下载的 DINOv3 backbone 参数；这个页面管理的是它。" result="pass" />
            <GateRow title="特征缓存" description="某个 dataset version 经过 DINOv3 CLS 提取后的 features.npz；删除权重不会删除它。" result="pending" />
            <GateRow title="分类头产物" description="FineVision 训练出的 linear head、校准报告和阈值策略；不在本页删除。" result="pending" />
          </div>
          <div className="code-panel section-gap-small">
            feature_pool: cls<br />
            image_size_default: 448<br />
            managed_presets: dinov3_vits | dinov3_vitb | dinov3_vitl<br />
            delete_scope: local Hugging Face repo cache only<br />
            cache_recovery: next training downloads again
          </div>
          {weights[0]?.cacheDir && (
            <div className="code-panel section-gap-small">
              cache_root_hint: {weights[0].cacheDir.replace(/\/models--timm--.*/, "")}
            </div>
          )}
        </Panel>
      </div>
    </>
  );
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

function trainingStageStatus(stage) {
  if (stage.status === "completed") return { label: "完成", tone: "default", fill: "#0f766e", shimmer: false };
  if (stage.status === "running") return { label: "运行中", tone: "warn", fill: "#a15c07", shimmer: true };
  if (stage.status === "failed") return { label: "失败", tone: "risk", fill: "#b4233c", shimmer: false };
  return { label: "等待", tone: "neutral", fill: "#94a3b8", shimmer: false };
}

function TrainingStageProgress({ run }) {
  const stages = run.trainingProgress?.stages ?? [];
  if (stages.length === 0) {
    return (
      <Panel title="阶段进度" caption="该训练运行没有阶段进度数据；新任务会实时写入阶段状态。">
        <div className="timeline">
          <GateRow title="特征提取" description={run.featureArtifactId ?? "等待特征产物"} result={run.featureArtifactId ? "pass" : "pending"} />
          <GateRow title="分类头训练" description={run.modelArtifactId ?? trainingStatus(run).label} result={run.modelArtifactId ? "pass" : "pending"} />
        </div>
      </Panel>
    );
  }

  return (
    <Panel
      title="阶段进度"
      caption={`当前阶段：${run.trainingProgress?.currentStage ?? "n/a"} · ${run.trainingProgress?.updatedAt ?? "等待更新"}`}
    >
      <div className="stage-progress-list">
        {stages.map((stage) => {
          const state = trainingStageStatus(stage);
          return (
            <div className="stage-progress-row" key={stage.id}>
              <div className="toolbar spread">
                <div>
                  <strong>{stage.label}</strong>
                  <div className="row-meta">{stage.note ?? `${stage.percent}% · 权重 ${stage.weight ?? "-"}`}</div>
                </div>
                <StatusChip tone={state.tone}>{state.label}</StatusChip>
              </div>
              <ProgressBar value={stage.percent} fill={state.fill} shimmer={state.shimmer} />
            </div>
          );
        })}
      </div>
    </Panel>
  );
}

export function TrainingPage({ showToast }) {
  const [trainingSearchParams, setTrainingSearchParams] = useSearchParams();
  const requestedTrainingDatasetVersionId = trainingSearchParams.get("dataset_version_id") || "";
  const { trainingRuns: runItems, source, loading, refresh } = useTrainingRuns();
  const { datasets: datasetOptions, source: datasetSource, loading: datasetsLoading, refresh: refreshDatasets } = useDatasets();
  const { weights: modelWeights, source: weightSource, loading: weightsLoading, refresh: refreshWeights } = useModelWeights();
  const [showCreate, setShowCreate] = useState(false);
  const [queueCollapsed, setQueueCollapsed] = useState(false);
  const [queueStatusFilter, setQueueStatusFilter] = useState("all");
  const [queueSortMode, setQueueSortMode] = useState("recent");
  const [trainingForm, setTrainingForm] = useState({
    datasetVersionId: "",
    extractor: "dinov3_vits",
    featureBatchSize: "8",
    imageSize: "448",
    learningRate: "0.001",
    epochs: "100",
    headBatchSize: "256",
    weightDecay: "0.0001",
  });
  const [createState, setCreateState] = useState({ status: "idle", run: null, error: null });
  const [queueActionState, setQueueActionState] = useState({ status: "idle", runId: null, error: null });
  const sourceLabel = loading ? "正在连接 Training API" : source === "api" ? "Training API" : "Training API 暂不可用";
  const readyDatasetOptions = datasetOptions.filter((dataset) => dataset.datasetVersionId && dataset.status === "ready");
  const blockedDatasetCount = datasetOptions.filter((dataset) => dataset.datasetVersionId && dataset.status !== "ready").length;
  const trainingDatasetOptions = readyDatasetOptions;
  const datasetVersionOptions = trainingDatasetOptions.map((dataset) => dataset.datasetVersionId).filter(Boolean);
  const filteredRuns = filterTrainingRuns(runItems, queueStatusFilter, queueSortMode);
  const recommendedBaseline = selectRecommendedClsRun(runItems);
  const failedRunCount = runItems.filter((run) => run.status === "failed").length;
  const activeRunCount = runItems.filter((run) => ["queued", "running"].includes(run.status)).length;
  const pausedRunCount = runItems.filter((run) => run.status === "paused").length;
  const weightByExtractor = Object.fromEntries(modelWeights.map((weight) => [weight.extractor, weight]));
  const canUseDatasetForTraining = datasetSource === "api" && datasetVersionOptions.length > 0;
  const canCreate =
    canUseDatasetForTraining &&
    createState.status !== "running" &&
    trainingForm.datasetVersionId.trim() &&
    Number(trainingForm.learningRate) > 0 &&
    Number(trainingForm.epochs) > 0 &&
    Number(trainingForm.headBatchSize) > 0 &&
    Number(trainingForm.weightDecay) >= 0 &&
    (!isDinoExtractor(trainingForm.extractor) ||
      (Number(trainingForm.featureBatchSize) > 0 && Number(trainingForm.imageSize) >= 128 && Number(trainingForm.imageSize) % 16 === 0));
  const createBlockReason = canUseDatasetForTraining
    ? ""
    : datasetSource === "api"
      ? blockedDatasetCount > 0
        ? "当前没有 ready dataset version 可用于训练；列表中的非 ready 数据集不会出现在训练表单。"
        : "当前没有真实 dataset version 可用于训练。"
      : "Control-plane API 暂不可用，不能提交真实训练任务。";

  useEffect(() => {
    if (datasetSource !== "api" || datasetVersionOptions.length === 0) return;
    setTrainingForm((current) => {
      const requested = datasetVersionOptions.includes(requestedTrainingDatasetVersionId) ? requestedTrainingDatasetVersionId : "";
      const nextDatasetVersionId = requested || (datasetVersionOptions.includes(current.datasetVersionId) ? current.datasetVersionId : datasetVersionOptions[0]);
      if (current.datasetVersionId === nextDatasetVersionId) return current;
      return { ...current, datasetVersionId: nextDatasetVersionId };
    });
  }, [datasetSource, datasetVersionOptions.join("|"), requestedTrainingDatasetVersionId]);

  useEffect(() => {
    if (trainingSearchParams.get("create") === "1" || requestedTrainingDatasetVersionId) setShowCreate(true);
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
        feature_batch_size: isDinoExtractor(trainingForm.extractor) ? Number(trainingForm.featureBatchSize) : undefined,
        image_size: isDinoExtractor(trainingForm.extractor) ? Number(trainingForm.imageSize) : undefined,
        head_config: {
          head_type: "torch_linear_adam",
          learning_rate: Number(trainingForm.learningRate),
          epochs: Number(trainingForm.epochs),
          batch_size: Number(trainingForm.headBatchSize),
          weight_decay: Number(trainingForm.weightDecay),
        },
        feature_pool: isDinoExtractor(trainingForm.extractor) ? "cls" : undefined,
      });
      setCreateState({ status: "succeeded", run, error: null });
      refresh();
      showToast(`训练已创建：${run.id}`);
    } catch (error) {
      setCreateState({ status: "failed", run: null, error });
      showToast("训练创建失败");
    }
  }

  async function handleQueueAction(action, run) {
    if (!run?.id || queueActionState.status === "running") return;
    if (action === "delete" && !window.confirm(`删除训练队列记录 ${run.id}？该操作不会删除数据集文件。`)) return;
    setQueueActionState({ status: "running", runId: run.id, error: null });
    try {
      if (action === "pause") await pauseTrainingRun(run.id);
      if (action === "resume") await resumeTrainingRun(run.id);
      if (action === "cancel") await cancelTrainingRun(run.id);
      if (action === "delete") await deleteTrainingRun(run.id);
      setQueueActionState({ status: "succeeded", runId: null, error: null });
      refresh();
      const label = { pause: "已暂停", resume: "已恢复", cancel: "已取消", delete: "已删除" }[action] ?? "已更新";
      showToast(`${label}：${run.id}`);
    } catch (error) {
      setQueueActionState({ status: "failed", runId: run.id, error });
      showToast("训练队列操作失败");
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
          action={<StatusChip tone={createState.status === "failed" ? "risk" : createState.status === "succeeded" ? "default" : "info"}>{uiStateLabel(createState.status)}</StatusChip>}
        >
          <div className="field-grid">
            <div className="field">
              <label>数据集版本</label>
              <select value={trainingForm.datasetVersionId} onChange={(event) => updateTrainingField("datasetVersionId", event.target.value)} disabled={!canUseDatasetForTraining}>
                {trainingDatasetOptions.map((dataset) => (
                  <option value={dataset.datasetVersionId} key={dataset.datasetVersionId}>
                    {dataset.name} · {dataset.datasetVersionId} · {dataset.images} 张样本 · {datasetStatusLabel(dataset.status)}
                  </option>
                ))}
              </select>
            </div>
            <div className="field">
              <label>特征提取器</label>
              <select value={trainingForm.extractor} onChange={(event) => updateTrainingField("extractor", event.target.value)}>
                <optgroup label="DINOv3 真实训练">
                  <option value="dinov3_vits">DINOv3 ViT-S/16 · 更快</option>
                  <option value="dinov3_vitb">DINOv3 ViT-B/16 · 平衡</option>
                  <option value="dinov3_vitl">DINOv3 ViT-L/16 · 更慢更重</option>
                </optgroup>
                <optgroup label="开发诊断">
                  <option value="color_stats">color_stats · 开发 smoke，不用于真实模型</option>
                </optgroup>
              </select>
            </div>
            <div className="field">
              <label>特征 batch_size</label>
              <input
                type="number"
                min="1"
                max="128"
                step="1"
                value={trainingForm.featureBatchSize}
                onChange={(event) => updateTrainingField("featureBatchSize", event.target.value)}
                disabled={!isDinoExtractor(trainingForm.extractor)}
              />
            </div>
            <div className="field">
              <label>输入分辨率</label>
              <select
                value={trainingForm.imageSize}
                onChange={(event) => updateTrainingField("imageSize", event.target.value)}
                disabled={!isDinoExtractor(trainingForm.extractor)}
              >
                <option value="256">256 · timm 默认</option>
                <option value="384">384 · 更细</option>
                <option value="448">448 · CUB 推荐</option>
                <option value="512">512 · 更慢</option>
              </select>
            </div>
            <div className="field">
              <label>特征池化</label>
              <input value={isDinoExtractor(trainingForm.extractor) ? "CLS token" : "color stats"} disabled />
            </div>
            <div className="field">
              <label>learning_rate</label>
              <input type="number" min="0.000001" step="0.0001" value={trainingForm.learningRate} onChange={(event) => updateTrainingField("learningRate", event.target.value)} />
            </div>
            <div className="field">
              <label>epochs</label>
              <input type="number" min="1" max="1000" step="1" value={trainingForm.epochs} onChange={(event) => updateTrainingField("epochs", event.target.value)} />
            </div>
            <div className="field">
              <label>head batch_size</label>
              <input type="number" min="1" max="4096" step="1" value={trainingForm.headBatchSize} onChange={(event) => updateTrainingField("headBatchSize", event.target.value)} />
            </div>
            <div className="field">
              <label>weight_decay</label>
              <input type="number" min="0" step="0.0001" value={trainingForm.weightDecay} onChange={(event) => updateTrainingField("weightDecay", event.target.value)} />
            </div>
            <div className="field">
              <label>执行</label>
              <button className="primary-button" onClick={handleCreateTrainingRun} disabled={!canCreate}>
                <Icon name={createState.status === "running" ? "LoaderCircle" : "Play"} size={16} />
                {createState.status === "running" ? "创建中" : "创建训练"}
              </button>
            </div>
          </div>
          <p className="panel-caption section-gap-small">
            DINOv3 batch_size 控制特征提取；输入分辨率和 CLS token 特征池化会进入特征缓存 key。分类头使用 torch_linear_adam，head batch_size 控制 Adam 小批量训练。
          </p>
          <div className="weight-status-grid section-gap-small">
            {["dinov3_vits", "dinov3_vitb", "dinov3_vitl"].map((extractor) => {
              const weight = weightByExtractor[extractor];
              const state = weight?.state ?? (weightsLoading ? "loading" : "missing");
              const sizeLabel =
                state === "cached"
                  ? formatBytes(weight?.cacheBytes)
                  : state === "partial"
                    ? `${formatBytes(weight?.partialBytes)} partial`
                    : "首次训练会下载";
              return (
                <button
                  className={`weight-status-card ${trainingForm.extractor === extractor ? "selected" : ""}`}
                  key={extractor}
                  onClick={() => updateTrainingField("extractor", extractor)}
                  type="button"
                >
                  <div>
                    <strong>{extractorShortLabel(extractor)}</strong>
                    <span>{weight?.modelName ?? "等待权重状态"}</span>
                  </div>
                  <StatusChip tone={modelWeightTone(state)}>{state === "loading" ? "读取中" : modelWeightLabel(state)}</StatusChip>
                  <small>{sizeLabel}</small>
                </button>
              );
            })}
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
            <button className="ghost-button" onClick={refreshWeights} disabled={weightsLoading}>
              <Icon name="RefreshCw" size={16} />
              刷新权重状态
            </button>
            <StatusChip tone={datasetSource === "api" ? "default" : "warn"}>
              {datasetSource === "api" ? `${trainingDatasetOptions.length} 个 ready 版本可选` : "数据集 API 不可用"}
            </StatusChip>
            <StatusChip tone={weightSource === "api" ? "default" : "warn"}>
              {weightSource === "api" ? "权重缓存可见" : "权重缓存不可用"}
            </StatusChip>
          </div>
          {createBlockReason && (
            <div className="route-box section-gap-small">
              <div><strong>创建训练已暂停</strong><div className="row-meta">{createBlockReason}</div></div>
              <StatusChip tone="warn">需要 API</StatusChip>
            </div>
          )}
          {createState.run && (
            <div className="next-step-card section-gap-small">
              <div>
                <div className="chips">
                  <StatusChip tone="default">{createState.run.id}</StatusChip>
                  <StatusChip tone="info">{createState.run.datasetVersionId ?? trainingForm.datasetVersionId}</StatusChip>
                  {createState.run.modelVersionId ? <StatusChip tone="info">{createState.run.modelVersionId}</StatusChip> : <StatusChip tone="warn">等待模型产物</StatusChip>}
                </div>
                <strong>训练运行已创建，下一步跟踪产物或进入推理实验室</strong>
                <div className="row-meta">
                  详情页会展示 job、阶段进度、模型产物和缺失项；推理实验室会预填当前 dataset/model query，模型未生成时会提示先等待训练完成。
                </div>
              </div>
              <div className="next-step-actions">
                <Link className="ghost-button" to={`/training/${createState.run.id}`}>
                  <Icon name="ExternalLink" size={16} />
                  打开详情
                </Link>
                <Link
                  className="primary-button"
                  to={pathWithSearch("/inference", [
                    ["dataset_version_id", createState.run.datasetVersionId ?? trainingForm.datasetVersionId],
                    ["model_version_id", createState.run.modelVersionId],
                  ])}
                >
                  <Icon name="ImageUp" size={16} />
                  去推理
                </Link>
              </div>
            </div>
          )}
          {createState.error && <div className="row-meta section-gap-small">{createState.error.message}</div>}
        </Panel>
      )}
      <Panel
        title="当前推荐 CLS 基线"
        caption="只从已完成的 DINOv3 CLS token 训练运行中选择；legacy/model-output 运行不会作为默认推理模型。"
        action={
          recommendedBaseline ? (
            <Link className="ghost-button" to={`/training/${recommendedBaseline.id}`}>
              <Icon name="ExternalLink" size={16} />
              打开运行
            </Link>
          ) : (
            <StatusChip tone="warn">等待完成训练</StatusChip>
          )
        }
      >
        {recommendedBaseline ? (
          <div className="baseline-summary">
            <div>
              <strong>{recommendedBaseline.modelVersionId}</strong>
              <div className="row-meta">
                {recommendedBaseline.datasetVersionId} · {extractorShortLabel(recommendedBaseline.backboneId)} · {featurePoolLabel(recommendedBaseline.featurePool)} · image {recommendedBaseline.imageSize ?? "n/a"}
              </div>
            </div>
            <div className="baseline-metrics">
              <div><span>accuracy</span><strong>{formatRunMetricPercent(recommendedBaseline, "accuracy", 1)}</strong></div>
              <div><span>macro F1</span><strong>{formatRunMetricPercent(recommendedBaseline, "macro_f1", 1)}</strong></div>
              <div><span>coverage</span><strong>{formatRunMetricPercent(recommendedBaseline, "expected_coverage", 0)}</strong></div>
              <div><span>risk</span><strong>{formatRunMetricPercent(recommendedBaseline, "expected_selective_risk", 2)}</strong></div>
            </div>
          </div>
        ) : (
          <div className="empty-state">
            <Icon name="Target" size={24} />
            <strong>还没有可推荐的 CLS 基线</strong>
            <span>完成一次 DINOv3 CLS + torch_linear_adam 训练后，推理实验室会默认选择它。</span>
          </div>
        )}
      </Panel>
      <div className="grid two">
        <Panel
          title="训练队列"
          caption={`${sourceLabel} · ${filteredRuns.length}/${runItems.length} 条显示 · 失败 ${failedRunCount} · 活跃 ${activeRunCount} · 暂停 ${pausedRunCount}`}
          action={<button className="ghost-button" onClick={() => setQueueCollapsed((value) => !value)}><Icon name={queueCollapsed ? "ChevronRight" : "ListFilter"} size={16} />{queueCollapsed ? "展开" : "折叠"}</button>}
        >
          <div className="review-filter-bar">
            <div className="tabs">
              {[
                ["all", "全部"],
                ["failed", "失败"],
                ["active", "运行中"],
                ["paused", "暂停"],
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
          {queueActionState.error && <div className="row-meta section-gap-small">{queueActionState.error.message}</div>}
          {queueCollapsed ? (
            <div className="queue-collapsed">
              <StatusChip tone={failedRunCount ? "risk" : "default"}>{failedRunCount} 失败</StatusChip>
              <StatusChip tone={activeRunCount ? "warn" : "neutral"}>{activeRunCount} 活跃</StatusChip>
              <StatusChip tone={pausedRunCount ? "neutral" : "info"}>{pausedRunCount} 暂停</StatusChip>
              <StatusChip tone="info">{runItems.length} 总数</StatusChip>
            </div>
          ) : (
            <div className="timeline">
              {filteredRuns.length > 0 ? (
                filteredRuns.map((run) => (
                  <RunRow
                    run={run}
                    key={run.id}
                    onAction={handleQueueAction}
                    busy={queueActionState.status === "running" && queueActionState.runId === run.id}
                    highlighted={recommendedBaseline?.id === run.id}
                    legacy={isLegacyFeatureRun(run)}
                  />
                ))
            ) : (
              <div className="timeline-item">
                <div className="timeline-icon"><Icon name="Inbox" size={18} /></div>
                <div><strong>当前筛选下没有训练运行</strong><div className="row-meta">可以切回全部，或新建一条训练任务。</div></div>
                <StatusChip tone="info">空</StatusChip>
              </div>
              )}
            </div>
          )}
        </Panel>
        <Panel title="训练配置模板" caption="MVP 先支持 frozen backbone + 分类头。">
          <div className="code-panel">backbone: dinov3_vits | dinov3_vitb | dinov3_vitl<br />feature_pool: cls<br />image_size: 448<br />feature_batch_size: 8<br />feature_cache: true<br />head: torch_linear_adam<br />head_training: cross_entropy + Adam<br />calibration: temperature_scaling<br />abstention: top1_margin + embedding_distance<br />report: accuracy, macro_f1, coverage_risk</div>
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
            <StatusChip tone="risk">{compactStatusLabel("not found")}</StatusChip>
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
  const hasCandidateModel = run.status === "succeeded" && Boolean(run.modelVersionId);
  const inferencePath = pathWithSearch("/inference", [
    ["dataset_version_id", run.datasetVersionId],
    ["model_version_id", run.modelVersionId],
  ]);
  return (
    <>
      <PageHero title={run.name} description={`${run.datasetName} · ${sourceLabel} · ${featurePoolLabel(run.featurePool)} · 训练分类头、生成校准报告、准备候选模型版本。`} actions={<><Link className="ghost-button" to="/training"><Icon name="ArrowLeft" size={16} />返回</Link>{hasCandidateModel ? <Link className="primary-button" to={inferencePath}><Icon name="ImageUp" size={16} />去推理</Link> : <button className="primary-button" disabled><Icon name="ExternalLink" size={16} />产物 API 待接入</button>}</>} />
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
      {hasCandidateModel && (
        <div className="section-gap">
          <div className="next-step-card">
            <div>
              <div className="chips">
                <StatusChip tone="default">候选模型</StatusChip>
                <StatusChip tone="info">{run.modelVersionId}</StatusChip>
                <StatusChip tone="info">{run.datasetVersionId}</StatusChip>
              </div>
              <strong>训练已产出实验候选，下一步做推理验证或查看候选模型</strong>
              <div className="row-meta">这不是生产发布；仍需复核队列、反馈池、OOD 压力集和发布门禁通过。</div>
            </div>
            <div className="next-step-actions">
              <Link className="ghost-button" to={`/models/${encodeURIComponent(run.modelVersionId)}`}>
                <Icon name="Boxes" size={16} />
                打开模型
              </Link>
              <Link className="primary-button" to={inferencePath}>
                <Icon name="ImageUp" size={16} />
                去推理
              </Link>
            </div>
          </div>
        </div>
      )}
      <div className="section-gap">
        <TrainingStageProgress run={run} />
      </div>
      <div className="grid two section-gap">
        <Panel title="候选发布判断" caption="不要只看 accuracy。">
          <CurveRow label="accuracy" value={accuracy === null ? "待生成" : `${accuracy}%`} percent={accuracy ?? 0} fill="#0f766e" />
          <CurveRow label="coverage" value={coverage === null ? "待生成" : `${coverage}%`} percent={coverage ?? 0} fill="#315fbd" />
          <CurveRow label="review cost" value={reviewCost} percent={coverage ?? 0} fill="#a15c07" />
        </Panel>
        <Panel title="产物元数据" caption="原始字段便于和 API / artifact store 对账。">
          <div className="code-panel">run: {run.id}<br />job: {run.jobId ?? "n/a"}<br />dataset: {run.datasetVersionId ?? "n/a"}<br />backbone: {run.backboneId ?? "n/a"}<br />feature_pool: {run.featurePool ?? "legacy/model"}<br />image_size: {run.imageSize ?? "n/a"}<br />feature_batch_size: {run.featureBatchSize ?? "n/a"}<br />model_version: {run.modelVersionId ?? "n/a"}<br />feature: {run.featureArtifactId ?? "n/a"}<br />model_artifact: {run.modelArtifactId ?? "n/a"}<br />report: {run.reportArtifactId ?? "n/a"}<br />calibration: {run.calibrationArtifactId ?? "n/a"}<br />threshold_strategy: {run.thresholdStrategyArtifactId ?? "n/a"}<br />error: {run.error ?? "n/a"}</div>
        </Panel>
      </div>
    </>
  );
}

export function InferencePage({ showToast }) {
  const [inferenceSearchParams] = useSearchParams();
  const requestedInferenceDatasetVersionId = inferenceSearchParams.get("dataset_version_id") || "";
  const requestedInferenceModelVersionId = inferenceSearchParams.get("model_version_id") || "";
  const batchFolderInputRef = useRef(null);
  const { datasets: apiDatasets, source: datasetSource } = useDatasets();
  const { trainingRuns: inferenceTrainingRuns, source: trainingSource } = useTrainingRuns();
  const llm = useLLMAssistance();
  const datasetOptions = apiDatasets;
  const [form, setForm] = useState({
    datasetVersionId: requestedInferenceDatasetVersionId || datasetOptions[0]?.datasetVersionId || "",
    modelVersionId: requestedInferenceModelVersionId,
    imageFile: null,
    imageFiles: [],
    imageFolderName: "",
    imagePath: "",
    sampleId: inferenceSearchParams.get("sample_id") || "",
    topK: 3,
    evidenceK: 3,
  });
  const [state, setState] = useState({ status: "idle", result: null, error: null });
  const [previewUrl, setPreviewUrl] = useState("");
  const datasetVersionOptions = datasetOptions.map((dataset) => dataset.datasetVersionId).filter(Boolean);
  const selectedDatasetVersionId = form.datasetVersionId.trim();
  const selectedDataset = datasetOptions.find((dataset) => dataset.datasetVersionId === selectedDatasetVersionId) ?? null;
  const modelRunsForDataset = inferenceTrainingRuns.filter(
    (run) => run.modelVersionId && run.datasetVersionId === selectedDatasetVersionId && run.status === "succeeded",
  );
  const modelVersionOptions = sortTrainingRunsForSelection(modelRunsForDataset);
  const modelVersionIds = modelVersionOptions
    .map((run) => run.modelVersionId)
    .filter(Boolean);
  const selectedModelRun = modelVersionOptions.find((run) => run.modelVersionId === form.modelVersionId) ?? null;
  const canUseInferenceInputs = datasetSource === "api" && trainingSource === "api" && datasetVersionOptions.length > 0 && modelVersionIds.length > 0;
  const canRun =
    canUseInferenceInputs &&
    state.status !== "running" &&
    form.datasetVersionId.trim() &&
    form.modelVersionId.trim() &&
    (form.imageFiles.length > 0 || form.imageFile || form.imagePath.trim() || form.sampleId.trim());
  const inferenceBlockReason = canUseInferenceInputs
    ? ""
    : datasetSource !== "api"
      ? "Control-plane API 暂不可用，不能运行真实推理。"
      : trainingSource !== "api"
        ? "Training API 暂不可用，不能运行真实推理。"
        : modelVersionIds.length === 0
          ? "当前数据版本没有可用模型版本，请先用该 dataset version 完成训练。"
          : "当前没有真实 dataset version 可用于推理。";

  useEffect(() => {
    if (datasetSource !== "api" || datasetVersionOptions.length === 0) return;
    setForm((current) => {
      const next = { ...current };
      const requestedDatasetAvailable = datasetVersionOptions.includes(requestedInferenceDatasetVersionId);
      if (requestedDatasetAvailable) {
        next.datasetVersionId = requestedInferenceDatasetVersionId;
      } else if (!datasetVersionOptions.includes(next.datasetVersionId)) {
        next.datasetVersionId = datasetVersionOptions[0];
      }
      const requestedModelAvailable = modelVersionIds.includes(requestedInferenceModelVersionId);
      if (requestedModelAvailable) {
        next.modelVersionId = requestedInferenceModelVersionId;
      } else if (modelVersionIds.length > 0 && !modelVersionIds.includes(next.modelVersionId)) {
        next.modelVersionId = modelVersionIds[0];
      }
      if (modelVersionIds.length === 0) {
        next.modelVersionId = "";
      }
      return next;
    });
  }, [datasetSource, datasetVersionOptions.join("|"), selectedDatasetVersionId, modelVersionIds.join("|"), requestedInferenceDatasetVersionId, requestedInferenceModelVersionId]);

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
      imageFiles: [],
      imageFolderName: "",
      imagePath: file ? "" : current.imagePath,
      sampleId: file ? "" : current.sampleId,
    }));
  }

  function updateImageFolder(files) {
    const imageFiles = Array.from(files ?? []).filter((file) => IMAGE_FOLDER_EXTENSIONS.has(`.${file.name.split(".").pop()?.toLowerCase()}`));
    const folderName = imageFiles[0]?.webkitRelativePath?.split("/")?.[0] ?? "";
    setForm((current) => ({
      ...current,
      imageFile: null,
      imageFiles,
      imageFolderName: folderName,
      imagePath: "",
      sampleId: "",
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
      const result = form.imageFiles.length > 0
        ? await runInferenceUploadFolder({
            ...commonInput,
            images: form.imageFiles,
            route_all_to_review: true,
          })
        : form.imageFile
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
  const isBatchResult = Boolean(result?.batch);
  const isBatchFolderMode = form.imageFiles.length > 0;
  const decisionState = inferenceDecisionStatus(isBatchResult ? null : result?.decision);
  const decisionCopy = inferenceDecisionCopy(isBatchResult ? null : result?.decision);
  const queryLabel = isBatchFolderMode ? `${form.imageFolderName || "文件夹"} · ${form.imageFiles.length} 张图片` : form.imageFile?.name || form.sampleId || form.imagePath || "query image";

  async function handleGenerateInferenceExplanation() {
    if (!result) return;
    const imageDataUrl = form.imageFile ? await readFileAsDataUrl(form.imageFile) : null;
    await llm.generate({
      task: "inference_explanation",
      context: {
        inference_event_id: result.inferenceEventId,
        review_item_id: result.reviewItemId,
        dataset_id: result.datasetId,
        dataset_version_id: result.datasetVersionId,
        model_version_id: result.modelVersionId,
        image_input: {
          ...result.input,
          local_preview_name: queryLabel,
          has_browser_upload_preview: Boolean(previewUrl),
          image_pixels_attached: Boolean(imageDataUrl),
          ...(imageDataUrl ? { image_data_url: imageDataUrl } : {}),
        },
        decision: result.decision,
        top_k: result.topK,
        nearest_neighbors: result.nearestNeighbors.slice(0, 5),
      },
    });
  }

  return (
    <>
      <CandidateOnlyGuard description="推理实验室只验证训练产出的候选模型和阈值策略；accept 也只是记录 inference event，不代表模型已经上线。" />
      <div className="grid detail section-gap-small">
      <Panel title="输入样本" caption="绑定数据版本和模型版本后运行 scoped inference。" action={<StatusChip tone={state.status === "running" ? "info" : "neutral"}>{state.status === "running" ? "运行中" : "实验室"}</StatusChip>}>
        {isBatchFolderMode ? (
          <div className="empty-query-preview">
            <Icon name="FolderInput" size={22} />
            <strong>{form.imageFolderName || "批量图片文件夹"}</strong>
            <span>{form.imageFiles.length} 张图片将批量推理，并默认进入人工复核队列。</span>
          </div>
        ) : previewUrl ? (
          <div className="uploaded-preview">
            <img src={previewUrl} alt={queryLabel} />
            <span>{queryLabel}</span>
          </div>
        ) : (
          <div className="empty-query-preview">
            <Icon name="ImageUp" size={22} />
            <strong>等待 query 图片</strong>
            <span>上传本地图片，或填写当前数据版本里的样本 ID。</span>
          </div>
        )}
        <div className="field-grid section-gap-small">
          <div className="field">
            <label>数据版本</label>
            <select value={form.datasetVersionId} onChange={(event) => updateField("datasetVersionId", event.target.value)} disabled={datasetVersionOptions.length === 0}>
              <option value="">{datasetVersionOptions.length === 0 ? "暂无数据版本" : "选择数据版本"}</option>
              {datasetOptions.map((dataset) => (
                <option value={dataset.datasetVersionId} key={dataset.datasetVersionId}>
                  {dataset.name} · {dataset.datasetVersionId} · {dataset.images} samples · {dataset.status}
                </option>
              ))}
            </select>
            <span className="field-hint">
              {selectedDataset
                ? `${datasetVersionOptions.length} 个数据版本可选；当前 ${selectedDataset.classes} 类 / ${selectedDataset.images} 张。`
                : `${datasetVersionOptions.length} 个数据版本可选。`}
            </span>
          </div>
          <div className="field">
            <label>模型版本</label>
            <select value={form.modelVersionId} onChange={(event) => updateField("modelVersionId", event.target.value)} disabled={modelVersionIds.length === 0}>
              <option value="">{modelVersionIds.length === 0 ? "当前数据版本暂无模型" : "选择模型版本"}</option>
              {modelVersionOptions.map((run) => (
                <option value={run.modelVersionId} key={run.modelVersionId}>
                  {run.modelVersionId} · {isRecommendedClsRun(run) ? "推荐 CLS" : isLegacyFeatureRun(run) ? "legacy" : "候选"} · {extractorShortLabel(run.backboneId)} · {run.metric}
                </option>
              ))}
            </select>
            <span className="field-hint">
              {modelVersionIds.length} 个模型匹配当前数据版本；默认优先使用已完成的 DINOv3 CLS 基线。
            </span>
            {selectedModelRun && (
              <div className="chips">
                <StatusChip tone={isRecommendedClsRun(selectedModelRun) ? "default" : isLegacyFeatureRun(selectedModelRun) ? "warn" : "info"}>
                  {isRecommendedClsRun(selectedModelRun) ? "推荐 CLS 基线" : isLegacyFeatureRun(selectedModelRun) ? "legacy 模型" : "候选模型"}
                </StatusChip>
                <StatusChip tone={selectedModelRun.thresholdStrategyArtifactId ? "default" : "warn"}>
                  {selectedModelRun.thresholdStrategyArtifactId ? "已校准阈值" : "阈值待确认"}
                </StatusChip>
              </div>
            )}
          </div>
          <div className="field full-span">
            <label>上传图片</label>
            <label className="file-picker">
              <input type="file" accept="image/png,image/jpeg,image/webp,image/bmp" onChange={(event) => updateImageFile(event.target.files?.[0] ?? null)} />
              <Icon name="ImageUp" size={18} />
              <span>{form.imageFile?.name || "选择一张图片作为 query"}</span>
            </label>
          </div>
          <div className="field full-span">
            <label>批量上传文件夹</label>
            <div className="file-picker folder-picker">
              <input
                ref={batchFolderInputRef}
                type="file"
                multiple
                webkitdirectory=""
                directory=""
                onChange={(event) => updateImageFolder(event.target.files)}
              />
              <Icon name="FolderInput" size={18} />
              <span>{isBatchFolderMode ? `${form.imageFolderName || "文件夹"} · ${form.imageFiles.length} 张图片` : "选择图片文件夹批量推理"}</span>
              <button
                className="ghost-button"
                type="button"
                onClick={(event) => {
                  event.preventDefault();
                  batchFolderInputRef.current?.click();
                }}
              >
                选择
              </button>
            </div>
            <span className="field-hint">批量模式会逐张运行推理，并忽略单样本 ID 和容器内图片路径输入。</span>
          </div>
          <div className="field">
            <label>样本 ID</label>
            <input
              value={form.sampleId}
              onChange={(event) => updateField("sampleId", event.target.value)}
              disabled={Boolean(form.imageFile) || isBatchFolderMode}
              placeholder={isBatchFolderMode ? "批量文件夹模式下忽略" : "feature artifact sample_id"}
            />
            {isBatchFolderMode && <span className="field-hint">已选择文件夹，运行时不会发送 sample_id。</span>}
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
            <input
              value={form.imagePath}
              onChange={(event) => updateField("imagePath", event.target.value)}
              disabled={Boolean(form.imageFile) || isBatchFolderMode}
              placeholder={isBatchFolderMode ? "批量文件夹模式下忽略" : "/absolute/path/to/image.png"}
            />
            {isBatchFolderMode && <span className="field-hint">已选择文件夹，运行时不会发送 image_path。</span>}
          </div>
        </details>
        <div className="toolbar section-gap-small">
          <button className="primary-button" onClick={handleRun} disabled={!canRun}><Icon name={state.status === "running" ? "LoaderCircle" : "Play"} size={16} />{state.status === "running" ? "推理中" : "运行推理"}</button>
          <button className="ghost-button" onClick={() => updateImageFile(null)} disabled={!form.imageFile || state.status === "running"}><Icon name="RefreshCw" size={16} />清除图片</button>
          <button className="ghost-button" onClick={() => updateImageFolder([])} disabled={form.imageFiles.length === 0 || state.status === "running"}><Icon name="RefreshCw" size={16} />清除文件夹</button>
        </div>
        {inferenceBlockReason && (
          <div className="route-box section-gap-small">
            <div><strong>推理运行已暂停</strong><div className="row-meta">{inferenceBlockReason}</div></div>
            <StatusChip tone="warn">需要 API</StatusChip>
          </div>
        )}
      </Panel>
      <Panel title="推理结果" caption="结果会记录为 inference event；abstain / reject_ood 会路由到人工复核。" action={<StatusChip tone={decisionState.tone}>{decisionState.label}</StatusChip>}>
        {state.status === "idle" && (
            <div className="timeline-item">
              <div className="timeline-icon"><Icon name="ScanSearch" size={18} /></div>
            <div><strong>等待推理输入</strong><div className="row-meta">上传图片，或填写样本 ID 后运行推理。</div></div>
            <StatusChip tone="info">待输入</StatusChip>
          </div>
        )}
        {state.status === "running" && (
          <div className="timeline-item">
            <div className="timeline-icon"><Icon name="LoaderCircle" size={18} /></div>
            <div><strong>正在运行推理</strong><div className="row-meta">{form.datasetVersionId} · {form.modelVersionId}</div><ProgressBar value={72} fill="#315fbd" shimmer /></div>
            <StatusChip tone="info">运行中</StatusChip>
          </div>
        )}
        {state.status === "failed" && (
          <div className="timeline-item">
            <div className="timeline-icon"><Icon name="AlertTriangle" size={18} /></div>
            <div><strong>推理失败</strong><div className="row-meta">{state.error?.message ?? "Inference API 请求失败"}</div></div>
            <StatusChip tone="risk">失败</StatusChip>
          </div>
        )}
        {state.status === "succeeded" && isBatchResult && (
          <>
            <div className="decision-summary">
              <div className="decision-callout">
                <div className="timeline-icon"><Icon name="FolderInput" size={18} /></div>
                <div>
                  <strong>批量推理完成</strong>
                  <div className="row-meta">
                    {result.batch.succeeded}/{result.batch.total} 张完成，{result.batch.review_item_count} 条进入人工复核队列。
                  </div>
                </div>
                <StatusChip tone={result.batch.failed > 0 ? "warn" : "default"}>{result.batch.failed > 0 ? "部分失败" : "完成"}</StatusChip>
              </div>
              <div className="evidence-metrics">
                <div><span>total</span><strong>{result.batch.total}</strong></div>
                <div><span>succeeded</span><strong>{result.batch.succeeded}</strong></div>
                <div><span>review items</span><strong>{result.batch.review_item_count}</strong></div>
              </div>
              <div className="route-box">
                <div>
                  <strong>进入人工复核队列</strong>
                  <div className="row-meta">批量上传默认创建复核项；你可以打开队列后按当前数据集连续处理。</div>
                </div>
                <Link className="primary-button" to={`/review?status=pending&dataset_id=${encodeURIComponent(selectedDataset?.id ?? "")}`}>
                  <Icon name="UserCheck" size={16} />
                  打开复核队列
                </Link>
              </div>
            </div>
            <div className="timeline section-gap-small">
              {result.results.slice(0, 8).map((item, index) => (
                <div className="timeline-item" key={item.inferenceEventId || index}>
                  <div className="timeline-icon"><Icon name={item.reviewItemId ? "UserCheck" : "CheckCircle2"} size={18} /></div>
                  <div>
                    <strong>{item.input?.upload_filename ?? `image-${index + 1}`}</strong>
                    <div className="row-meta">
                      {item.decision.value} · {item.topK[0]?.label ?? "unknown"} {item.topK[0] ? item.topK[0].score.toFixed(3) : ""}
                    </div>
                  </div>
                  {item.reviewItemId ? <StatusChip tone="info">{item.reviewItemId}</StatusChip> : <StatusChip tone="default">{compactStatusLabel("event")}</StatusChip>}
                </div>
              ))}
              {result.results.length > 8 && (
                <div className="row-meta">还有 {result.results.length - 8} 条结果未展开；请到复核队列继续处理。</div>
              )}
              {result.failures.length > 0 && (
                <div className="route-box risk">
                  <div><strong>{result.failures.length} 张图片失败</strong><div className="row-meta">{result.failures.slice(0, 3).map((failure) => `${failure.filename}: ${failure.error}`).join("；")}</div></div>
                  <StatusChip tone="risk">{compactStatusLabel("failed")}</StatusChip>
                </div>
              )}
            </div>
          </>
        )}
        {state.status === "succeeded" && result && !isBatchResult && (
          <>
            <div className="decision-summary">
              <div className="decision-callout">
                <div className="timeline-icon"><Icon name={decisionState.icon} size={18} /></div>
                <div>
                  <strong>{decisionCopy.title}</strong>
                  <div className="row-meta">{decisionCopy.body}</div>
                </div>
                <StatusChip tone={decisionState.tone}>{decisionValueLabel(result.decision.value)}</StatusChip>
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
                  <StatusChip tone="default">已记录事件</StatusChip>
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
    </>
  );
}

function readFileAsDataUrl(file) {
  return new Promise((resolve) => {
    const reader = new FileReader();
    reader.addEventListener("load", () => resolve(typeof reader.result === "string" ? reader.result : null));
    reader.addEventListener("error", () => resolve(null));
    reader.readAsDataURL(file);
  });
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
              <StatusChip tone="risk">{compactStatusLabel("error")}</StatusChip>
            </div>
          )}
          {!error && apiReviewItems.length === 0 && (
            <div className="timeline-item">
              <div className="timeline-icon"><Icon name="CheckCircle2" size={18} /></div>
              <div><strong>{loading ? "正在加载队列" : "当前筛选下没有样本"}</strong><div className="row-meta">{loading ? "Review API 正在返回结果。" : statusFilter === "pending" ? "abstain / reject_ood 推理会自动进入这里。" : "可以切回待复核或全部查看其他记录。"}</div></div>
              <StatusChip tone={loading ? "info" : "default"}>{compactStatusLabel(loading ? "loading" : "clear")}</StatusChip>
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
            <div className="timeline-item"><div className="timeline-icon"><Icon name="Gauge" size={18} /></div><div><strong>{lowConfidenceCount} 条低置信</strong><div className="row-meta">置信度低于阈值，建议确认最终类别或标记不确定。</div></div><StatusChip tone="warn">低置信</StatusChip></div>
            <div className="timeline-item"><div className="timeline-icon"><Icon name="GitCompare" size={18} /></div><div><strong>{lowMarginCount} 条低间隔</strong><div className="row-meta">top-1 与 top-2 接近，优先检查易混类别。</div></div><StatusChip tone="info">低间隔</StatusChip></div>
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
        <Panel title="无法打开复核详情" caption="请从真实队列中选择一个待复核样本。"><StatusChip tone="risk">{compactStatusLabel("not found")}</StatusChip></Panel>
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
            <StatusChip tone="info">优先级 {item.priority}</StatusChip>
            <StatusChip tone={item.decision.value === "reject_ood" ? "risk" : "warn"}>{decisionValueLabel(item.decision.value)}</StatusChip>
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
              <>
                <div className="feedback-summary">
                  <div><span>最终结论</span><strong>{item.feedback.final_outcome}</strong></div>
                  <div><span>反馈池</span><strong>{item.feedback.destination}</strong></div>
                  <div><span>最终标签</span><strong>{item.feedback.final_label ?? "n/a"}</strong></div>
                  <div><span>备注</span><strong>{item.feedback.reviewer_note ?? "none"}</strong></div>
                </div>
                <div className="next-step-card">
                  <div>
                    <div className="chips">
                      <StatusChip tone="default">已进入反馈池</StatusChip>
                      <StatusChip tone={item.feedback.destination === "ood_stress" ? "risk" : item.feedback.destination === "training_candidate" ? "default" : "warn"}>
                        {feedbackDestinationLabel(item.feedback.destination)}
                      </StatusChip>
                    </div>
                    <strong>复核已完成，继续处理队列或检查反馈池</strong>
                    <div className="row-meta">
                      反馈池只是下一轮数据策展候选，不会自动写回训练集；继续下一张会回到当前筛选的待复核队列。
                    </div>
                  </div>
                  <div className="next-step-actions">
                    <Link className="primary-button" to={pathWithSearch("/review", [["status", "pending"], ["dataset_id", item.datasetId]])}>
                      <Icon name="UserCheck" size={16} />
                      继续下一张
                    </Link>
                    <Link className="ghost-button" to={pathWithSearch("/feedback", [["destination", item.feedback.destination], ["dataset_id", item.datasetId]])}>
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
    holisticAnalysis: raw.holisticAnalysis ?? raw.holistic_analysis ?? "",
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
          <StatusChip tone="risk">{compactStatusLabel("error")}</StatusChip>
        </div>
      )}
      {!error && !hasAssistance && (
        <div className="route-box section-gap-small">
          <div><strong>尚未生成辅助建议</strong><div className="row-meta">这不会影响人工复核、训练或反馈池操作。</div></div>
          <StatusChip tone="info">可选</StatusChip>
        </div>
      )}
      {hasAssistance && (
        <div className="section-gap-small">
          <div className="reason-box">
            <strong>建议摘要</strong>
            <span>{assistance.summary}</span>
          </div>
          {assistance.holisticAnalysis && (
            <div className="reason-box section-gap-small">
              <strong>LLM 综合分析</strong>
              <span>{assistance.holisticAnalysis}</span>
            </div>
          )}
          <div className="timeline section-gap-small">
            <LLMListItem icon="ScanSearch" title="检查点" items={assistance.inspectionNotes} empty="没有返回检查点。" />
            <LLMListItem icon="CheckCircle2" title="建议动作" items={assistance.suggestedActions} empty="没有返回建议动作。" />
            <LLMListItem icon="ShieldAlert" title="风险提示" items={assistance.riskFlags} empty="没有额外风险提示。" tone="warn" />
          </div>
          <div className="chips">
            <StatusChip tone="info">仅供参考</StatusChip>
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

function AbstentionPolicyPanel({ feedbackItems, showToast }) {
  const proposeState = useProposeAbstentionPolicy();
  const scopeOptions = Array.from(
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
  );
  const [selectedScope, setSelectedScope] = useState(scopeOptions[0]?.key ?? "");
  const [targetRisk, setTargetRisk] = useState("0.05");
  useEffect(() => {
    if (!selectedScope && scopeOptions[0]?.key) setSelectedScope(scopeOptions[0].key);
  }, [selectedScope, scopeOptions.map((item) => item.key).join("|")]);
  const activeScope = scopeOptions.find((item) => item.key === selectedScope) ?? scopeOptions[0] ?? null;
  const { policies, loading, error, refresh } = useAbstentionPolicies({
    datasetVersionId: activeScope?.datasetVersionId,
    modelVersionId: activeScope?.modelVersionId,
    status: "all",
    limit: 5,
  });
  const latestPolicy = policies[0] ?? null;
  const { shadowDecisions, loading: shadowLoading, error: shadowError, refresh: refreshShadow } = useAbstentionShadowDecisions(latestPolicy?.id, {
    diff: "all",
    limit: 12,
  });
  const diffCounts = latestPolicy?.metrics?.decision_diff_counts ?? {};
  const canPropose = Boolean(activeScope) && proposeState.status !== "submitting";

  async function handleProposePolicy() {
    if (!activeScope) return;
    try {
      await proposeState.propose({
        dataset_version_id: activeScope.datasetVersionId,
        model_version_id: activeScope.modelVersionId,
        target_selective_risk: Number(targetRisk),
        review_cost_per_item: 1.0,
        created_by: "local-operator",
      });
      refresh();
      refreshShadow();
      showToast?.("已生成影子弃权策略");
    } catch {
      showToast?.("生成弃权策略失败");
    }
  }

  return (
    <Panel title="弃权策略评估" caption="Phase 1 只做反馈池回放和影子评估，不改变真实推理阈值。" action={<StatusChip tone="info">shadow only</StatusChip>}>
      <div className="review-filter-bar">
        <label className="filter-select wide">
          <span>反馈范围</span>
          <select value={selectedScope} onChange={(event) => setSelectedScope(event.target.value)} disabled={scopeOptions.length === 0}>
            {scopeOptions.length === 0 ? (
              <option value="">等待反馈样本</option>
            ) : (
              scopeOptions.map((item) => (
                <option value={item.key} key={item.key}>{item.label}</option>
              ))
            )}
          </select>
        </label>
        <label className="filter-select compact">
          <span>目标风险</span>
          <input value={targetRisk} onChange={(event) => setTargetRisk(event.target.value)} inputMode="decimal" />
        </label>
        <button className="secondary-button" onClick={handleProposePolicy} disabled={!canPropose}>
          <Icon name={proposeState.status === "submitting" ? "LoaderCircle" : "Gauge"} size={16} />生成候选策略
        </button>
      </div>
      {proposeState.error && (
        <div className="timeline-item">
          <div className="timeline-icon"><Icon name="AlertTriangle" size={18} /></div>
          <div><strong>策略生成失败</strong><div className="row-meta">{proposeState.error.message}</div></div>
          <StatusChip tone="risk">错误</StatusChip>
        </div>
      )}
      {error && (
        <div className="timeline-item">
          <div className="timeline-icon"><Icon name="AlertTriangle" size={18} /></div>
          <div><strong>策略列表读取失败</strong><div className="row-meta">{error.message}</div></div>
          <StatusChip tone="risk">错误</StatusChip>
        </div>
      )}
      {!latestPolicy && !error && (
        <div className="timeline-item">
          <div className="timeline-icon"><Icon name={loading ? "LoaderCircle" : "ShieldCheck"} size={18} /></div>
          <div><strong>{loading ? "正在读取候选策略" : "还没有候选弃权策略"}</strong><div className="row-meta">先完成一批人工复核，再从反馈池生成 shadow policy。</div></div>
          <StatusChip tone={loading ? "info" : "warn"}>{loading ? "加载中" : "待生成"}</StatusChip>
        </div>
      )}
      {latestPolicy && (
        <>
          <div className="feedback-summary section-gap">
            <div><span>coverage</span><strong>{formatPolicyPercent(latestPolicy.estimatedCoverage)}</strong></div>
            <div><span>selective risk</span><strong>{formatPolicyPercent(latestPolicy.estimatedSelectiveRisk)}</strong></div>
            <div><span>feedback</span><strong>{latestPolicy.sourceFeedbackCount}</strong></div>
            <div><span>review cost</span><strong>{formatPolicyNumber(latestPolicy.estimatedReviewCost)}</strong></div>
          </div>
          <div className="timeline section-gap">
            <div className="timeline-item"><div className="timeline-icon"><Icon name="SlidersHorizontal" size={18} /></div><div><strong>阈值</strong><div className="row-meta">conf {formatPolicyNumber(latestPolicy.tauConf)} · margin {formatPolicyNumber(latestPolicy.tauMargin)} · ood {latestPolicy.tauOod == null ? "未启用" : formatPolicyNumber(latestPolicy.tauOod)}</div></div><StatusChip tone="info">{latestPolicy.status}</StatusChip></div>
            <div className="timeline-item"><div className="timeline-icon"><Icon name="ShieldAlert" size={18} /></div><div><strong>限制</strong><div className="row-meta">反馈池样本有选择偏差；该策略不会自动覆盖模型 artifact 或真实推理结果。</div></div><StatusChip tone="warn">{latestPolicy.metrics?.status_note ?? "shadow"}</StatusChip></div>
          </div>
          <div className="feedback-summary section-gap">
            {Object.entries(diffCounts).map(([key, count]) => (
              <div key={key}><span>{shadowDiffLabel(key)}</span><strong>{count}</strong></div>
            ))}
          </div>
          <div className="timeline section-gap">
            {shadowError && (
              <div className="timeline-item"><div className="timeline-icon"><Icon name="AlertTriangle" size={18} /></div><div><strong>影子决策读取失败</strong><div className="row-meta">{shadowError.message}</div></div><StatusChip tone="risk">错误</StatusChip></div>
            )}
            {!shadowError && shadowDecisions.length === 0 && (
              <div className="timeline-item"><div className="timeline-icon"><Icon name={shadowLoading ? "LoaderCircle" : "Route"} size={18} /></div><div><strong>{shadowLoading ? "正在读取影子决策" : "暂无影子决策"}</strong><div className="row-meta">生成策略时会回放已有反馈；后续推理也会追加 shadow row。</div></div><StatusChip tone="info">{shadowLoading ? "加载中" : "空"}</StatusChip></div>
            )}
            {shadowDecisions.map((item) => (
              <div className="timeline-item" key={item.id}>
                <div className="timeline-icon"><Icon name={item.decisionDiff === "same" ? "CheckCircle2" : "GitCompare"} size={18} /></div>
                <div>
                  <strong>{item.inferenceEventId}</strong>
                  <div className="row-meta">
                    {decisionValueLabel(item.currentDecision)} 到 {decisionValueLabel(item.shadowDecision)} · conf {formatPolicyNumber(item.scoreSnapshot.confidence)} · margin {formatPolicyNumber(item.scoreSnapshot.margin)}
                  </div>
                </div>
                <StatusChip tone={item.decisionDiff === "same" ? "default" : "warn"}>{shadowDiffLabel(item.decisionDiff)}</StatusChip>
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
              <StatusChip tone="risk">{compactStatusLabel("error")}</StatusChip>
            </div>
          )}
          {!error && feedbackItems.length === 0 && (
            <div className="timeline-item">
              <div className="timeline-icon"><Icon name="DatabaseZap" size={18} /></div>
              <div><strong>{loading ? "正在加载反馈池" : "当前筛选下没有反馈"}</strong><div className="row-meta">完成人工复核后，反馈会按目的地进入这里。</div></div>
              <StatusChip tone={loading ? "info" : "default"}>{compactStatusLabel(loading ? "loading" : "empty")}</StatusChip>
            </div>
          )}
          <div className="feedback-list">
            {feedbackItems.map((item) => <FeedbackCard item={item} key={item.id} />)}
          </div>
        </Panel>
        <AbstentionPolicyPanel feedbackItems={feedbackItems} showToast={showToast} />
        <Panel title="策展门禁" caption="MVP 只做候选池可见，不自动生成新数据集版本。">
          <div className="feedback-summary">
            {poolCounts.map(([value, label, count]) => (
              <div key={value}><span>{label}</span><strong>{count}</strong></div>
            ))}
          </div>
          <div className="timeline section-gap">
            <div className="timeline-item"><div className="timeline-icon"><Icon name="ShieldCheck" size={18} /></div><div><strong>不会直接污染训练集</strong><div className="row-meta">训练仍只能选择不可变 dataset_version。</div></div><StatusChip tone="default">{compactStatusLabel("guarded")}</StatusChip></div>
            <div className="timeline-item"><div className="timeline-icon"><Icon name="Database" size={18} /></div><div><strong>下一步：数据策展</strong><div className="row-meta">后续会把已采纳反馈冻结成新的 dataset version。</div></div><StatusChip tone="warn">{compactStatusLabel("deferred")}</StatusChip></div>
            <div className="timeline-item"><div className="timeline-icon"><Icon name="Route" size={18} /></div><div><strong>发布前再消费</strong><div className="row-meta">模型发布门禁应检查 OOD 压力池、坏图池和争议池处理状态。</div></div><StatusChip tone="info">{compactStatusLabel("gate")}</StatusChip></div>
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
  const apiModels = sortTrainingRunsForSelection(modelRuns.filter((run) => run.modelVersionId)).map(modelRecordFromRun);
  const calibratedModels = apiModels.filter((model) => model.calibrationArtifactId && model.thresholdStrategyId);
  const incompleteModels = apiModels.filter((model) => !model.modelArtifactId || !model.reportArtifactId || !model.calibrationArtifactId || !model.thresholdStrategyId);
  const sourceLabel = loading ? "正在连接 Training API" : source === "api" ? "Training API 派生候选模型" : "Training API 暂不可用";
  return (
    <>
      <CandidateOnlyGuard description="这里的模型来自已完成训练运行，只能作为实验候选查看和验证；生产发布仍等待 Model Registry promote / rollback API。" />
      <div className="grid metrics">
        <MetricCard title="候选版本" value={loading ? "..." : String(apiModels.length)} caption={sourceLabel} fill="#315fbd" percent={Math.min(100, apiModels.length * 28)} icon="GitCompare" to="/training" />
        <MetricCard title="可评估候选" value={String(calibratedModels.length)} caption="calibration + threshold strategy" fill="#26804f" percent={apiModels.length ? Math.round((calibratedModels.length / apiModels.length) * 100) : 0} icon="Thermometer" to="/training" />
        <MetricCard title="需补产物" value={String(incompleteModels.length)} caption="model / report / calibration / threshold" fill="#a15c07" percent={apiModels.length ? Math.round((incompleteModels.length / apiModels.length) * 100) : 0} icon="ClipboardList" to="/training" />
        <MetricCard title="Registry 状态" value="--" caption="本页不展示生产状态" fill="#6b7280" percent={0} icon="Boxes" to="/models" />
      </div>
      <div className="grid two section-gap">
        <Panel title="模型候选" caption={apiModels.length > 0 ? "来自已完成训练运行；不是生产注册表。" : "暂无真实候选模型；完成训练后会出现在这里。"}>
          {apiModels.length > 0 ? (
            <div className="grid three">{apiModels.map((model) => <ModelCard model={model} key={model.id} />)}</div>
          ) : (
            <div className="empty-state"><Icon name="Boxes" size={24} /><strong>没有可展示的真实模型版本</strong><span>这里不再显示本地 mock 模型。请先在训练页完成一次训练。</span></div>
          )}
        </Panel>
        <Panel title="候选评估清单" caption="这里只判断候选材料是否齐全，不暗示已经进入生产发布流程。"><div className="timeline"><GateRow title="离线评估报告" description={apiModels.length > 0 ? "读取 training report / metrics" : "等待真实候选模型"} result={apiModels.some((model) => model.reportArtifactId) ? "pass" : "pending"} /><GateRow title="校准和阈值策略" description={calibratedModels.length > 0 ? `${calibratedModels.length} 个候选已具备 calibration / threshold` : "等待校准产物"} result={calibratedModels.length > 0 ? "pass" : "pending"} /><GateRow title="复核反馈材料" description="人工复核和反馈池用于评估候选风险，不在本页伪造状态。" result="pending" /><GateRow title="注册表记录" description="未接入时保持为空；不显示生产假状态。" result="pending" /></div></Panel>
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
            <GateRow title="候选评估" description="补齐报告、校准和阈值策略后再进入评估列表。" result="pending" />
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
  const inferencePath = pathWithSearch("/inference", [
    ["dataset_version_id", model.datasetVersionId],
    ["model_version_id", model.id],
  ]);
  return (
    <>
      <PageHero title={model.id} description={`${sourceLabel} · 模型详情页聚焦候选权重、数据版本、阈值策略和评估报告。`} actions={<><Link className="ghost-button" to="/models"><Icon name="ArrowLeft" size={16} />返回</Link><Link className="secondary-button" to={inferencePath}><Icon name="ImageUp" size={16} />推理验证</Link><StatusChip tone="info">候选评估</StatusChip></>} />
      <CandidateOnlyGuard description="该版本仍是训练产出的实验候选；即使指标通过，也必须经过人工复核、反馈池策展、OOD 压力集和发布门禁后才能生产发布。" />
      <div className="grid two section-gap-small">
        <Panel title="版本元数据" caption="来自训练运行和 artifact metadata。"><div className="code-panel">model: {model.id}<br />run: {model.runId ?? "n/a"}<br />job: {model.jobId ?? "n/a"}<br />dataset: {model.datasetVersionId ?? "n/a"}<br />backbone: {model.backboneId ?? "n/a"}<br />feature_pool: {model.featurePool ?? "legacy/model"}<br />image_size: {model.imageSize ?? "n/a"}<br />feature_batch_size: {model.featureBatchSize ?? "n/a"}<br />head_type: {model.headType ?? "n/a"}<br />features: {model.featureArtifactId ?? "n/a"}<br />model_artifact: {model.modelArtifactId ?? "n/a"}<br />calibration: {model.calibrationArtifactId ?? "n/a"}<br />threshold: {model.thresholdStrategyId ?? "n/a"}<br />report: {model.reportArtifactId ?? "n/a"}<br />source: {model.source}</div></Panel>
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
      <PageHero title="查看真实 Control-plane jobs。" description="流水线页优先展示 /api/jobs 返回的任务状态；模板只作为流程说明，不代表正在运行。" actions={<StatusChip tone="info">优先真实任务</StatusChip>} />
      <div className="grid two section-gap">
        <RecentJobsPanel selectedJobId={selectedJobId} />
        <Panel title="Worker 边界" caption="MVP 阶段先查询任务状态，不在浏览器里直接触发模型计算。">
          <div className="code-panel">control-plane: /api/jobs<br />selected_job: {selectedJobId || "none"}<br />worker: feature extraction / train / calibration<br />storage: artifacts + metadata store<br />frontend: poll job status only</div>
        </Panel>
      </div>
      <Panel className="section-gap" title="流程模板说明" caption="只读参考模板；不展示运行进度、假日志或假节点状态。">
        <div className="pipeline">{pipelineNodes.map((node) => <PipelineNode node={node} key={node.id} />)}</div>
      </Panel>
    </>
  );
}

export function PipelineRunPage({ showToast }) {
  const { pipelineRunId = "" } = useParams();
  return (
    <>
      <PageHero title="流水线运行详情待接入" description={`${pipelineRunId || "unknown"} · 当前请在流水线页通过 job_id 查看真实 Control-plane job 状态。`} actions={<><Link className="ghost-button" to="/pipelines"><Icon name="ArrowLeft" size={16} />返回</Link><StatusChip tone="warn">运行详情 API 待接入</StatusChip></>} />
      <div className="grid two">
        <Panel title="运行节点" caption="不展示静态假日志；等待 Pipeline Run API 接入。"><div className="timeline"><GateRow title="解析运行 ID" description={pipelineRunId || "n/a"} result="pending" /><GateRow title="读取 job 状态" description="请使用 /pipelines?job_id=<job_id>" result="pending" /><GateRow title="读取产物链接" description="artifact store API 待接入" result="pending" /><GateRow title="失败重试" description="pipeline orchestration 待接入" result="pending" /></div></Panel>
        <Panel title="真实状态入口" caption="当前 MVP 已接入 /api/jobs 列表和查询。"><div className="code-panel">pipeline_run_id: {pipelineRunId || "n/a"}<br />source: not connected<br />job_status_route: /pipelines?job_id=&lt;job_id&gt;<br />static_demo_log: disabled</div></Panel>
      </div>
    </>
  );
}
