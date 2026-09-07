import { useState } from "react";
import { Icon } from "../components/icons.jsx";
import { StatusChip } from "../components/ui.jsx";
import { useVLMReviewRuns } from "../hooks/useVLMReviewRuns.js";

const TERMINAL_STATUSES = new Set(["succeeded", "partial_failed", "failed", "cancelled"]);

function runStatus(run) {
  const labels = {
    queued: ["排队中", "info"],
    running: ["处理中", "warn"],
    succeeded: ["已完成", "default"],
    partial_failed: ["部分失败", "warn"],
    failed: ["失败", "risk"],
    cancelled: ["已取消", "neutral"],
  };
  return labels[run.status] ?? [run.status, "neutral"];
}

export function ReviewAutomationEntry({ pendingCount, datasetId }) {
  const [mode, setMode] = useState("assisted");
  const [limit, setLimit] = useState(20);
  const [riskAcknowledged, setRiskAcknowledged] = useState(false);
  const vlm = useVLMReviewRuns();
  const recentRuns = vlm.runs.slice(0, 3);

  async function handleCreate() {
    try {
      await vlm.create({
        mode,
        dataset_id: datasetId || null,
        limit,
        risk_acknowledged: mode === "auto" && riskAcknowledged,
        created_by: "local-operator",
      });
    } catch {
      // The hook exposes the actionable API error inline.
    }
  }

  return (
    <section className="panel review-automation-sidecar">
      <div className="review-automation-sidecar-head">
        <div className="review-automation-mark"><Icon name="Wand2" size={19} /></div>
        <div><span>视觉复核</span><h2>Fine-R1 队列</h2></div>
        <StatusChip tone="default">已接入</StatusChip>
      </div>
      <div className="review-automation-volume">
        <strong>{pendingCount}</strong>
        <span>条待复核样本；仅 abstain 可进入 VLM 队列</span>
      </div>
      <div className="review-automation-guards">
        <span><Icon name="ShieldCheck" size={15} />候选类别约束</span>
        <span><Icon name="DatabaseZap" size={15} />完整留痕</span>
        <span><Icon name="UserCheck" size={15} />门禁失败回退人工</span>
      </div>
      <div className="vlm-mode-selector">
        <button type="button" className={mode === "assisted" ? "active" : ""} onClick={() => setMode("assisted")}>辅助建议</button>
        <button
          type="button"
          className={mode === "auto" ? "active" : ""}
          onClick={() => setMode("auto")}
          disabled={!vlm.capabilities.autoEnabled}
          title={vlm.capabilities.autoEnabled ? "使用双次一致性和独立信号门禁" : "完成目标数据集影子评测后开放"}
        >
          门禁自动提交
        </button>
      </div>
      <label className="vlm-limit-field">
        <span>本次处理</span>
        <select value={limit} onChange={(event) => setLimit(Number(event.target.value))}>
          <option value={10}>10 条</option>
          <option value={20}>20 条</option>
          <option value={50}>50 条</option>
          <option value={100}>100 条</option>
        </select>
      </label>
      {mode === "auto" && (
        <label className="vlm-risk-confirm">
          <input type="checkbox" checked={riskAcknowledged} onChange={(event) => setRiskAcknowledged(event.target.checked)} />
          <span>我理解自动结果可能出错；未通过双次一致性和独立信号门禁的样本仍由人工处理。</span>
        </label>
      )}
      {!vlm.capabilities.autoEnabled && (
        <div className="vlm-inline-notice">自动提交处于影子评测阶段；当前仅生成辅助建议，不写入反馈池。</div>
      )}
      <button
        className="primary-button"
        type="button"
        onClick={handleCreate}
        disabled={vlm.mutating || pendingCount === 0 || (mode === "auto" && !riskAcknowledged)}
      >
        <Icon name={vlm.mutating ? "LoaderCircle" : "Play"} size={16} />
        {vlm.mutating ? "创建中" : "新建 Fine-R1 任务"}
      </button>
      {vlm.error && <div className="vlm-inline-error">{vlm.error.message}</div>}
      <div className="vlm-recent-runs">
        <div className="vlm-recent-head">
          <strong>最近任务</strong>
          <button className="icon-button" type="button" onClick={() => vlm.refresh().catch(() => {})} title="刷新 Fine-R1 任务">
            <Icon name="RefreshCw" size={15} />
          </button>
        </div>
        {recentRuns.length === 0 && <span className="row-meta">{vlm.loading ? "正在读取任务。" : "还没有 Fine-R1 任务。"}</span>}
        {recentRuns.map((run) => {
          const [label, tone] = runStatus(run);
          return (
            <div className="vlm-run-row" key={run.id}>
              <div>
                <strong>{run.mode === "auto" ? "自动门禁" : "辅助建议"} · {run.totalCount} 条</strong>
                <span>
                  {run.succeededCount} 完成 · {run.failedCount} 失败 · {run.skippedCount} 跳过 · {run.fallbackCount} 回退
                </span>
              </div>
              <StatusChip tone={tone}>{label}</StatusChip>
              {!TERMINAL_STATUSES.has(run.status) && (
                <button className="icon-button" type="button" onClick={() => vlm.cancel(run.id).catch(() => {})} title="取消任务">
                  <Icon name="Square" size={14} />
                </button>
              )}
            </div>
          );
        })}
      </div>
    </section>
  );
}
