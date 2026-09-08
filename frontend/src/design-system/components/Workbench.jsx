import { Icon } from "../../components/icons.jsx";

export function PageHeading({ eyebrow, title, description, actions }) {
  return (
    <header className="fv-page-heading">
      <div>
        {eyebrow && <span className="fv-eyebrow">{eyebrow}</span>}
        <h2>{title}</h2>
        {description && <p>{description}</p>}
      </div>
      {actions && <div className="fv-heading-actions">{actions}</div>}
    </header>
  );
}

const statusLabels = {
  queued: "等待中",
  paused: "已暂停",
  running: "运行中",
  succeeded: "已完成",
  failed: "失败",
  cancelled: "已取消",
  candidate: "候选",
  staging: "验证中",
  production: "生产",
  archived: "已归档",
};

export function StatusBadge({ status }) {
  return <span className={`fv-status fv-status--${status}`}><i />{statusLabels[status] ?? status}</span>;
}

export function MetricTile({ label, value, caption, tone = "neutral" }) {
  return (
    <article className={`fv-metric-tile fv-metric-tile--${tone}`}>
      <span>{label}</span>
      <strong>{value ?? "N/A"}</strong>
      <small>{caption}</small>
    </article>
  );
}

export function Panel({ eyebrow, title, aside, children, className = "" }) {
  return (
    <section className={`fv-panel ${className}`}>
      {(eyebrow || title || aside) && (
        <header className="fv-panel-head">
          <div>{eyebrow && <span className="fv-eyebrow">{eyebrow}</span>}{title && <h3>{title}</h3>}</div>
          {aside}
        </header>
      )}
      {children}
    </section>
  );
}

export function EmptyState({ icon = "Inbox", title, description }) {
  return <div className="fv-empty"><Icon name={icon} size={24} /><strong>{title}</strong><p>{description}</p></div>;
}

export function CodeValue({ children, title }) {
  return <code className="fv-code" title={title ?? String(children ?? "")}>{children || "N/A"}</code>;
}

export function formatPercent(value, digits = 2) {
  return value != null && value !== "" && Number.isFinite(Number(value)) ? `${(Number(value) * 100).toFixed(digits)}%` : "未采集";
}

export function formatNumber(value, digits = 3) {
  return value != null && value !== "" && Number.isFinite(Number(value)) ? Number(value).toFixed(digits) : "未采集";
}
