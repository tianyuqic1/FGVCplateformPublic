import { useMemo, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
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
import { useDatasets } from "../../hooks/useDatasets.js";
import { useTrainingRun, useTrainingRuns } from "../../hooks/useTrainingRuns.js";
import { accuracyChartOption, lossChartOption } from "./metricChartOptions.js";
import { useTrainingMetrics } from "./useTrainingMetrics.js";
import { PaginatedList } from "../../design-system/components/PaginatedList.jsx";
import { DatasetPicker } from "./DatasetPicker.jsx";
import { PaginatedSelect } from "../../design-system/components/PaginatedSelect.jsx";

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

function trainingModeLabel(run) {
  const config = run.headConfig ?? {};
  if (config.head_type !== "image_classifier_v2") return "旧版 · 特征 + 分类头";
  if (config.training_mode === "full") return "ImageNet · 全参数更新";
  return config.lora_enabled ? `DINOv3 · 冻结骨干 + LoRA r=${config.lora_rank}` : "DINOv3 · 冻结骨干，仅训练分类头";
}

export function TrainingPage({ showToast }) {
  const navigate = useNavigate();
  const { trainingRuns, loading, error, refresh } = useTrainingRuns();
  const { weights } = useModelWeights();
  const { datasets } = useDatasets();
  const [searchParams] = useSearchParams();
  const [statusFilter, setStatusFilter] = useState("all");
  const [queueQuery, setQueueQuery] = useState("");
  const [queueDataset, setQueueDataset] = useState("");
  const [queueBackbone, setQueueBackbone] = useState("");
  const [form, setForm] = useState({ name: "", datasetVersionId: searchParams.get("dataset_version_id") ?? "", backboneKey: backbones[0].key, loraEnabled: false, loraRank: "8", epochs: "30", batchSize: "8" });
  const isDino = form.backboneKey.startsWith("dinov3_");
  const [submitting, setSubmitting] = useState(false);
  const counts = statusCounts(trainingRuns);
  const filtered = trainingRuns.filter(run => (statusFilter === "all" || run.status === statusFilter) && (!queueDataset || run.datasetId === queueDataset) && (!queueBackbone || run.backboneId === queueBackbone) && `${run.name} ${run.id} ${run.datasetName} ${run.datasetVersionId}`.toLowerCase().includes(queueQuery.trim().toLowerCase()));
  const weightByKey = Object.fromEntries(weights.map((weight) => [weight.backboneKey, weight]));

  async function submit(event) {
    event.preventDefault();
    if (!form.datasetVersionId.trim() || !form.name.trim() || submitting) return;
    setSubmitting(true);
    try {
      const run = await createTrainingRun({
        name: form.name.trim(),
        dataset_version_id: form.datasetVersionId.trim(),
        backbone_key: form.backboneKey,
        head_config: { head_type: "image_classifier_v2", epochs: Number(form.epochs), batch_size: Number(form.batchSize), lora_enabled: isDino && form.loraEnabled, lora_rank: Number(form.loraRank) },
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
        description="DINOv3 冻结骨干，可选 LoRA；ImageNet 更新全部参数。直接读取图片训练，不再离线提取特征。"
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
            <label><span>训练任务名</span><input required maxLength={80} placeholder="例如：鸟类识别 · ViT-S 基线实验" value={form.name} onChange={event => setForm({ ...form, name: event.target.value })} /></label>
            <DatasetPicker datasets={datasets} value={form.datasetVersionId} onChange={datasetVersionId => setForm({ ...form, datasetVersionId })} />
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
            <section className={`fv-adaptation-card ${isDino && form.loraEnabled ? "is-active" : ""}`} aria-label="训练策略">
              <div className="fv-adaptation-heading">
                <span className="fv-adaptation-icon"><Icon name="SlidersHorizontal" size={17} /></span>
                <div><span className="fv-adaptation-eyebrow">训练策略</span><strong>{isDino ? "DINOv3" : "ImageNet"}</strong></div>
                <span className="fv-adaptation-badge">{isDino ? "骨干冻结" : "全参数更新"}</span>
              </div>
              {isDino ? <>
                <div className="fv-adaptation-toggle-row">
                  <div><strong>LoRA 适配</strong><p>在冻结骨干上学习低秩增量</p></div>
                  <button type="button" role="switch" aria-label="启用 LoRA" aria-checked={form.loraEnabled} className="fv-lora-switch" onClick={() => setForm(current => ({ ...current, loraEnabled: !current.loraEnabled }))}><span /></button>
                </div>
                {form.loraEnabled && <div className="fv-lora-ranks" role="group" aria-label="LoRA 秩">
                  {[{ rank: "8", title: "轻量适配", detail: "较少可训练参数" }, { rank: "16", title: "增强适配", detail: "更大适配容量" }].map(item => <button key={item.rank} type="button" aria-pressed={form.loraRank === item.rank} aria-label={`LoRA r=${item.rank}`} onClick={() => setForm(current => ({ ...current, loraRank: item.rank }))}>
                    <span className="fv-rank-heading"><strong>r = {item.rank}</strong><span className="fv-rank-check">{form.loraRank === item.rank && <Icon name="Check" size={11} />}</span></span>
                    <span>{item.title}</span><small>{item.detail}</small>
                  </button>)}
                </div>}
                <div className="fv-adaptation-footer"><span className="fv-adaptation-dot" /><span>{form.loraEnabled ? `更新 A/B 矩阵与分类头 · α = ${Number(form.loraRank) * 2}` : "仅训练分类头，保留原始骨干权重"}</span></div>
              </> : <div className="fv-adaptation-full"><strong>骨干与分类头共同训练</strong><p>从预训练权重初始化，更新全部参数，不使用 LoRA。</p></div>}
            </section>
            <label><span>训练轮数</span><input type="number" required min="1" max="1000" step="1" value={form.epochs} onChange={event => setForm({ ...form, epochs: event.target.value })} /></label>
            <label><span>图片批大小</span><input type="number" required min="1" max="128" step="1" value={form.batchSize} onChange={event => setForm({ ...form, batchSize: event.target.value })} /></label>
            <p className="fv-training-note">保存完整训练检查点；发布时合并 LoRA 并导出完整图片分类 ONNX。本期不生成检索特征。</p>
            <button className="primary-button" type="submit" disabled={submitting || !form.datasetVersionId.trim() || !form.name.trim()}><Icon name="Play" size={15} />{submitting ? "正在创建…" : "创建训练"}</button>
          </form>
        </Panel>

        <Panel
          eyebrow="Run queue"
          title="实验队列"
          aside={<select aria-label="状态筛选" value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)}><option value="all">全部状态</option><option value="running">运行中</option><option value="queued">等待中</option><option value="succeeded">已完成</option><option value="failed">失败</option></select>}
          className="fv-runs-panel"
        >
          <div className="training-queue-filters">
            <input aria-label="搜索训练任务" placeholder="搜索任务名 / 数据集 / ID" value={queueQuery} onChange={event => setQueueQuery(event.target.value)} />
            <PaginatedSelect aria-label="队列数据集筛选" value={queueDataset} onChange={event => setQueueDataset(event.target.value)} options={[{ value: "", label: "全部数据集" }, ...datasets.map(item => ({ value: item.id, label: item.name, detail: item.datasetVersionId }))]} />
            <select aria-label="队列骨干筛选" value={queueBackbone} onChange={event => setQueueBackbone(event.target.value)}><option value="">全部骨干</option>{backbones.map(item => <option key={item.key} value={item.key}>{item.name} · {item.pretraining}</option>)}</select>
            <button className="ghost-button" onClick={() => { setQueueQuery(""); setQueueDataset(""); setQueueBackbone(""); setStatusFilter("all"); }}>重置</button>
          </div>
          {loading && <EmptyState icon="LoaderCircle" title="正在读取训练队列" description="连接 Go Control Plane…" />}
          {!loading && error && <EmptyState icon="TriangleAlert" title="训练服务不可用" description={error.message} />}
          {!loading && !error && filtered.length === 0 && <EmptyState title="暂无匹配运行" description="创建一次训练，或调整状态筛选。" />}
          {filtered.length > 0 && (
            <PaginatedList key={JSON.stringify([statusFilter, queueQuery, queueDataset, queueBackbone])} items={filtered} label="实验队列分页" className="queue-pagination">{pageItems => (
            <div className="fv-table-wrap"><table className="fv-table"><thead><tr><th>训练任务</th><th>Dataset Version</th><th>Backbone</th><th>状态</th><th>最新指标</th><th>创建时间</th></tr></thead><tbody>
              {pageItems.map((run) => <tr key={run.id} tabIndex="0" onKeyDown={(event) => event.key === "Enter" && navigate(`/training/${run.id}`)}><td><Link to={`/training/${run.id}`}><strong title={run.name}>{run.name}</strong></Link></td><td><CodeValue>{run.datasetVersionId}</CodeValue></td><td>{runBackboneLabel(run)}</td><td><StatusBadge status={run.status} /></td><td>{run.metric}</td><td>{run.createdAt ? new Date(run.createdAt).toLocaleString("zh-CN") : "未记录"}</td></tr>)}
            </tbody></table></div>
            )}</PaginatedList>
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
  const elapsed = run?.startedAt ? Math.max(0, (run.finishedAt ? new Date(run.finishedAt).getTime() : Date.now()) - new Date(run.startedAt).getTime()) : null;
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
        <MetricTile label="Latest Loss" value={latestLoss ? latestLoss.value.toFixed(4) : "未采集"} caption={latestLoss ? `epoch ${latestLoss.step}` : "等待首轮训练完成"} />
        <MetricTile label="Elapsed" value={elapsedLabel} caption={run.status === "running" ? "运行中" : "按开始时间估算"} />
      </div>

      <div className="fv-detail-grid">
        <div className="fv-chart-stack">
          <Panel eyebrow="Metric history" title="Train Loss / Epoch" aside={<AttemptSelector attempts={metrics.attempts} value={attemptId} onChange={setAttemptId} />}>
            {points.some((point) => point.name === "train_loss") ? <EChart option={lossOption} ariaLabel="训练损失曲线" /> : <EmptyState icon="LineChart" title="指标尚未上报" description="图片分类训练在每轮结束后上报损失与验证准确率。" />}
          </Panel>
          <Panel eyebrow="Metric history" title="Validation Accuracy / Epoch">
            {latestAccuracy ? <EChart option={accuracyOption} ariaLabel="验证准确率曲线" /> : <EmptyState icon="LineChart" title="暂无验证准确率曲线" description="等待 Compute Runtime 上报新的 Metric Point。" />}
          </Panel>
        </div>
        <aside className="fv-side-stack">
          {run.trainingProgress?.stages?.length > 0 && <Panel eyebrow="Pipeline" title="阶段进度"><div className="fv-artifact-list">{run.trainingProgress.stages.map(stage => <div key={stage.id}><span>{stage.label}</span><small>{run.status === "succeeded" ? "已完成" : `${stage.percent}% · ${stage.status}`}</small><progress max="100" value={run.status === "succeeded" ? 100 : stage.percent} aria-label={stage.label} /></div>)}</div></Panel>}
          <Panel eyebrow="Training mode" title="训练方式"><strong>{trainingModeLabel(run)}</strong><p>{run.headConfig?.head_type === "image_classifier_v2" ? "图片 → 骨干网络 → 分类头；保存完整模型，无离线特征或检索产物。" : "历史任务保留原训练方式与产物。"}</p></Panel>
          <Panel eyebrow="Run context" title="实验配置"><dl className="fv-definition-list"><div><dt>Dataset Version</dt><dd><CodeValue>{run.datasetVersionId}</CodeValue></dd></div><div><dt>Backbone</dt><dd>{runBackboneLabel(run)}</dd></div><div><dt>Backbone Key</dt><dd><CodeValue>{run.backboneId}</CodeValue></dd></div><div><dt>Pooling</dt><dd>{run.featurePool || "未记录"}</dd></div><div><dt>Input Size</dt><dd>{run.imageSize || "未记录"}</dd></div><div><dt>Head</dt><dd>{run.headConfig?.head_type || "未记录"}</dd></div><div><dt>Attempt</dt><dd><CodeValue>{attemptId || metrics.attempts.at(-1)?.attempt_id}</CodeValue></dd></div></dl></Panel>
          <Panel eyebrow="Integrity" title="训练产物"><div className="fv-artifact-list">{[...(run.headConfig?.head_type === "image_classifier_v2" ? [] : [["Feature", run.featureArtifactId]]), ["Model", run.modelArtifactId], ["Report", run.reportArtifactId], ["Calibration", run.calibrationArtifactId]].map(([label, value]) => <div key={label}><span><Icon name={value ? "ShieldCheck" : "CircleDashed"} size={15} />{label}</span><CodeValue>{value}</CodeValue><small>{value ? "逻辑归属已登记；加载时校验 SHA/大小" : "尚未生成"}</small></div>)}</div></Panel>
          {run.modelVersionId && <Link className="primary-button fv-full-button" to={`/models/${run.modelVersionId}`}><Icon name="Boxes" size={15} />打开 Model Version</Link>}
        </aside>
      </div>
    </div>
  );
}

function AttemptSelector({ attempts, value, onChange }) {
  if (!attempts.length) return <span className="fv-muted">等待 attempt</span>;
  return <PaginatedSelect aria-label="选择训练 attempt" value={value} onChange={(event) => onChange(event.target.value)}><option value="">全部 attempt（独立曲线）</option>{attempts.map((attempt) => <option key={attempt.attempt_id} value={attempt.attempt_id}>#{attempt.attempt_number ?? "?"} · {attempt.status}</option>)}</PaginatedSelect>;
}
