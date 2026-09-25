import { Link, useParams, useSearchParams } from "react-router-dom";
import { PageHero } from "../../components/AppShell.jsx";
import { Icon } from "../../components/icons.jsx";
import { GateRow, Panel, ProgressBar, StatusChip } from "../../components/ui.jsx";
import { useRecentJobs } from "../../hooks/useJobs.js";

const pipelineNodes = [
  { id: "import", title: "数据导入", description: "生成不可变 dataset version", icon: "FolderInput" },
  { id: "audit", title: "数据审计", description: "质量、类别、样本清单检查", icon: "BadgeCheck" },
  { id: "features", title: "特征提取", description: "生成 feature artifact", icon: "Cpu" },
  { id: "training", title: "分类头训练", description: "生成候选模型", icon: "FlaskConical" },
  { id: "calibration", title: "校准弃权", description: "生成 calibration / threshold artifact", icon: "CircleGauge" },
  { id: "handoff", title: "候选评估", description: "汇总人工评估和风险材料", icon: "ClipboardCheck" },
];

function jobStatus(job) {
  if (job.status === "succeeded") return { label: "完成", tone: "default", icon: "Check" };
  if (job.status === "running") return { label: "运行中", tone: "warn", icon: "LoaderCircle" };
  if (job.status === "paused") return { label: "已暂停", tone: "neutral", icon: "Pause" };
  if (job.status === "failed") return { label: "失败", tone: "risk", icon: "AlertTriangle" };
  if (job.status === "cancelled") return { label: "已取消", tone: "neutral", icon: "Ban" };
  return { label: "排队中", tone: "info", icon: "Clock" };
}

function JobRow({ job, selected = false }) {
  const state = jobStatus(job);
  const target = job.datasetVersionId ?? job.datasetId ?? "未绑定数据集";
  return (
    <Link
      className={`timeline-item clickable ${selected ? "selected" : ""}`}
      to={`/pipelines?job_id=${encodeURIComponent(job.id)}`}
    >
      <div className="timeline-icon">
        <Icon name={state.icon} size={18} />
      </div>
      <div>
        <strong>{job.jobType}</strong>
        <div className="row-meta">
          {target} · {job.message || job.id}
        </div>
        <ProgressBar
          value={job.progress}
          fill={
            job.status === "failed"
              ? "#b4233c"
              : job.status === "running"
                ? "#a15c07"
                : job.status === "cancelled"
                  ? "#6b7280"
                  : "#0f766e"
          }
          shimmer={job.status === "running"}
        />
      </div>
      <StatusChip tone={state.tone}>{state.label}</StatusChip>
    </Link>
  );
}

function RecentJobsPanel({ limit = 5, selectedJobId = "" }) {
  const { jobs, source, loading } = useRecentJobs(limit);
  const sourceLabel = loading ? "正在读取任务状态" : source === "api" ? "任务状态已同步" : "任务状态暂不可用";
  const selectedJob = selectedJobId ? jobs.find((job) => job.id === selectedJobId) : null;
  return (
    <Panel title="任务状态" caption={`${sourceLabel} · 覆盖排队、运行、完成、失败和取消。`}>
      {selectedJobId && !selectedJob && (
        <div className="timeline-item">
          <div className="timeline-icon">
            <Icon name="Search" size={18} />
          </div>
          <div>
            <strong>当前列表没有这个任务</strong>
            <div className="row-meta">{selectedJobId} · 请刷新或扩大任务查询范围。</div>
          </div>
          <StatusChip tone="warn">缺失</StatusChip>
        </div>
      )}
      {jobs.length > 0 ? (
        <div className="timeline">
          {jobs.map((job) => (
            <JobRow job={job} selected={job.id === selectedJobId} key={job.id} />
          ))}
        </div>
      ) : (
        <div className="empty-state">
          <Icon name="ListChecks" size={24} />
          <strong>{loading ? "正在读取任务" : "暂无任务记录"}</strong>
          <span>{loading ? "任务列表加载完成后会显示最新进度。" : "启动训练或导入任务后再看这里。"}</span>
        </div>
      )}
    </Panel>
  );
}

function TechnicalDetails({ summary = "技术详情", children }) {
  return (
    <details className="technical-details">
      <summary>{summary}</summary>
      <code>{children}</code>
    </details>
  );
}

export function PipelinesPage() {
  const [searchParams] = useSearchParams();
  const selectedJobId = searchParams.get("job_id") || "";
  return (
    <>
      <PageHero
        title="任务流水线"
        description="这里优先展示后台任务状态；流程模板只作为说明，不代表正在运行。"
        actions={<StatusChip tone="info">任务状态</StatusChip>}
      />
      <div className="grid two section-gap">
        <RecentJobsPanel selectedJobId={selectedJobId} />
        <Panel title="执行范围" caption="浏览器只查看任务状态，不直接触发模型计算。">
          <div className="timeline">
            <GateRow title="任务查询" description="展示导入、特征提取、训练和校准任务状态" result="pass" />
            <GateRow title="后台执行" description="计算任务由后端 worker 执行" result="pass" />
            <GateRow title="产物追踪" description="训练和评估页展示产物状态" result="pending" />
          </div>
          <TechnicalDetails>
            control_plane: /api/jobs
            <br />
            selected_job: {selectedJobId || "未选择"}
            <br />
            worker: feature extraction / train / calibration
            <br />
            storage: artifacts + metadata store
            <br />
            frontend: poll job status only
          </TechnicalDetails>
        </Panel>
      </div>
      <Panel className="section-gap" title="流程模板说明" caption="只读参考模板；不展示运行进度、假日志或假节点状态。">
        <div className="pipeline">
          {pipelineNodes.map((node) => (
            <div className="pipeline-node template" key={node.id}>
              <Icon name={node.icon} size={20} />
              <h3>{node.title}</h3>
              <p className="small">{node.description}</p>
              <StatusChip tone="neutral">模板节点</StatusChip>
            </div>
          ))}
        </div>
      </Panel>
    </>
  );
}

export function PipelineRunPage() {
  const { pipelineRunId = "" } = useParams();
  return (
    <>
      <PageHero
        title="流水线运行详情"
        description={`${pipelineRunId || "未选择运行"} · 请先在流水线页通过任务 ID 查看后台任务状态。`}
        actions={
          <>
            <Link className="ghost-button" to="/pipelines">
              <Icon name="ArrowLeft" size={16} />
              返回
            </Link>
            <StatusChip tone="warn">只读预览</StatusChip>
          </>
        }
      />
      <div className="grid two">
        <Panel title="运行节点" caption="当前不展示静态日志；请从任务状态页查看真实进度。">
          <div className="timeline">
            <GateRow title="解析运行 ID" description={pipelineRunId || "未选择"} result="pending" />
            <GateRow title="读取任务状态" description="请使用流水线页的任务 ID 查询" result="pending" />
            <GateRow title="读取产物链接" description="等待产物追踪完善" result="pending" />
            <GateRow title="失败重试" description="等待编排能力完善" result="pending" />
          </div>
        </Panel>
        <Panel title="状态入口" caption="任务列表支持查看后台任务状态。">
          <TechnicalDetails>
            pipeline_run_id: {pipelineRunId || "未选择"}
            <br />
            source: 待连接
            <br />
            job_status_route: /pipelines?job_id=&lt;job_id&gt;
            <br />
            static_demo_log: disabled
          </TechnicalDetails>
        </Panel>
      </div>
    </>
  );
}
