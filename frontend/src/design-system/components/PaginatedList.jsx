import { useEffect, useState } from "react";
import { Icon } from "../../components/icons.jsx";
import { paginateItems, pageNumbers } from "./pagination.js";
import "./pagination.css";

export function PaginatedList({ items, children, label = "列表分页", unit = "条", className = "" }) {
  const [page, setPage] = useState(1);
  const result = paginateItems(items, page, 6);
  useEffect(() => { setPage(result.page); }, [result.page]);
  return <>
    {children(result.items)}
    <nav className={`dataset-pagination ${className}`} aria-label={label}>
      <div className="dataset-pagination__summary" aria-live="polite">
        <span><strong>{result.start}–{result.end}</strong> / 共 {result.total} {unit}</span>
        <span className="dataset-pagination__size">6 {unit} / 页</span>
      </div>
      <div className="dataset-pagination__pages">
        <button type="button" aria-label="上一页" title="上一页" disabled={result.page === 1} onClick={() => setPage(result.page - 1)}><Icon name="ChevronLeft" size={16} /></button>
        {pageNumbers(result.page, result.pageCount).map((number, index) => number === null
          ? <span className="dataset-pagination__ellipsis" key={`gap-${index}`} aria-hidden="true">…</span>
          : <button type="button" key={number} aria-label={`第 ${number} 页`} aria-current={number === result.page ? "page" : undefined} onClick={() => setPage(number)}>{number}</button>)}
        <button type="button" aria-label="下一页" title="下一页" disabled={result.page === result.pageCount} onClick={() => setPage(result.page + 1)}><Icon name="ChevronRight" size={16} /></button>
      </div>
    </nav>
  </>;
}
