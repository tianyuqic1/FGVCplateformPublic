import { useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import {
  cancelTrainingRun,
  createTrainingRun,
  pauseTrainingRun,
  resumeTrainingRun,
} from "../../api/trainingRuns.js";
import { Icon } from "../../components/icons.jsx";
import { EChart } from "../../design-system/charts/EChart.jsx";
import {
  CodeValue,
  EmptyState,
  MetricTile,
  PageHeading,
  Panel,
  StatusBadge,
  formatPercent,
} from "../../design-system/components/Workbench.jsx";
import { useModelWeights } from "../../hooks/useModelWeights.js";
import { useTrainingRun, useTrainingRuns } from "../../hooks/useTrainingRuns.js";
import { accuracyChartOption, lossChartOption } from "./metricChartOptions.js";
import { useTrainingMetrics } from "./useTrainingMetrics.js";

const backbones = [
  {
    key: "dinov3_vits16_lvd1689m",
    name: "ViT-S/16",
    pretraining: "DINOv3 · LVD-1689M",
    feature: "384-dim · CLS pooling",
  },
  {
    key: "imagenet_vits16_augreg_in21k_ft_in1k",
    name: "ViT-S/16",
    pretraining: "ImageNet-21K → 1K · supervised",
    feature: "384-dim · model pooling",
  },
  {
    key: "imagenet_resnet50_a1_in1k",
    name: "ResNet-50",
    pretraining: "ImageNet-1K · supervised",
    feature: "2048-dim · global pooling",
  },
];

function statusCounts(runs) {
  return runs.reduce((counts, run) => ({ ...counts, [run.status]: (counts[run.status] ?? 0) + 1 }), {});
}

function runBackboneLabel(run) {
  return backbones.find((item) => item.key === run.backboneId)?.name ?? run.backboneId ?? "未记录骨干";
}

export function TrainingPage({ showToast }) {
  const navigate = useNavigate();
  const { trainingRuns, loading, error, refresh } = useTrainingRuns();
  const { weights } = useModelWeights();
  const [statusFilter, setStatusFilter] = useState("all");
  const [form, setForm] = useState({ datasetVersionId: "", backboneKey: backbones[0].key, headType: "torch_linear_adam" });
  const [submitting, setSubmitting] = useState(false);
  const counts = statusCounts(trainingRuns);
  const filtered = statusFilter === "all" ? trainingRuns : trainingRuns.filter((run) => run.status === statusFilter);
  const weightByKey = Object.fromEntries(weights.map((weight) => [weight.backboneKey, weight]));

  async function submit(event) {
    event.preventDefault();
    if (!form.datasetVersionId.trim() || submitting) return;
    setSubmitting(true);
    try {
      const run = await createTrainingRun({
        dataset_version_id: form.datasetVersionId.trim(),
        backbone_key: form.backboneKey,
        head_config: { head_type: form.headType, epochs: form.headType === "torch_linear_adam" ? 30 : undefined },
      });
      showToast?.("训练任务已进入队列");
      navigate(`/training/${encodeURIComponent(run.id)}`);
    } catch (submitError) {
      showToast?.(submitError.message);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="fv-feature-page">
      <PageHeading
        eyebrow="Experiments"
        title="训练实验"
        description="选择受治理的冻结骨干，派发训练任务并追踪每个 attempt 的状态与指标。"
        actions={<button className="secondary-button" type="button" onClick={refresh}><Icon name="RefreshCw" size={15} />刷新</button>}
      />
      <div className="fv-metric-grid">
        <MetricTile label="全部运行" value={trainingRuns.length} caption="当前可见范围" />
        <MetricTile label="运行中" value={counts.running ?? 0} caption="含当前 worker attempt" tone="running" />
        <MetricTile label="已完成" value={counts.succeeded ?? 0} caption="已生成 Model Version" tone="success" />
        <MetricTile label="失败 / 取消" value={(counts.failed ?? 0) + (counts.cancelled ?? 0)} caption="需检查诊断信息" tone="danger" />
      </div>

      <div className="fv-training-layout">
        <Panel eyebrow="New run" title="创建训练" className="fv-create-panel">
          <form className="fv-form" onSubmit={submit}>
            <label><span>Dataset Version</span><input required value={form.datasetVersionId} onChange={(event) => setForm({ ...form, datasetVersionId: event.target.value })} placeholder="dataset@bird-200-v12" /></label>
            <fieldset>
              <legend>Pretrained Backbone</legend>
              <div className="fv-backbone-picker">
                {backbones.map((item) => {
                  const managed = weightByKey[item.key];
                  return (
                    <label className={form.backboneKey === item.key ? "is-selected" : ""} key={item.key}>
                      <input type="radio" name="backbone" value={item.key} checked={form.backboneKey === item.key} onChange={() => setForm({ ...form, backboneKey: item.key })} />
                      <span><strong>{item.name}</strong><small>{item.pretraining}</small><em>{item.feature}</em></span>
                      <b className={managed?.sha256 ? "is-verified" : ""}>{managed?.sha256 ? "SHA 已登记" : "读取中"}</b>
                    </label>
                  );
                })}
              </div>
            </fieldset>
            <label><span>轻量分类头</span><select value={form.headType} onChange={(event) => setForm({ ...form, headType: event.target.value })}><option value="torch_linear_adam">Linear · AdamW（含曲线）</option><option value="ridge_linear">Ridge Linear（快速基线）</option></select></label>
            <button className="primary-button" type="submit" disabled={submitting || !form.datasetVersionId.trim()}><Icon name="Play" size={15} />{submitting ? "正在创建…" : "创建训练"}</button>
          </form>
        </Panel>

        <Panel
          eyebrow="Run queue"
          title="实验队列"
          aside={<select aria-label="状态筛选" value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)}><option value="all">全部状态</option><option value="running">运行中</option><option value="queued">等待中</option><option value="succeeded">已完成</option><option value="failed">失败</option></select>}
          className="fv-runs-panel"
        >
          {loading && <EmptyState icon="LoaderCircle" title="正在读取训练队列" description="连接 Go Control Plane…" />}
          {!loading && error && <EmptyState icon="TriangleAlert" title="训练服务不可用" description={error.message} />}
          {!loading && !error && filtered.length === 0 && <EmptyState title="暂无匹配运行" description="创建一次训练，或调整状态筛选。" />}
          {filtered.length > 0 && (
            <div className="fv-table-wrap"><table className="fv-table"><thead><tr><th>运行</th><th>Dataset Version</th><th>Backbone</th><th>状态</th><th>最新指标</th><th>创建时间</th></tr></thead><tbody>
              {filtered.map((run) => <tr key={run.id} tabIndex="0" onKeyDown={(event) => event.key === "Enter" && navigate(`/training/${run.id}`)}><td><Link to={`/training/${run.id}`}><strong>{run.name}</strong><CodeValue>{run.id}</CodeValue></Link></td><td><CodeValue>{run.datasetVersionId}</CodeValue></td><td>{runBackboneLabel(run)}</td><td><StatusBadge status={run.status} /></td><td>{run.metric}</td><td>{run.createdAt ? new Date(run.createdAt).toLocaleString("zh-CN") : "未记录"}</td></tr>)}
            </tbody></table></div>
          )}
        </Panel>
      </div>
    </div>
  );
}

function latest(points, name) {
  return [...points].reverse().find((point) => point.name === name);
}

export function TrainingDetailPage({ showToast }) {
  const { runId } = useParams();
  const { trainingRun: run, loading, error } = useTrainingRun(runId);
  const [attemptId, setAttemptId] = useState("");
  const metrics = useTrainingMetrics(runId, attemptId);
  const points = metrics.points;
  const latestLoss = latest(points, "train_loss");
  const latestAccuracy = latest(points, "eval_accuracy");
  const bestAccuracy = points.filter((point) => point.name === "eval_accuracy").reduce((best, point) => Math.max(best, point.value), Number.NEGATIVE_INFINITY);
  const currentEpoch = points.reduce((step, point) => Math.max(step, point.step), 0);
  const elapsed = run?.startedAt ? Math.max(0, Date.now() - new Date(run.startedAt).getTime()) : null;
  const elapsedLabel = elapsed === null ? "未开始" : `${Math.floor(elapsed / 3600000)}h ${Math.floor((elapsed % 3600000) / 60000)}m`;
  const lossOption = useMemo(() => lossChartOption(points), [points]);
  const accuracyOption = useMemo(() => accuracyChartOption(points), [points]);

  async function action(kind) {
    try {
      if (kind === "pause") await pauseTrainingRun(runId);
      if (kind === "resume") await resumeTrainingRun(runId);
      if (kind === "cancel") await cancelTrainingRun(runId);
      showToast?.(`训练已${kind === "pause" ? "暂停" : kind === "resume" ? "恢复" : "取消"}`);
    } catch (actionError) {
      showToast?.(actionError.message);
    }
  }

  if (loading) return <EmptyState icon="LoaderCircle" title="正在读取 Training Run" description={runId} />;
  if (error || !run) return <EmptyState icon="TriangleAlert" title="Training Run 不可用" description={error?.message ?? runId} />;

  return (
    <div className="fv-feature-page">
      <PageHeading
        eyebrow="Training run detail"
        title={run.name}
        description={<><CodeValue>{run.id}</CodeValue> · <CodeValue>{run.datasetVersionId}</CodeValue></>}
        actions={<><StatusBadge status={run.status} />{run.status === "running" && <button className="secondary-button" onClick={() => action("pause")}>暂停</button>}{run.status === "paused" && <button className="primary-button" onClick={() => action("resume")}>恢复</button>}{["queued", "paused", "running"].includes(run.status) && <button className="danger-button" onClick={() => action("cancel")}>取消</button>}</>}
      />
      <div className="fv-metric-grid">
        <MetricTile label="当前 Epoch" value={currentEpoch || "—"} caption={metrics.runStatus === "running" ? "每 2 秒增量更新" : "当前 attempt"} tone="running" />
        <MetricTile label="Best Accuracy" value={Number.isFinite(bestAccuracy) ? formatPercent(bestAccuracy) : "未采集"} caption="验证集历史最佳" tone="success" />
        <MetricTile label="Latest Loss" value={latestLoss ? latestLoss.value.toFixed(4) : "未采集"} caption={latestLoss ? `epoch ${latestLoss.step}` : "Ridge 训练不产生曲线"} />
        <MetricTile label="Elapsed" value={elapsedLabel} caption={run.status === "running" ? "运行中" : "按开始时间估算"} />
      </div>

      <div className="fv-detail-grid">
        <div className="fv-chart-stack">
          <Panel eyebrow="Metric history" title="Train Loss / Epoch" aside={<AttemptSelector attempts={metrics.attempts} value={attemptId} onChange={setAttemptId} />}>
            {points.some((point) => point.name === "train_loss") ? <EChart option={lossOption} ariaLabel="训练损失曲线" /> : <EmptyState icon="LineChart" title="指标尚未上报" description="torch_linear_adam 会在每个 epoch 上报；Ridge Linear 只展示最终指标。" />}
          </Panel>
          <Panel eyebrow="Metric history" title="Validation Accuracy / Epoch">
            {latestAccuracy ? <EChart option={accuracyOption} ariaLabel="验证准确率曲线" /> : <EmptyState icon="LineChart" title="暂无验证准确率曲线" description="等待 Compute Runtime 上报新的 Metric Point。" />}
          </Panel>
        </div>
        <aside className="fv-side-stack">
          <Panel eyebrow="Run context" title="实验配置"><dl className="fv-definition-list"><div><dt>Dataset Version</dt><dd><CodeValue>{run.datasetVersionId}</CodeValue></dd></div><div><dt>Backbone</dt><dd>{runBackboneLabel(run)}</dd></div><div><dt>Backbone Key</dt><dd><CodeValue>{run.backboneId}</CodeValue></dd></div><div><dt>Pooling</dt><dd>{run.featurePool || "未记录"}</dd></div><div><dt>Input Size</dt><dd>{run.imageSize || "未记录"}</dd></div><div><dt>Head</dt><dd>{run.headConfig?.head_type || "未记录"}</dd></div><div><dt>Attempt</dt><dd><CodeValue>{attemptId || metrics.attempts.at(-1)?.attempt_id}</CodeValue></dd></div></dl></Panel>
          <Panel eyebrow="Integrity" title="训练产物"><div className="fv-artifact-list">{[["Feature", run.featureArtifactId], ["Model", run.modelArtifactId], ["Report", run.reportArtifactId], ["Calibration", run.calibrationArtifactId]].map(([label, value]) => <div key={label}><span><Icon name={value ? "ShieldCheck" : "CircleDashed"} size={15} />{label}</span><CodeValue>{value}</CodeValue><small>{value ? "逻辑归属已登记；加载时校验 SHA/大小" : "尚未生成"}</small></div>)}</div></Panel>
          {run.modelVersionId && <Link className="primary-button fv-full-button" to={`/models/${run.modelVersionId}`}><Icon name="Boxes" size={15} />打开 Model Version</Link>}
        </aside>
      </div>
    </div>
  );
}

function AttemptSelector({ attempts, value, onChange }) {
  if (!attempts.length) return <span className="fv-muted">等待 attempt</span>;
  return <select aria-label="选择训练 attempt" value={value} onChange={(event) => onChange(event.target.value)}><option value="">当前 / 成功 attempt</option>{attempts.map((attempt) => <option key={attempt.attempt_id} value={attempt.attempt_id}>#{attempt.attempt_number ?? "?"} · {attempt.status}</option>)}</select>;
}
