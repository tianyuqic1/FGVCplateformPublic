import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { listDatasetImports } from "../../api/datasets.js";
import { Panel, StatusChip } from "../../components/ui.jsx";

const labels = { queued: "排队中", running: "校验与入库中", succeeded: "导入完成", failed: "导入失败" };

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
    <Panel title="后台导入任务" caption="上传完成后可离开页面；后台继续校验并创建数据集版本，刷新页面可恢复查看。">
      {error && <div role="status" className="row-meta error-text">任务状态暂时无法更新，正在重试。{error.message}</div>}
      <div className="grid" aria-live="polite">
        {jobs.map((job) => (
          <div className="timeline-item" key={job.id}>
            <div>
              <strong>{job.name}</strong>
              <div className="row-meta">{job.result?.version?.version_number ? `v${job.result.version.version_number}` : "完成后自动创建 v1"}</div>
              {job.status === "queued" && <div className="row-meta">等待后台处理，当前同时处理 1 个数据集。</div>}
              {job.status === "failed" && <div className="row-meta error-text">{job.error} 可在上方重新选择文件夹上传。</div>}
            </div>
            <StatusChip tone={job.status === "failed" ? "risk" : job.status === "succeeded" ? "default" : "info"}>{labels[job.status] ?? job.status}</StatusChip>
            {job.status === "succeeded" && <Link className="ghost-button" to={`/datasets/${encodeURIComponent(job.result?.dataset?.dataset_id ?? "")}`}>打开数据集</Link>}
          </div>
        ))}
      </div>
    </Panel>
  );
}
