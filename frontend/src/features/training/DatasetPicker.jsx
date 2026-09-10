import { datasetVersionOptions } from "../../api/datasets.js";
import { PaginatedSelect } from "../../design-system/components/PaginatedSelect.jsx";
import "./trainingFilters.css";

export function DatasetPicker({ datasets, value, onChange }) {
  return <div><span className="training-picker-label">数据集版本</span>
    <PaginatedSelect aria-label="数据集版本" placeholder="请选择已导入的数据集" value={value} onChange={event => onChange(event.target.value)} options={datasetVersionOptions(datasets)} />
  </div>;
}
