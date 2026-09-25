import { Icon } from "../../components/icons.jsx";
import { pageNumbers } from "./pagination.js";
import "./pagination.css";

export function ServerPagination({ pagination, onPageChange, label = "列表分页", unit = "条", className = "" }) {
  const total = Math.max(0, Number(pagination?.total) || 0);
  const limit = Math.max(1, Number(pagination?.limit) || 6);
  const offset = Math.max(0, Number(pagination?.offset) || 0);
  const pageCount = Math.max(1, Math.ceil(total / limit));
  const page = Math.min(pageCount, Math.floor(offset / limit) + 1);
  const start = total ? offset + 1 : 0;
  const end = Math.min(total, offset + limit);
  return <nav className={`dataset-pagination ${className}`} aria-label={label}>
    <div className="dataset-pagination__summary" aria-live="polite"><span><strong>{start}–{end}</strong> / 共 {total} {unit}</span><span className="dataset-pagination__size">{limit} {unit} / 页</span></div>
    <div className="dataset-pagination__pages">
      <button type="button" aria-label="上一页" disabled={page === 1} onClick={() => onPageChange(page - 1)}><Icon name="ChevronLeft" size={16} /></button>
      {pageNumbers(page, pageCount).map((number, index) => number === null ? <span className="dataset-pagination__ellipsis" key={`gap-${index}`} aria-hidden="true">…</span> : <button type="button" key={number} aria-label={`第 ${number} 页`} aria-current={number === page ? "page" : undefined} onClick={() => onPageChange(number)}>{number}</button>)}
      <button type="button" aria-label="下一页" disabled={page === pageCount} onClick={() => onPageChange(page + 1)}><Icon name="ChevronRight" size={16} /></button>
    </div>
  </nav>;
}
