import { Icon } from "../components/icons.jsx";
import { StatusChip } from "../components/ui.jsx";

export function ReviewAutomationEntry({ pendingCount }) {
  return (
    <section className="panel review-automation-sidecar">
      <div className="review-automation-sidecar-head">
        <div className="review-automation-mark"><Icon name="Wand2" size={19} /></div>
        <div><span>自动复核</span><h2>LLM 主导队列</h2></div>
        <StatusChip tone="info">待接入</StatusChip>
      </div>
      <div className="review-automation-volume">
        <strong>{pendingCount}</strong>
        <span>条样本等待处理</span>
      </div>
      <div className="review-automation-guards">
        <span><Icon name="ShieldCheck" size={15} />结构化结论</span>
        <span><Icon name="DatabaseZap" size={15} />完整留痕</span>
        <span><Icon name="UserCheck" size={15} />低置信回退人工</span>
      </div>
      <button className="primary-button" type="button" disabled title="后端自动复核任务尚未接入">
        <Icon name="Play" size={16} />新建自动复核任务
      </button>
    </section>
  );
}
