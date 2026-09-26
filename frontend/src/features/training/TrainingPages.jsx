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
import { useTrainingRun, useTrainingRunPage, useTrainingRunSummary } from "../../hooks/useTrainingRuns.js";
import { accuracyChartOption, lossChartOption } from "./metricChartOptions.js";
import { useTrainingMetrics } from "./useTrainingMetrics.js";
import { ServerPagination } from "../../design-system/components/ServerPagination.jsx";
import { DatasetPicker } from "./DatasetPicker.jsx";
import { PaginatedSelect } from "../../design-system/components/PaginatedSelect.jsx";
import "../../design-system/components/filter-controls.css";

import { TrainingParameters } from "./TrainingParameters.jsx";
import { trainingHeadConfig, trainingParameterRows } from "./trainingParameters.js";

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
  const { weights } = useModelWeights();
  const { datasets } = useDatasets();
  const [searchParams] = useSearchParams();
  const [statusFilter, setStatusFilter] = useState("all");
  const [queueQuery, setQueueQuery] = useState("");
  const [queueDataset, setQueueDataset] = useState("");
  const [queueBackbone, setQueueBackbone] = useState("");
  const [page, setPage] = useState(1);
  const { trainingRuns, pagination, loading, error, refresh } = useTrainingRunPage({ query: queueQuery, status: statusFilter, datasetId: queueDataset, backboneId: queueBackbone, limit: 6, offset: (page - 1) * 6 });
  const { counts } = useTrainingRunSummary();
  const [form, setForm] = useState({ name: "", datasetVersionId: searchParams.get("dataset_version_id") ?? "", backboneKey: backbones[0].key, loraEnabled: false, loraRank: "8", epochs: "30", batchSize: "8", imageSize: "224", headLearningRate: "0.001", backboneLearningRate: "0.00001", loraLearningRate: "0.0001", augmentations: {} });
  const isDino = form.backboneKey.startsWith("dinov3_");
  const [submitting, setSubmitting] = useState(false);
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
        image_size: Number(form.imageSize),
        head_config: trainingHeadConfig(form),
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
        title="训练任务"
        description="选择数据集和预训练权重，配置训练参数并查看任务进度。"
        actions={<button className="secondary-button" type="button" onClick={refresh}><Icon name="RefreshCw" size={15} />刷新</button>}
      />
      <div className="fv-metric-grid">
        <MetricTile label="全部任务" value={Object.values(counts).reduce((sum, count) => sum + count, 0)} caption="全部训练任务的状态统计" />
        <MetricTile label="运行中" value={counts.running ?? 0} caption="正在执行的训练任务" tone="running" />
        <MetricTile label="已完成" value={counts.succeeded ?? 0} caption="已生成模型版本" tone="success" />
        <MetricTile label="失败 / 取消" value={(counts.failed ?? 0) + (counts.cancelled ?? 0)} caption="需检查诊断信息" tone="danger" />
      </div>

      <div className="fv-training-layout">
        <Panel title="创建训练任务" className="fv-create-panel">
          <form className="fv-form" onSubmit={submit}>
            <label><span>训练任务名</span><input required maxLength={80} placeholder="例如：鸟类识别 · ViT-S 基线实验" value={form.name} onChange={event => setForm({ ...form, name: event.target.value })} /></label>
            <DatasetPicker datasets={datasets} value={form.datasetVersionId} onChange={datasetVersionId => setForm({ ...form, datasetVersionId })} />
            <fieldset>
              <legend>预训练骨干网络</legend>
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
            <label><span>批次大小（Batch size）</span><input type="number" required min="1" max="128" step="1" value={form.batchSize} onChange={event => setForm({ ...form, batchSize: event.target.value })} /></label>
            <TrainingParameters form={form} setForm={setForm} />
            <p className="fv-training-note">训练完成后保存完整模型；发布时合并 LoRA 参数（如启用）并导出 ONNX。</p>
            <button className="primary-button" type="submit" disabled={submitting || !form.datasetVersionId.trim() || !form.name.trim()}><Icon name="Play" size={15} />{submitting ? "正在创建…" : "创建训练任务"}</button>
          </form>
        </Panel>

        <Panel
          title="任务列表"
          aside={<select aria-label="状态筛选" value={statusFilter} onChange={(event) => { setStatusFilter(event.target.value); setPage(1); }}><option value="all">全部状态</option><option value="running">运行中</option><option value="queued">等待中</option><option value="succeeded">已完成</option><option value="failed">失败</option></select>}
          className="fv-runs-panel"
        >
          <div className="training-queue-filters">
            <input aria-label="搜索训练任务" placeholder="搜索任务名 / 数据集 / ID" value={queueQuery} onChange={event => { setQueueQuery(event.target.value); setPage(1); }} />
            <PaginatedSelect aria-label="队列数据集筛选" value={queueDataset} onChange={event => { setQueueDataset(event.target.value); setPage(1); }} options={[{ value: "", label: "全部数据集" }, ...datasets.map(item => ({ value: item.id, label: item.name, detail: item.datasetVersionId }))]} />
            <select aria-label="队列骨干筛选" value={queueBackbone} onChange={event => { setQueueBackbone(event.target.value); setPage(1); }}><option value="">全部骨干</option>{backbones.map(item => <option key={item.key} value={item.key}>{item.name} · {item.pretraining}</option>)}</select>
            <button className="ghost-button" onClick={() => { setQueueQuery(""); setQueueDataset(""); setQueueBackbone(""); setStatusFilter("all"); setPage(1); }}>重置</button>
          </div>
          {loading && <EmptyState icon="LoaderCircle" title="正在加载训练任务" description="正在获取任务数据…" />}
          {!loading && error && <EmptyState icon="TriangleAlert" title="训练服务不可用" description={error.message} />}
          {!loading && !error && trainingRuns.length === 0 && <EmptyState title="暂无匹配的训练任务" description="创建训练任务，或调整筛选条件。" />}
          {trainingRuns.length > 0 && (
            <div className="fv-table-wrap"><table className="fv-table"><thead><tr><th>训练任务</th><th>数据集版本</th><th>骨干网络</th><th>状态</th><th>最新指标</th><th>创建时间</th></tr></thead><tbody>
              {trainingRuns.map((run) => <tr key={run.id} tabIndex="0" onKeyDown={(event) => event.key === "Enter" && navigate(`/training/${run.id}`)}><td><Link to={`/training/${run.id}`}><strong title={run.name}>{run.name}</strong></Link></td><td><CodeValue>{run.datasetVersionId}</CodeValue></td><td>{runBackboneLabel(run)}</td><td><StatusBadge status={run.status} /></td><td>{run.metric}</td><td>{run.createdAt ? new Date(run.createdAt).toLocaleString("zh-CN") : "未记录"}</td></tr>)}
            </tbody></table></div>
          )}
          {!error && <ServerPagination pagination={pagination} onPageChange={setPage} label="任务列表分页" className="queue-pagination" />}
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

  if (loading) return <EmptyState icon="LoaderCircle" title="正在加载训练任务" description={runId} />;
  if (error || !run) return <EmptyState icon="TriangleAlert" title="训练任务加载失败" description={error?.message ?? runId} />;

  return (
    <div className="fv-feature-page">
      <PageHeading
        title={run.name}
        description={<><CodeValue>{run.id}</CodeValue> · <CodeValue>{run.datasetVersionId}</CodeValue></>}
        actions={<>{run.runtimeNodeId && <Link className="secondary-button" to={`/hardware?node=${encodeURIComponent(run.runtimeNodeId)}`}><Icon name="Cpu" size={16} />查看运行节点</Link>}<StatusBadge status={run.status} />{run.status === "running" && <button className="secondary-button" onClick={() => action("pause")}>暂停</button>}{run.status === "paused" && <button className="primary-button" onClick={() => action("resume")}>恢复</button>}{["queued", "paused", "running"].includes(run.status) && <button className="danger-button" onClick={() => action("cancel")}>取消</button>}</>}
      />
      <div className="fv-metric-grid">
        <MetricTile label="当前 Epoch" value={currentEpoch || "—"} caption={metrics.runStatus === "running" ? "每 2 秒增量更新" : "当前 attempt"} tone="running" />
        <MetricTile label="Best Accuracy" value={Number.isFinite(bestAccuracy) ? formatPercent(bestAccuracy) : "未采集"} caption="验证集历史最佳" tone="success" />
        <MetricTile label="Latest Loss" value={latestLoss ? latestLoss.value.toFixed(4) : "未采集"} caption={latestLoss ? `epoch ${latestLoss.step}` : "等待首轮训练完成"} />
        <MetricTile label="Elapsed" value={elapsedLabel} caption={run.status === "running" ? "运行中" : "按开始时间估算"} />
      </div>

      <div className="fv-detail-grid">
        <div className="fv-chart-stack">
          <Panel title="训练损失" aside={<AttemptSelector attempts={metrics.attempts} value={attemptId} onChange={setAttemptId} />}>
            {points.some((point) => point.name === "train_loss") ? <EChart option={lossOption} ariaLabel="训练损失曲线" /> : <EmptyState icon="LineChart" title="指标尚未上报" description="图片分类训练在每轮结束后上报损失与验证准确率。" />}
          </Panel>
          <Panel title="验证准确率">
            {latestAccuracy ? <EChart option={accuracyOption} ariaLabel="验证准确率曲线" /> : <EmptyState icon="LineChart" title="暂无验证准确率曲线" description="等待计算节点上报验证指标。" />}
          </Panel>
        </div>
        <aside className="fv-side-stack">
          {run.trainingProgress?.stages?.length > 0 && <Panel title="阶段进度"><div className="fv-artifact-list">{run.trainingProgress.stages.map(stage => <div key={stage.id}><span>{stage.label}</span><small>{run.status === "succeeded" ? "已完成" : `${stage.percent}% · ${stage.status}`}</small><progress max="100" value={run.status === "succeeded" ? 100 : stage.percent} aria-label={stage.label} /></div>)}</div></Panel>}
          <Panel title="训练方式"><strong>{trainingModeLabel(run)}</strong><p>{run.headConfig?.head_type === "image_classifier_v2" ? "图片 → 骨干网络 → 分类头；保存完整模型，用于后续发布和推理。" : "历史任务保留原训练方式与产物。"}</p></Panel>
          {(run.status === "failed" || run.status === "cancelled") && <Panel title="运行诊断" className="fv-run-diagnostics"><p role="alert">{run.error || (run.status === "cancelled" ? "任务已取消；未返回失败原因。" : "任务失败；服务尚未返回错误详情，请按任务 ID 查阅日志。")}</p><dl className="fv-definition-list"><div><dt>Job ID</dt><dd><CodeValue>{run.jobId || "未记录"}</CodeValue></dd></div><div><dt>Attempt ID</dt><dd><CodeValue>{attemptId || metrics.attempts.at(-1)?.attempt_id || "未记录"}</CodeValue></dd></div><div><dt>计算节点</dt><dd><CodeValue>{run.runtimeNodeId || "未记录"}</CodeValue></dd></div><div><dt>开始时间</dt><dd>{run.startedAt ? new Date(run.startedAt).toLocaleString("zh-CN") : "未记录"}</dd></div><div><dt>结束时间</dt><dd>{run.finishedAt ? new Date(run.finishedAt).toLocaleString("zh-CN") : "未记录"}</dd></div><div><dt>缺失产物</dt><dd>{[["模型", run.modelArtifactId], ["报告", run.reportArtifactId], ["校准", run.calibrationArtifactId]].filter(([, value]) => !value).map(([label]) => label).join("、") || "无"}</dd></div></dl><button type="button" className="secondary-button" onClick={async () => { const details = JSON.stringify({ runId: run.id, jobId: run.jobId, attemptId: attemptId || metrics.attempts.at(-1)?.attempt_id, runtimeNodeId: run.runtimeNodeId, status: run.status, error: run.error }, null, 2); try { await navigator.clipboard.writeText(details); showToast?.("诊断信息已复制"); } catch { showToast?.("复制失败，请手动选择诊断信息"); } }}>复制诊断信息</button></Panel>}
          <Panel title="训练参数"><dl className="fv-definition-list">{trainingParameterRows(run).map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl><p className="fv-training-note">以上为任务保存的配置。验证与测试使用确定性预处理，不应用训练集随机增强。</p></Panel>
          <Panel title="模型配置"><dl className="fv-definition-list"><div><dt>数据集版本</dt><dd><CodeValue>{run.datasetVersionId}</CodeValue></dd></div><div><dt>骨干网络</dt><dd>{runBackboneLabel(run)}</dd></div><div><dt>骨干标识</dt><dd><CodeValue>{run.backboneId}</CodeValue></dd></div><div><dt>池化方式</dt><dd>{run.featurePool || "未记录"}</dd></div><div><dt>输入分辨率</dt><dd>{run.imageSize || "未记录"}</dd></div><div><dt>分类头</dt><dd>{run.headConfig?.head_type || "未记录"}</dd></div><div><dt>执行记录</dt><dd><CodeValue>{attemptId || metrics.attempts.at(-1)?.attempt_id}</CodeValue></dd></div></dl></Panel>
          <Panel title="训练产物"><div className="fv-artifact-list">{[...(run.headConfig?.head_type === "image_classifier_v2" ? [] : [["Feature", run.featureArtifactId]]), ["Model", run.modelArtifactId], ["Report", run.reportArtifactId], ["Calibration", run.calibrationArtifactId]].map(([label, value]) => <div key={label}><span><Icon name={value ? "ShieldCheck" : "CircleDashed"} size={15} />{label}</span><CodeValue>{value}</CodeValue><small>{value ? "已关联；加载时校验文件大小和 SHA" : "尚未生成"}</small></div>)}</div></Panel>
          {run.modelVersionId && <Link className="primary-button fv-full-button" to={`/models/${run.modelVersionId}`}><Icon name="Boxes" size={15} />查看模型版本</Link>}
        </aside>
      </div>
    </div>
  );
}

function AttemptSelector({ attempts, value, onChange }) {
  if (!attempts.length) return <span className="fv-muted">等待 attempt</span>;
  return <PaginatedSelect aria-label="选择训练 attempt" value={value} onChange={(event) => onChange(event.target.value)}><option value="">全部 attempt（独立曲线）</option>{attempts.map((attempt) => <option key={attempt.attempt_id} value={attempt.attempt_id}>#{attempt.attempt_number ?? "?"} · {attempt.status}</option>)}</PaginatedSelect>;
}
