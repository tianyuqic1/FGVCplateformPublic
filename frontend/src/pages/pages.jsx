import { useEffect, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { datasets, modelVersions, pipelineNodes, reviewItems } from "../data/mockData.js";
import { importImagefolder } from "../api/datasets.js";
import { runInference, runInferenceUpload } from "../api/inference.js";
import { createTrainingRun } from "../api/trainingRuns.js";
import { useDataset, useDatasets } from "../hooks/useDatasets.js";
import { useRecentJobs } from "../hooks/useJobs.js";
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
  VisualCard,
  VisualPlaceholder,
} from "../components/ui.jsx";
import { PageHero } from "../components/AppShell.jsx";

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

function ReviewCard({ item }) {
  return (
    <Link className="sample-card clickable" to={`/review/${item.id}`}>
      <VisualPlaceholder type={item.visualType} label={item.id} low={item.route !== "ood"} />
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

function DatasetTable({ items = datasets }) {
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

function JobRow({ job }) {
  const state = jobStatus(job);
  return (
    <Link className="timeline-item clickable" to="/pipelines/pipe-014">
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

function RecentJobsPanel({ limit = 5 }) {
  const { jobs, source, loading } = useRecentJobs(limit);
  const sourceLabel = loading ? "正在连接 Control-plane API" : source === "api" ? "Control-plane API" : "本地预览任务";

  return (
    <Panel title="任务状态" caption={`${sourceLabel} · queued / running / succeeded / failed / cancelled。`}>
      <div className="timeline">{jobs.map((job) => <JobRow job={job} key={job.id} />)}</div>
    </Panel>
  );
}

function ModelCard({ model }) {
  const tone = model.state === "production" ? "default" : model.state === "staging" ? "info" : "warn";
  return (
    <Link className="card clickable" to={`/models/${model.id}`}>
      <StatusChip tone={tone}>{model.state}</StatusChip>
      <h3>{model.id}</h3>
      <p>{model.description}</p>
      <ProgressBar value={model.state === "production" ? 91 : model.state === "staging" ? 86 : 48} fill={model.state === "experiment" ? "#a15c07" : "#0f766e"} />
    </Link>
  );
}

function PipelineNode({ node }) {
  return (
    <Link className={`pipeline-node ${node.running ? "running" : ""}`} to="/pipelines/pipe-014">
      <Icon name={node.icon} size={20} />
      <h3>{node.title}</h3>
      <p className="small">{node.description}</p>
      <ProgressBar value={node.progress} fill={node.running ? "#a15c07" : "#0f766e"} />
      <div className="row-meta">{node.progress}%</div>
    </Link>
  );
}

export function DashboardPage({ showToast }) {
  const { datasets: datasetItems } = useDatasets();

  return (
    <>
      <PageHero
        title="先处理风险，再发布模型。"
        description="这里不再是展示页，而是每天打开后能行动的算法平台工作台：看待处理队列、训练状态、数据风险、模型发布门禁和 OOD 告警。"
        actions={
          <>
            <button className="ghost-button" disabled>
              <Icon name="RefreshCw" size={16} />
              生产刷新待接入
            </button>
            <Link className="primary-button" to="/review">
              <Icon name="UserCheck" size={16} />
              处理复核
            </Link>
          </>
        }
      />
      <div className="grid metrics">
        <MetricCard title="待复核样本" value="128" caption="高风险 17 · LLM 已读 63" fill="#a15c07" percent={48} icon="UserCheck" to="/review" />
        <MetricCard title="运行中训练" value="2" caption="1 个候选版本可灰度" fill="#315fbd" percent={72} icon="FlaskConical" to="/training" />
        <MetricCard title="生产覆盖率" value="82%" caption="阈值策略 selective-v4" fill="#0f766e" percent={82} icon="Gauge" to="/models" />
        <MetricCard title="OOD 告警" value="3" caption="工业零件数据集漂移" fill="#b4233c" percent={34} icon="ShieldAlert" to="/review" />
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
            <TaskItem icon="AlertTriangle" title="处理 17 条高风险复核样本" description="其中 5 条疑似 OOD，可能影响生产指标。" action="现在处理" to="/review" tone="risk" />
            <TaskItem icon="CircleGauge" title="确认候选模型阈值策略" description="候选模型准确率略升，但复核压力增加 4%。" action="打开训练" to="/training" tone="warn" />
            <TaskItem icon="DatabaseZap" title="工业零件数据集特征漂移" description="近 24 小时 embedding 分布偏离训练集。" action="看数据集" to="/datasets/defect" tone="info" />
          </div>
        </Panel>
        <Panel
          title="最近低置信样本"
          caption="点击进入审核详情。"
          action={
            <Link className="ghost-button" to="/review">
              <Icon name="ListFilter" size={16} />
              全部
            </Link>
          }
        >
          <div className="grid">{reviewItems.map((item) => <ReviewCard item={item} key={item.id} />)}</div>
        </Panel>
      </div>
      <div className="grid two section-gap">
        <Panel title="数据集状态" caption="当前系统支持多数据集持续接入。">
          <DatasetTable items={datasetItems} />
        </Panel>
        <Panel title="模型发布门禁" caption="上线前必须通过的检查。">
          <div className="grid">
            <GateRow title="离线评估" description="top-1 91.9%，macro F1 88.4%" />
            <GateRow title="OOD 压力集" description="拦截率 96.3%，误拒 6.2%" />
            <GateRow title="人工抽检" description="还剩 34 条长尾类样本" result="pending" />
            <GateRow title="回滚配置" description="已保留 bird-cls-v4" />
          </div>
        </Panel>
      </div>
    </>
  );
}

export function DatasetsPage({ showToast }) {
  const { datasets: datasetItems, source, loading, refresh } = useDatasets();
  const [showImport, setShowImport] = useState(false);
  const [importForm, setImportForm] = useState({
    path: "/app/data/test/cifar10-mini-imagefolder",
    datasetId: "cifar10-mini",
    datasetVersionId: "dataset@cifar10-mini-001",
  });
  const [importState, setImportState] = useState({ status: "idle", result: null, error: null });
  const sourceLabel = source === "api" ? "Control-plane API" : "本地预览数据";
  const canImport =
    importState.status !== "running" &&
    importForm.path.trim() &&
    importForm.datasetId.trim() &&
    importForm.datasetVersionId.trim();

  function updateImportField(field, value) {
    setImportForm((current) => ({ ...current, [field]: value }));
  }

  async function handleImportDataset() {
    if (!canImport) return;
    setImportState({ status: "running", result: null, error: null });
    try {
      const result = await importImagefolder({
        path: importForm.path.trim(),
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
          title="导入 ImageFolder"
          caption="Docker 环境默认挂载 ./data 到 /app/data，只能导入 API 容器可访问的路径。"
          action={<StatusChip tone={importState.status === "failed" ? "risk" : importState.status === "succeeded" ? "default" : "info"}>{importState.status}</StatusChip>}
        >
          <div className="field-grid">
            <div className="field">
              <label>路径</label>
              <input value={importForm.path} onChange={(event) => updateImportField("path", event.target.value)} placeholder="/app/data/..." />
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
                {importState.status === "running" ? "导入中" : "开始导入"}
              </button>
            </div>
          </div>
          {importState.result?.version && (
            <div className="chips section-gap-small">
              <StatusChip tone={importState.result.version.readiness?.ready ? "default" : "warn"}>
                {importState.result.version.readiness?.ready ? "ready" : "not ready"}
              </StatusChip>
              <StatusChip tone="info">{importState.result.version.sample_count ?? 0} samples</StatusChip>
              <StatusChip tone="info">{importState.result.version.class_count ?? 0} classes</StatusChip>
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
            <button className="tab-button">生产</button>
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

  return (
    <>
      <PageHero
        title={dataset.name}
        description={`${dataset.description} · ${loading ? "正在连接 Control-plane API" : source === "api" ? "来自 Control-plane API" : "本地预览数据"}`}
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
        <Panel title="类别定义" caption="给人工和 LLM 使用的判别说明。">
          <div className="field">
            <label>判别规则</label>
            <textarea defaultValue="关注喉部色块、胸侧颜色、尾羽形状；不要把背景或拍摄地点作为类别依据。" />
          </div>
          <div className="toolbar section-gap-small">
            <button className="primary-button" disabled>
              <Icon name="Save" size={16} />
              类别保存待接入
            </button>
            <button className="ghost-button">
              <Icon name="Wand2" size={16} />
              让 LLM 生成差异点
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
        <div className="image-grid">
          <VisualCard type="bird" label="黑喉石鵖" />
          <VisualCard type="bird" label="普通石鵖" />
          <VisualCard type="bird" label="低光照" low />
          <VisualCard type="ood" label="疑似 OOD" />
          <VisualCard type="defect" label="遮挡坏图" low />
        </div>
      </Panel>
    );
  }

  if (tab === "features") {
    return (
      <div className="grid two section-gap">
        <Panel title="特征索引" caption="DINOv3 embedding、类别原型和近邻索引。">
          <MetricCard title="特征向量" value={dataset.images.toLocaleString()} caption="embedding@014" fill="#0891b2" percent={100} icon="DatabaseZap" />
          <div className="code-panel section-gap-small">index: hnsw://bird/dataset@014<br />dimension: 1024<br />backbone: dinov3_vitl<br />prototype_strategy: class_centroid + hard_negative_bank</div>
        </Panel>
        <Panel title="最近邻检查" caption="用于解释预测和发现离群样本。">
          <div className="image-grid compact">
            <VisualCard type="bird" label="query" />
            <VisualCard type="bird" label="nn-1" />
            <VisualCard type="ood" label="far" />
          </div>
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
          <CurveRow label="coverage 92%" value="risk 8.6%" percent={92} fill="#b4233c" />
          <CurveRow label="coverage 82%" value="risk 4.1%" percent={82} fill="#0f766e" />
          <CurveRow label="coverage 70%" value="risk 2.8%" percent={70} fill="#315fbd" />
        </Panel>
      </div>
    );
  }

  return (
    <>
      <div className="grid metrics section-gap">
        <MetricCard title="样本质量" value={`${dataset.quality}%`} caption="坏图、错标、重复图综合" fill="#0f766e" percent={dataset.quality} icon="BadgeCheck" />
        <MetricCard title="自动覆盖率" value={`${dataset.coverage}%`} caption="当前生产阈值" fill="#315fbd" percent={dataset.coverage || 12} icon="Gauge" />
        <MetricCard title="OOD 拦截" value={dataset.oodRecall ? `${dataset.oodRecall}%` : "--"} caption="压力集表现" fill="#26804f" percent={dataset.oodRecall || 0} icon="ShieldAlert" />
        <MetricCard title="类别数量" value={dataset.classes} caption="可训练类别" fill="#6750a4" percent={Math.min(100, dataset.classes / 2)} icon="Tags" />
      </div>
      <div className="grid two section-gap">
        <Panel title="训练准备" caption="数据集能否进入训练流水线。">
          <div className="timeline">
            <GateRow title="类别体系" description="易混类别已标注，长尾类仍需补样" result="pending" />
            <GateRow title="特征缓存" description="embedding@014 已完成" />
            <GateRow title="验证集" description="val/test/stress 已划分" />
          </div>
        </Panel>
        <Panel title="样本预览" caption="真实实现应替换为图片和 mask 对比。">
          <div className="image-grid compact">
            <VisualCard type="bird" label="高置信" />
            <VisualCard type="bird" label="低置信" low />
            <VisualCard type="ood" label="OOD" />
          </div>
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

export function TrainingPage({ showToast }) {
  const { trainingRuns: runItems, source, loading, refresh } = useTrainingRuns();
  const { datasets: datasetOptions, source: datasetSource } = useDatasets();
  const [showCreate, setShowCreate] = useState(false);
  const [trainingForm, setTrainingForm] = useState({
    datasetVersionId: datasetOptions[0]?.datasetVersionId ?? "dataset@cifar10-mini-001",
    extractor: "color_stats",
    ridgeLambda: "0.01",
  });
  const [createState, setCreateState] = useState({ status: "idle", run: null, error: null });
  const sourceLabel = loading ? "正在连接 Training API" : source === "api" ? "Training API" : "本地预览训练";
  const datasetVersionOptions = datasetOptions.map((dataset) => dataset.datasetVersionId).filter(Boolean);
  const canCreate =
    createState.status !== "running" &&
    trainingForm.datasetVersionId.trim() &&
    Number(trainingForm.ridgeLambda) > 0;

  useEffect(() => {
    if (datasetSource !== "api" || datasetVersionOptions.length === 0) return;
    setTrainingForm((current) => {
      if (datasetVersionOptions.includes(current.datasetVersionId)) return current;
      return { ...current, datasetVersionId: datasetVersionOptions[0] };
    });
  }, [datasetSource, datasetVersionOptions.join("|")]);

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
      <PageHero title="冻结视觉基座，快速训练分类头。" description="训练页聚焦数据版本、backbone、分类头、阈值校准和报告产物，避免把实验结果变成不可追踪的文件。" actions={<button className="primary-button" onClick={() => setShowCreate((value) => !value)}><Icon name="Plus" size={16} />新建训练</button>} />
      {showCreate && (
        <Panel
          title="创建训练运行"
          caption="只允许 ready 的 dataset version 进入训练，训练任务会由 ml-worker 执行。"
          action={<StatusChip tone={createState.status === "failed" ? "risk" : createState.status === "succeeded" ? "default" : "info"}>{createState.status}</StatusChip>}
        >
          <div className="field-grid">
            <div className="field">
              <label>dataset_version_id</label>
              <input list="training-dataset-version-options" value={trainingForm.datasetVersionId} onChange={(event) => updateTrainingField("datasetVersionId", event.target.value)} placeholder="dataset@..." />
              <datalist id="training-dataset-version-options">
                {datasetOptions.map((dataset) => (
                  <option value={dataset.datasetVersionId} key={dataset.datasetVersionId}>{dataset.datasetVersionId}</option>
                ))}
              </datalist>
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
        <Panel title="训练队列" caption={`${sourceLabel} · 点击进入运行详情。`}>
          <div className="timeline">
            {runItems.length > 0 ? (
              runItems.map((run) => <RunRow run={run} key={run.id} />)
            ) : (
              <div className="timeline-item">
                <div className="timeline-icon"><Icon name="Inbox" size={18} /></div>
                <div><strong>暂无训练运行</strong><div className="row-meta">导入 ready 数据集后，通过 Training API 创建第一条训练。</div></div>
                <StatusChip tone="info">空队列</StatusChip>
              </div>
            )}
          </div>
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
  const accuracy = Number.isFinite(Number(metrics.accuracy)) ? Math.round(Number(metrics.accuracy) * 1000) / 10 : 91.9;
  const macroF1 = Number.isFinite(Number(metrics.macro_f1)) ? Math.round(Number(metrics.macro_f1) * 1000) / 10 : 88.4;
  const coverage = Number.isFinite(Number(metrics.expected_coverage)) ? Math.round(Number(metrics.expected_coverage) * 100) : 86;
  const reviewCost = Number.isFinite(Number(metrics.expected_selective_risk)) ? `${(Number(metrics.expected_selective_risk) * 100).toFixed(2)}% risk` : "+4%";
  const sourceLabel = loading ? "正在连接 Training API" : source === "api" ? "Training API" : "本地预览训练";
  return (
    <>
      <PageHero title={run.name} description={`${run.datasetName} · ${sourceLabel} · 训练分类头、生成校准报告、准备候选模型版本。`} actions={<><Link className="ghost-button" to="/training"><Icon name="ArrowLeft" size={16} />返回</Link><button className="primary-button" disabled><Icon name="ExternalLink" size={16} />产物 API 待接入</button></>} />
      <div className="grid metrics">
        <MetricCard title="进度" value={`${run.progress}%`} caption={trainingStatus(run).label} fill="#a15c07" percent={run.progress} icon="LoaderCircle" />
        <MetricCard title="Val Acc" value={`${accuracy}%`} caption={run.modelVersionId ?? "候选模型待生成"} fill="#0f766e" percent={accuracy} icon="Target" />
        <MetricCard title="Macro F1" value={`${macroF1}%`} caption={run.reportArtifactId ?? "报告待生成"} fill="#315fbd" percent={macroF1} icon="BarChart3" />
        <MetricCard title="复核压力" value={reviewCost} caption="selective risk / review cost" fill="#b4233c" percent={coverage} icon="UserCheck" />
      </div>
      <div className="grid two section-gap">
        <Panel title="运行步骤" caption="每一步都应有产物和失败恢复点。">
          <div className="timeline"><GateRow title="数据快照" description={`${run.datasetVersionId} locked`} /><GateRow title="特征缓存" description={run.featureArtifactId ?? "等待特征抽取"} result={run.featureArtifactId ? "pass" : "pending"} /><GateRow title="分类头训练" description={run.modelArtifactId ?? trainingStatus(run).label} result={run.modelArtifactId ? "pass" : "pending"} /><GateRow title="阈值扫描" description={run.thresholdStrategyArtifactId ?? "等待训练完成"} result={run.thresholdStrategyArtifactId ? "pass" : "pending"} /></div>
        </Panel>
        <Panel title="候选发布判断" caption="不要只看 accuracy。">
          <CurveRow label="accuracy" value={`${accuracy}%`} percent={accuracy} fill="#0f766e" />
          <CurveRow label="coverage" value={`${coverage}%`} percent={coverage} fill="#315fbd" />
          <CurveRow label="review cost" value={reviewCost} percent={54} fill="#a15c07" />
        </Panel>
      </div>
    </>
  );
}

export function InferencePage({ showToast }) {
  const { datasets: apiDatasets, source: datasetSource } = useDatasets();
  const { trainingRuns: inferenceTrainingRuns } = useTrainingRuns();
  const datasetOptions = apiDatasets.length > 0 ? apiDatasets : datasets;
  const [form, setForm] = useState({
    datasetVersionId: datasetOptions[0]?.datasetVersionId ?? "",
    modelVersionId: datasetOptions[0]?.productionModelVersionId ?? modelVersions[0]?.id ?? "",
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
  const canRun =
    state.status !== "running" &&
    form.datasetVersionId.trim() &&
    form.modelVersionId.trim() &&
    (form.imageFile || form.imagePath.trim() || form.sampleId.trim());

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
  const queryLabel = form.imageFile?.name || form.sampleId || form.imagePath || "query image";
  return (
    <div className="grid detail">
      <Panel title="输入样本" caption="绑定数据版本和模型版本后运行 scoped inference。" action={<StatusChip tone={state.status === "running" ? "info" : "neutral"}>{state.status === "running" ? "运行中" : "实验室"}</StatusChip>}>
        {previewUrl ? (
          <div className="uploaded-preview">
            <img src={previewUrl} alt={queryLabel} />
            <span>{queryLabel}</span>
          </div>
        ) : (
          <VisualPlaceholder type="bird" label={queryLabel} low />
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
          <button className="ghost-button" disabled><Icon name="ScissorsLineDashed" size={16} />SAM3 后续接入</button>
        </div>
      </Panel>
      <Panel title="推理结果" caption="模型结果、弃权判断、近邻解释。" action={<StatusChip tone={decisionState.tone}>{decisionState.label}</StatusChip>}>
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
            <div className="grid two">
              <div>
                {result.topK.length > 0 ? (
                  result.topK.map((candidate, index) => (
                    <CandidateBar label={candidate.label} score={candidate.score} fill={index === 0 ? "#0891b2" : index === 1 ? "#a15c07" : "#315fbd"} key={`${candidate.label}-${index}`} />
                  ))
                ) : (
                  <div className="row-meta">API 未返回候选类别。</div>
                )}
                <div className="chips section-gap-small">
                  <StatusChip tone={decisionState.tone}>confidence {result.decision.confidence.toFixed(2)}</StatusChip>
                  <StatusChip tone="info">margin {result.decision.margin.toFixed(2)}</StatusChip>
                  <StatusChip tone={result.decision.value === "accept" ? "default" : "warn"}>{decisionState.label}</StatusChip>
                </div>
              </div>
              <div className="code-panel">decision: {result.decision.value}<br />reason: {result.decision.reasons.join(", ") || "none"}<br />model: {result.modelVersionId}<br />strategy: {result.thresholdStrategyId}<br />ood_score: {result.decision.oodScore ?? "n/a"}</div>
            </div>
            <div className="panel-title embedded"><div><h2>近邻样本</h2><span>辅助解释，不作为真值。</span></div></div>
            {result.nearestNeighbors.length > 0 ? (
              <div className="timeline">
                {result.nearestNeighbors.map((neighbor) => (
                  <div className="timeline-item" key={neighbor.sampleId}>
                    <div className="timeline-icon"><Icon name="GitCompare" size={18} /></div>
                    <div><strong>{neighbor.sampleId}</strong><div className="row-meta">{neighbor.label} · distance {neighbor.distance?.toFixed(4) ?? "n/a"}</div></div>
                    <StatusChip tone="info">NN</StatusChip>
                  </div>
                ))}
              </div>
            ) : (
              <div className="row-meta">暂无近邻证据。</div>
            )}
          </>
        )}
      </Panel>
    </div>
  );
}

export function ReviewPage() {
  return (
    <>
      <PageHero title="让人工只处理模型真正不确定的样本。" description="复核页是这个系统的产品核心：模型弃权、LLM 预读、人工确认、标签回流都发生在这里。" actions={<div className="segmented"><button className="seg-button active">高风险</button><button className="seg-button">OOD</button><button className="seg-button">坏图</button><button className="seg-button">争议</button></div>} />
      <div className="grid review">
        <Panel title="队列" caption="点击样本进入复核详情。"><div className="grid">{reviewItems.map((item) => <ReviewCard item={item} key={item.id} />)}</div></Panel>
        <Panel title="批量处理建议" caption="适合审核主管查看。">
          <div className="timeline">
            <TaskItem icon="Ban" title="5 条疑似 OOD" description="建议统一标记后进入压力集。" action="查看" to="/review/sample-0820" tone="risk" />
            <TaskItem icon="ImageOff" title="12 条坏图" description="遮挡、低光照、主体不完整。" action="查看" to="/review/sample-0831" tone="warn" />
            <TaskItem icon="Tags" title="8 条类别争议" description="需要更新类别定义和 LLM 提示。" action="查看" to="/datasets/bird?tab=classes" tone="info" />
          </div>
        </Panel>
      </div>
    </>
  );
}

export function ReviewDetailPage({ showToast }) {
  const { reviewItemId = "sample-0817" } = useParams();
  const item = reviewItems.find((row) => row.id === reviewItemId) ?? reviewItems[0];
  return (
    <>
      <PageHero title={item.title} description={item.assistance} actions={<><Link className="ghost-button" to="/review"><Icon name="ArrowLeft" size={16} />返回队列</Link><button className="primary-button" disabled><Icon name="Check" size={16} />复核提交待接入</button></>} />
      <div className="grid detail">
        <Panel title="图像对比" caption="原图、主体裁剪、SAM3 mask 和近邻。">
          <VisualPlaceholder type={item.visualType} label="原图" low={item.route !== "ood"} />
          <div className="image-grid compact section-gap-small"><VisualCard type={item.visualType} label="SAM3 mask" /><VisualCard type="bird" label="近邻正例" /><VisualCard type="ood" label="hard negative" /></div>
        </Panel>
        <Panel title="审核表单" caption="人工标签是唯一可回流真值。" action={<StatusChip tone={riskTone(item.route)}>{item.risk}</StatusChip>}>
          <div className="grid">
            <div className="card"><h3>模型候选</h3><CandidateBar label={item.modelCandidate.label} score={item.modelCandidate.score} /><CandidateBar label={item.secondCandidate.label} score={item.secondCandidate.score} fill="#a15c07" /></div>
            <div className="card"><h3>LLM 建议</h3><p>{item.assistance}</p><StatusChip tone="info">辅助判断，不写入真值</StatusChip></div>
            <div className="field-grid"><div className="field"><label>最终标签</label><select><option>普通石鵖</option><option>黑喉石鵖</option><option>OOD</option><option>坏图</option></select></div><div className="field"><label>回流目标</label><select><option>训练候选池</option><option>压力集</option><option>坏图池</option><option>争议池</option></select></div></div>
            <div className="field"><label>审核备注</label><textarea defaultValue="喉部色块不明显，建议保留到争议池二审。" /></div>
          </div>
        </Panel>
      </div>
    </>
  );
}

export function ModelsPage() {
  return (
    <>
      <div className="grid metrics">
        <MetricCard title="线上版本" value="v4" caption="bird-cls-v4" fill="#0f766e" percent={88} icon="Rocket" to="/models/bird-cls-v4" />
        <MetricCard title="候选版本" value="v5" caption="等待人工抽检" fill="#315fbd" percent={64} icon="GitCompare" to="/models/bird-cls-v5" />
        <MetricCard title="校准误差" value="2.8%" caption="ECE after scaling" fill="#26804f" percent={72} icon="Thermometer" to="/models/bird-cls-v4" />
        <MetricCard title="LLM 成本" value="¥126" caption="今日复核辅助" fill="#a15c07" percent={44} icon="WalletCards" to="/models/bird-cls-v5" />
      </div>
      <div className="grid two section-gap">
        <Panel title="版本注册表" caption="点击版本查看发布门禁。"><div className="grid three">{modelVersions.map((model) => <ModelCard model={model} key={model.id} />)}</div></Panel>
        <Panel title="发布门禁" caption="生产系统不允许只凭 accuracy 上线。"><div className="timeline"><GateRow title="离线评估" description="top-1、macro F1、混淆矩阵" /><GateRow title="OOD 压力集" description="拦截率和误拒率" /><GateRow title="人工抽检" description="长尾、易混、低置信" result="pending" /><GateRow title="回滚策略" description="保留上一生产模型" /></div></Panel>
      </div>
    </>
  );
}

export function ModelDetailPage({ showToast }) {
  const { modelId = "bird-cls-v4" } = useParams();
  const model = modelVersions.find((item) => item.id === modelId) ?? modelVersions[0];
  return (
    <>
      <PageHero title={model.id} description="模型详情页把权重、数据版本、阈值策略、评估报告、发布门禁和回滚配置放在一起。" actions={<><Link className="ghost-button" to="/models"><Icon name="ArrowLeft" size={16} />返回</Link><button className="primary-button" disabled><Icon name="Rocket" size={16} />发布流程待接入</button></>} />
      <div className="grid two">
        <Panel title="版本元数据" caption="可追溯是算法平台的底线。"><div className="code-panel">model: {model.id}<br />dataset: bird/dataset@014<br />features: embedding@014<br />backbone: dinov3_vitl<br />head: linear<br />threshold: selective-v4<br />artifact: weights/{model.id}.safetensors</div></Panel>
        <Panel title="评估指标" caption="包含自动覆盖和弃权后的准确率。"><CurveRow label="top-1 accuracy" value={`${model.accuracy}%`} percent={Math.round(model.accuracy)} fill="#0f766e" /><CurveRow label="coverage" value={`${model.coverage}%`} percent={model.coverage} fill="#315fbd" /><CurveRow label="selective risk" value={`${model.selectiveRisk}%`} percent={41} fill="#a15c07" /></Panel>
      </div>
    </>
  );
}

export function PipelinesPage() {
  return (
    <>
      <PageHero title="把数据、训练、弃权和发布串成可重跑流程。" description="流水线视图面向工程实现：每个节点都有输入产物、输出产物、日志和失败恢复点。" actions={<button className="primary-button" disabled><Icon name="Play" size={16} />流水线执行待接入</button>} />
      <Panel title="模板：DINOv3 分类头训练" caption="点击节点查看运行样式。">
        <div className="pipeline">{pipelineNodes.map((node) => <PipelineNode node={node} key={node.id} />)}</div>
      </Panel>
      <div className="grid two section-gap">
        <RecentJobsPanel />
        <Panel title="Worker 边界" caption="MVP 阶段先查询任务状态，不在浏览器里直接触发模型计算。">
          <div className="code-panel">control-plane: /api/jobs<br />worker: feature extraction / train / calibration<br />storage: artifacts + metadata store<br />frontend: poll job status + fallback preview</div>
        </Panel>
      </div>
    </>
  );
}

export function PipelineRunPage({ showToast }) {
  return (
    <>
      <PageHero title="DINOv3 分类头训练运行中" description="当前卡在分类头训练和阈值扫描。正式实现里这里会显示日志、产物链接和失败重试。" actions={<><Link className="ghost-button" to="/pipelines"><Icon name="ArrowLeft" size={16} />返回</Link><button className="primary-button" disabled><Icon name="Pause" size={16} />暂停待接入</button></>} />
      <div className="grid two">
        <Panel title="运行节点" caption="正在持续更新。"><div className="timeline"><GateRow title="数据导入" description="dataset@014" /><GateRow title="特征提取" description="embedding@014" /><GateRow title="分类头训练" description="epoch 13 / 18" result="pending" /><GateRow title="阈值扫描" description="等待模型权重" result="pending" /></div></Panel>
        <Panel title="日志预览" caption="保留给后端任务系统。"><div className="code-panel">[12:41:03] load feature cache embedding@014<br />[12:41:18] start linear head training<br />[12:46:55] epoch 13 val_acc=0.919 macro_f1=0.884<br />[12:47:02] waiting for calibration sweep...</div></Panel>
      </div>
    </>
  );
}
