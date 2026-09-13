import { useState } from "react";
import { Icon } from "../../components/icons.jsx";
import { groupModels } from "./modelGroups.js";
import { useDatasets } from "../../hooks/useDatasets.js";
import "./model-groups.css";

export function ModelGroups({ versions, onScopeChange, children }) {
  const [opened, setOpened] = useState({ dataset: null, version: null });
  const { datasets } = useDatasets();
  const groups = groupModels(versions);
  function open(dataset, version = null) { setOpened({ dataset, version }); onScopeChange(); }
  return <div className="model-dataset-groups">{groups.map(group => {
    const expanded = opened.dataset === group.id;
    const dataset = datasets.find(item => item.id === group.id);
    return <section className="model-dataset-group" key={group.id}>
      <button className="model-group-heading" aria-expanded={expanded} onClick={() => open(expanded ? null : group.id)}>
        <span className="model-group-icon"><Icon name="Database" size={20} /></span>
        <span className="model-group-name"><strong>{group.name}</strong><small>{group.versions.length} 个数据版本 · {group.models.length} 个模型 · {group.models.filter(m => m.status === "production").length} 个已发布</small></span>
        <Icon name={expanded ? "ChevronDown" : "ChevronRight"} size={18} />
      </button>
      {expanded && <div className="model-data-versions">{group.versions.map(version => {
        const active = opened.version === version.key;
        const snapshot = dataset?.versions.find(item => item.id === version.id);
        return <section className="model-data-version" key={version.key}>
          <button className="model-version-heading" aria-expanded={active} onClick={() => open(group.id, active ? null : version.key)}>
            <Icon name={active ? "ChevronDown" : "ChevronRight"} size={16} />
            <strong title={version.id}>{version.number ? `数据版本 v${version.number}` : `历史快照 · ${version.id?.slice(0, 8) || "未记录"}`}</strong>
            <span>{snapshot ? `${snapshot.images.toLocaleString()} 张 · ${snapshot.classes} 类 · ` : ""}{version.models.length} 个模型</span>
          </button>
          {active && <div className="model-version-content"><p className="model-comparison-hint">在此数据版本下选择 2–5 个模型进行比较；切换分组会清空选择。</p>{children(version.models)}</div>}
        </section>;
      })}</div>}
    </section>;
  })}</div>;
}
