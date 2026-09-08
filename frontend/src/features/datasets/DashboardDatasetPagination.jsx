import { PaginatedList } from "../../design-system/components/PaginatedList.jsx";
import { useState } from "react";
import "../training/trainingFilters.css";

export function DashboardDatasetPagination({ items, children, showStatus = true }) {
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("");
  const filtered = items.filter(item => (!status || item.status === status) && `${item.name} ${item.id} ${item.datasetVersionId}`.toLowerCase().includes(query.trim().toLowerCase()));
  return <>
    <div className="training-queue-filters">
      <input aria-label="搜索数据集" placeholder="搜索数据集名称 / 版本" value={query} onChange={event => setQuery(event.target.value)} />
      {showStatus && <select aria-label="数据集分页状态筛选" value={status} onChange={event => setStatus(event.target.value)}>
        <option value="">全部状态</option><option value="ready">可训练</option><option value="production">可推理</option><option value="training">准备中</option>
      </select>}
    </div>
    <PaginatedList key={`${query}:${status}`} items={filtered} label="数据集状态分页" unit="个">{children}</PaginatedList>
  </>;
}
