import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { expandDataset, listTrainingCandidates } from "../../api/datasets.js";
import { Panel, StatusChip } from "../../components/ui.jsx";
import { PaginatedList } from "../../design-system/components/PaginatedList.jsx";
import "./datasetVersions.css";

const sourceLabels = { initial_import: "首次导入", manual_expansion: "手动扩充", review_feedback: "复核回流", mixed_expansion: "上传 + 复核回流" };
const trainingLabels = { untrained: "未训练", training: "训练中 / 等待中", failed: "训练失败", trained: "已训练" };
const splitTotal = (counts, split) => Object.values(counts?.[split] ?? {}).reduce((sum, n) => sum + Number(n), 0);

export function DatasetVersionList({ dataset }) {
  const versions = dataset.versions ?? [];
  const numberById = Object.fromEntries(versions.map(version => [version.id, version.number]));
  if (!versions.length) return <p className="row-meta">暂无版本历史。</p>;
  return <div className="dataset-versions-scroll" role="region" aria-label={`${dataset.name}版本历史`} tabIndex={0}>
    <table className="dataset-versions-table">
      <thead><tr><th>版本 / 来源</th><th>训练 / 验证 / 测试</th><th>权重与训练状态</th><th>操作</th></tr></thead>
      <tbody>{versions.map(version => <tr key={version.id}>
        <td><strong title={version.id}>v{version.number}{version.id === dataset.latestVersionId ? " · 最新" : ""}</strong>
          <small>{sourceLabels[version.sourceType] ?? "历史导入"}{version.parentId ? ` · 基于 v${numberById[version.parentId] ?? "?"}` : ""}</small>
          <small>{version.changes.added_count != null ? `新增 ${version.changes.added_count} 张` : `${version.images} 张`}{version.changes.duplicate_count ? ` · 去重 ${version.changes.duplicate_count} 张` : ""}</small>
          <small>{version.createdAt ? new Date(version.createdAt).toLocaleString() : ""}</small>
        </td>
        <td><span>{splitTotal(version.splitCounts, "train")} / {splitTotal(version.splitCounts, "val")} / {splitTotal(version.splitCounts, "test")}</span>
          {version.parentId && <small>验证集、测试集沿用基础版本</small>}
        </td>
        <td><StatusChip tone={version.hasWeights ? "default" : "neutral"}>{version.hasWeights ? `已有 ${version.modelCount} 份权重` : "暂无权重"}</StatusChip>
          <small>{trainingLabels[version.trainingStatus] ?? "未训练"}</small>
          <div className="version-model-links">{version.models.map(model => <Link key={model.id} to={`/models/${encodeURIComponent(model.id)}`}>{model.name}{model.status === "archived" ? "（已归档）" : ""}</Link>)}</div>
        </td>
        <td>{version.readiness.ready ? <Link className="ghost-button" to={`/training?create=1&dataset_version_id=${encodeURIComponent(version.id)}`}>创建训练</Link> : <small>数据待检查</small>}</td>
      </tr>)}</tbody>
    </table>
  </div>;
}

function trainingFiles(input) {
  const files = Array.from(input).filter(file => /\.(jpg|jpeg|png|webp|bmp)$/i.test(file.name));
  if (!files.length) throw new Error("请选择包含类别子文件夹的图片目录");
  for (const file of files) {
    let parts = (file.webkitRelativePath || file.name).split("/");
    if (parts.length >= 3 && !["train", "val", "test"].includes(parts[0])) parts = parts.slice(1);
    if (["val", "test"].includes(parts[0])) throw new Error("本次只扩充训练集，请选择类别目录或 train 目录，不要包含 val/test 图片。");
    if (parts.length < 2 || (parts[0] === "train" && parts.length < 3)) throw new Error("请保留 类别/图片 或 train/类别/图片 目录结构。");
    if (file.size > 32 * 1024 * 1024) throw new Error("单张图片不能超过 32 MiB");
  }
  if (files.length > 10000 || files.reduce((sum, file) => sum + file.size, 0) > 512 * 1024 * 1024) throw new Error("上传上限为 10000 张、512 MiB");
  return files;
}

export function DatasetExpansionPanel({ dataset, onPublished, showToast }) {
  const [candidates, setCandidates] = useState([]);
  const [candidateError, setCandidateError] = useState(null);
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState([]);
  const [files, setFiles] = useState([]);
  const [fileError, setFileError] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const requestId = useRef(crypto.randomUUID());
  useEffect(() => {
    let active = true;
    listTrainingCandidates(dataset.id).then(items => { if (active) { setCandidates(items); setLoading(false); } })
      .catch(failure => { if (active) { setCandidateError(failure.message); setLoading(false); } });
    return () => { active = false; };
  }, [dataset.id]);
  const next = (dataset.versionNumber || 1) + 1;
  const resetRequest = () => { requestId.current = crypto.randomUUID(); setError(null); };
  function chooseFiles(event) {
    resetRequest();
    try { setFiles(trainingFiles(event.target.files)); setFileError(null); }
    catch (failure) { setFiles([]); setFileError(failure.message); }
  }
  async function publish() {
    if (busy || (!files.length && !selected.length) || fileError) return;
    setBusy(true); setError(null);
    try {
      const result = await expandDataset(dataset.id, { baseVersionId: dataset.latestVersionId, requestId: requestId.current, files, feedbackIds: selected });
      showToast?.(`已生成 v${result.version.version_number}，新增 ${result.upload.added_count} 张训练图片；尚未训练`);
      onPublished();
    } catch (failure) { setError(failure.message); }
    finally { setBusy(false); }
  }
  return <div id="training-expansion"><Panel title="扩充训练集" caption={`基于最新 v${dataset.versionNumber || 1} 生成 v${next}。原有图片和标签保持不变，新图片仅进入训练集，验证集和测试集保持不变。`}>
    <fieldset className="dataset-expansion-fields" disabled={busy}>
      <label className="field"><span>手动补充图片（可选）</span><input type="file" multiple webkitdirectory="" directory="" onChange={chooseFiles} /></label>
      <p className="row-meta">已选 {files.length} 张上传图片；选择类别子文件夹或 train 文件夹，文件夹名称不影响数据集名称。</p>
      {fileError && <p className="error-text" role="alert">{fileError}</p>}
      <strong>已复核的训练候选</strong>
      <p className="row-meta">{loading ? "正在读取候选…" : `${candidates.length} 张待纳入，已选择 ${selected.length} 张。旧模型的复核数据也合入最新数据版本。`}</p>
      {candidateError && <p className="error-text" role="alert">候选暂不可用：{candidateError}</p>}
      {candidates.length > 0 && <>
        <button className="ghost-button" type="button" onClick={() => { resetRequest(); setSelected(selected.length === candidates.length ? [] : candidates.map(item => item.id)); }}>{selected.length === candidates.length ? "取消全选" : "选择全部候选"}</button>
        <PaginatedList items={candidates} label="训练候选分页" unit="张">{items => <div className="training-candidate-list">{items.map(item => <label key={item.id}>
          <input type="checkbox" checked={selected.includes(item.id)} onChange={event => { resetRequest(); setSelected(current => event.target.checked ? [...current, item.id] : current.filter(id => id !== item.id)); }} />
          <span><strong>{item.label}</strong><small>复核 {item.review_id} · 来源模型 {item.model_version_id}</small></span>
        </label>)}</div>}</PaginatedList>
      </>}
      <div className="toolbar spread section-gap-small"><span className="row-meta">本批选择 {files.length + selected.length} 张，后端按图片内容去重。合并后的完整数据集上限为 10000 张、512 MiB。</span>
        <button className="primary-button" type="button" onClick={publish} disabled={busy || Boolean(fileError) || (!files.length && !selected.length)}>{busy ? "正在校验并生成版本…" : `生成 v${next}`}</button>
      </div>
    </fieldset>
    {error && <p className="error-text" role="alert">{error} <button type="button" className="ghost-button" onClick={onPublished}>刷新数据版本</button></p>}
  </Panel></div>;
}
