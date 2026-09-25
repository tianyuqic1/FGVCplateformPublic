import { Icon } from "../../components/icons.jsx";
import { StatusChip } from "../../components/ui.jsx";

function LLMListItem({ icon, title, items = [], empty, tone = "info" }) {
  return (
    <div className="timeline-item">
      <div className="timeline-icon">
        <Icon name={icon} size={18} />
      </div>
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

export function LLMAssistanceBox({
  title = "LLM 辅助",
  caption,
  assistance,
  status = "idle",
  error,
  onGenerate,
  disabled = false,
}) {
  const isGenerating = status === "generating";
  const hasAssistance = Boolean(assistance?.summary);

  return (
    <div className="llm-assistance">
      <div className="llm-assistance-head">
        <div>
          <strong>{title}</strong>
          <div className="row-meta">{caption || "LLM 仅提供辅助建议，不写入真值、不提交反馈池。"}</div>
        </div>
        <button type="button" className="ghost-button" onClick={onGenerate} disabled={disabled || isGenerating}>
          <Icon name={isGenerating ? "LoaderCircle" : "Wand2"} size={16} />
          {isGenerating ? "生成中" : hasAssistance ? "重新生成" : "生成建议"}
        </button>
      </div>
      {error && !hasAssistance && (
        <div className="route-box risk section-gap-small">
          <div>
            <strong>LLM 辅助暂不可用</strong>
            <div className="row-meta">{error.message}</div>
          </div>
          <StatusChip tone="risk">错误</StatusChip>
        </div>
      )}
      {!error && !hasAssistance && (
        <div className="route-box section-gap-small">
          <div>
            <strong>尚未生成辅助建议</strong>
            <div className="row-meta">这不会影响人工复核、训练或反馈池操作。</div>
          </div>
          <StatusChip tone="info">可选</StatusChip>
        </div>
      )}
      {hasAssistance && (
        <div className="section-gap-small">
          <div className="reason-box">
            <strong>建议摘要</strong>
            <span>{assistance.summary}</span>
          </div>
          {assistance.finalCategorySuggestion?.label && assistance.finalCategorySuggestion.label !== "unknown" && (
            <div className="reason-box section-gap-small">
              <strong>最后类别建议</strong>
              <span>
                {assistance.finalCategorySuggestion.label}
                {assistance.finalCategorySuggestion.rationale
                  ? ` · ${assistance.finalCategorySuggestion.rationale}`
                  : ""}
              </span>
            </div>
          )}
          {assistance.holisticAnalysis && (
            <div className="reason-box section-gap-small">
              <strong>LLM 综合分析</strong>
              <span>{assistance.holisticAnalysis}</span>
            </div>
          )}
          <div className="timeline section-gap-small">
            <LLMListItem icon="ScanSearch" title="检查点" items={assistance.inspectionNotes} empty="没有返回检查点。" />
            <LLMListItem
              icon="CheckCircle2"
              title="建议动作"
              items={assistance.suggestedActions}
              empty="没有返回建议动作。"
            />
            <LLMListItem
              icon="ShieldAlert"
              title="风险提示"
              items={assistance.riskFlags}
              empty="没有额外风险提示。"
              tone="warn"
            />
          </div>
          <div className="chips">
            <StatusChip tone="info">仅供参考</StatusChip>
            {assistance.model && <StatusChip tone="neutral">{assistance.model}</StatusChip>}
            {assistance.createdAt && (
              <StatusChip tone="neutral">{new Date(assistance.createdAt).toLocaleString()}</StatusChip>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
