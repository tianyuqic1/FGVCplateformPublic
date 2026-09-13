import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { listDatasetImports } from "../../api/datasets.js";
import { Panel, StatusChip } from "../../components/ui.jsx";
import { Icon } from "../../components/icons.jsx";
import "./dataset-import-jobs.css";

const labels = { queued: "排队中", running: "校验与入库中", succeeded: "导入完成", failed: "导入失败" };
const icons = { queued: "Clock", running: "LoaderCircle", succeeded: "CheckCircle2", failed: "AlertTriangle" };
const dateFormat = new Intl.DateTimeFormat("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false });
function submittedAt(value) {
  const date = new Date(value);
  return value && Number.isFinite(date.getTime()) ? dateFormat.format(date) : "";
}

export function DatasetImportJobs({ submittedJob, onCompleted }) {
  const [jobs, setJobs] = useState([]);
  const [error, setError] = useState(null);
  const previous = useRef(new Map());
  useEffect(() => {
    let active = true;
    let timer;
    if (submittedJob) {
      previous.current.set(submittedJob.id, submittedJob.status);
      setJobs((current) => [submittedJob, ...current.filter((job) => job.id !== submittedJob.id)]);
    }
    async function poll() {
      try {
        const next = await listDatasetImports();
        if (!active) return;
        const completed = next.some((job) => job.status === "succeeded" && ["queued", "running"].includes(previous.current.get(job.id)));
        previous.current = new Map(next.map((job) => [job.id, job.status]));
        setJobs(next);
        setError(null);
        if (completed) onCompleted();
      } catch (error) {
        if (active) setError(error);
      } finally {
        // Schedule after completion, never overlapping slow status requests.
        if (active) timer = setTimeout(poll, 2000);
      }
    }
    poll();
    return () => { active = false; clearTimeout(timer); };
  }, [submittedJob, onCompleted]);

  if (!jobs.length && !error) return null;
  return (
    <Panel className="dataset-import-panel" title="后台导入任务" caption="上传后自动校验并创建版本，离开页面也会继续处理。" action={<span className="import-job-count">{jobs.length} 个任务</span>}>
      {error && <div role="status" className="row-meta error-text">任务状态暂时无法更新，正在重试。{error.message}</div>}
      <div className="import-job-list" aria-label="后台导入任务列表" aria-live="polite">
        {jobs.map((job) => {
          const time = submittedAt(job.created_at);
          const imageCount = job.result?.upload?.image_count;
          const datasetId = job.result?.dataset?.dataset_id;
          return (
          <div className={`import-job-row import-job-row--${job.status}`} key={job.id}>
            <div className="import-job-icon" aria-hidden="true"><Icon name={icons[job.status] ?? "Database"} size={20} /></div>
            <div className="import-job-content">
              <strong className="import-job-name" title={job.name}>{job.name}</strong>
              <div className="import-job-meta">
                <span>{job.result?.version?.version_number ? `数据版本 v${job.result.version.version_number}` : "自动创建 v1"}</span>
                {Number.isFinite(imageCount) && <span>{imageCount.toLocaleString()} 张图片</span>}
                {time && <time dateTime={job.created_at}>{time} 提交</time>}
              </div>
              {job.status === "queued" && <div className="import-job-note">等待校验，后台按顺序处理。</div>}
              {job.status === "running" && <div className="import-job-note">正在校验图片并创建数据集版本。</div>}
              {job.status === "failed" && <div className="import-job-note error-text">{job.error} 可重新选择文件夹上传。</div>}
            </div>
            <div className="import-job-status"><StatusChip tone={job.status === "failed" ? "risk" : job.status === "succeeded" ? "default" : "info"}>{labels[job.status] ?? job.status}</StatusChip></div>
            <div className="import-job-action">{job.status === "succeeded" && datasetId && <Link className="ghost-button" to={`/datasets/${encodeURIComponent(datasetId)}`}>打开数据集<Icon name="ExternalLink" size={15} /></Link>}</div>
          </div>
        );})}
      </div>
    </Panel>
  );
}
