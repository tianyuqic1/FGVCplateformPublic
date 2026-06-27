import { Link } from "react-router-dom";
import { Icon } from "./icons.jsx";

export function StatusChip({ children, tone = "default" }) {
  return <span className={`status ${tone}`}>{children}</span>;
}

export function Panel({ title, caption, action, children, className = "" }) {
  return (
    <section className={`panel ${className}`}>
      {(title || caption || action) && (
        <div className="panel-title">
          <div>
            {title && <h2>{title}</h2>}
            {caption && <span>{caption}</span>}
          </div>
          {action}
        </div>
      )}
      {children}
    </section>
  );
}

export function ProgressBar({ value, fill = "#0f766e", shimmer = false }) {
  return (
    <div className={`bar ${shimmer ? "shimmer" : ""}`} style={{ "--value": `${value}%`, "--fill": fill }}>
      <i />
    </div>
  );
}

export function MetricCard({ title, value, caption, fill, percent, icon, to }) {
  const content = (
    <>
      <Icon name={icon} size={19} />
      <strong>{value}</strong>
      <span>
        {title}
        <br />
        {caption}
      </span>
      <ProgressBar value={percent} fill={fill} shimmer={percent < 100} />
    </>
  );

  if (to) {
    return (
      <Link className="metric clickable" style={{ "--glow": `${fill}22` }} to={to}>
        {content}
      </Link>
    );
  }

  return (
    <div className="metric" style={{ "--glow": `${fill}22` }}>
      {content}
    </div>
  );
}

export function GateRow({ title, description, result = "pass" }) {
  const pass = result === "pass";
  return (
    <div className="timeline-item">
      <div className="timeline-icon">
        <Icon name={pass ? "Check" : "Clock"} size={18} />
      </div>
      <div>
        <strong>{title}</strong>
        <div className="row-meta">{description}</div>
      </div>
      <StatusChip tone={pass ? "default" : "warn"}>{pass ? "通过" : "待完成"}</StatusChip>
    </div>
  );
}

export function TaskItem({ icon, title, description, action, to, tone = "default" }) {
  return (
    <div className="timeline-item">
      <div className="timeline-icon">
        <Icon name={icon} size={18} />
      </div>
      <div>
        <strong>{title}</strong>
        <div className="row-meta">{description}</div>
      </div>
      <Link className="ghost-button" to={to} data-tone={tone}>
        {action}
      </Link>
    </div>
  );
}

export function VisualPlaceholder({ type = "bird", label, low = false }) {
  return <div className={`visual ${type} ${low ? "low" : ""}`} data-label={label} />;
}

export function VisualCard({ type, label, low = false }) {
  return (
    <div className="image-card">
      <VisualPlaceholder type={type} label={label} low={low} />
      <div className="caption">
        <strong>{label}</strong>
        <div className="row-meta">点击后可查看原图、裁剪、mask 和近邻</div>
      </div>
    </div>
  );
}

export function CandidateBar({ label, score, fill = "#0891b2" }) {
  return (
    <div className="candidate">
      <strong>{label}</strong>
      <div className="candidate-track" style={{ "--value": `${Math.round(score * 100)}%`, "--fill": fill }}>
        <i />
      </div>
      <span>{score.toFixed(2)}</span>
    </div>
  );
}

export function CurveRow({ label, value, percent, fill }) {
  return (
    <div className="curve-row">
      <div className="toolbar spread">
        <strong>{label}</strong>
        <span className="row-meta">{value}</span>
      </div>
      <ProgressBar value={percent} fill={fill} />
    </div>
  );
}
