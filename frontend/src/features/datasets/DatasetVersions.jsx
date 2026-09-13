import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { expandDataset, listTrainingCandidates } from "../../api/datasets.js";
import { Panel, StatusChip } from "../../components/ui.jsx";
import { Icon } from "../../components/icons.jsx";
import { PaginatedList } from "../../design-system/components/PaginatedList.jsx";
import "./datasetVersions.css";

const sourceLabels = { initial_import: "首次导入", manual_expansion: "手动扩充", review_feedback: "复核回流", mixed_expansion: "上传 + 复核回流", annotation_publish: "标注发布", annotation_append: "标注追加" };
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
          {version.parentId && <small>原划分保留{version.sourceType === "annotation_append" ? " · 新样本按发布策略分配" : " · 仅扩充训练集"}</small>}
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
  const folderInput = useRef(null);
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
  return <div id="training-expansion"><Panel className="expansion-panel" title="扩充训练集" caption="补充新图片或纳入已复核样本，生成独立的数据版本。" action={<div className="expansion-version"><span>当前 v{dataset.versionNumber || 1}</span><Icon name="ChevronRight" size={14} /><strong>新版本 v{next}</strong></div>}>
    <div className="expansion-notice"><Icon name="ShieldCheck" size={16} /><span>原有图片与标签保留；新样本仅加入训练集，验证集和测试集不变。</span></div>
    <fieldset className="dataset-expansion-fields" disabled={busy}>
      <div className="expansion-sources">
      <section className="expansion-source" aria-label="手动补充图片">
        <div className="expansion-source-heading"><div><span className="expansion-step">01</span><strong>手动补充图片</strong></div><span className="expansion-optional">可选</span></div>
        <input ref={folderInput} className="expansion-file-input" aria-label="选择训练图片文件夹" type="file" multiple webkitdirectory="" directory="" onChange={chooseFiles} />
        <button className={`expansion-upload ${files.length ? "has-files" : ""}`} type="button" onClick={() => folderInput.current?.click()}>
          <span className="expansion-upload-icon"><Icon name={files.length ? "CheckCircle2" : "FolderInput"} size={24} /></span>
          <strong>{files.length ? `已选择 ${files.length.toLocaleString()} 张图片` : "选择图片文件夹"}</strong>
          <span>{files.length ? "点击重新选择文件夹" : "支持 JPG、PNG、WebP 和 BMP"}</span>
        </button>
        <p className="expansion-hint">选择类别子文件夹或 train 目录，保留「类别 / 图片」结构。文件夹名称不会修改数据集名称。</p>
      {fileError && <p className="error-text" role="alert">{fileError}</p>}
      </section>
      <section className="expansion-source" aria-label="已复核的训练候选">
      <div className="expansion-source-heading"><div><span className="expansion-step">02</span><strong>已复核的训练候选</strong></div><span className="expansion-optional">已选 {selected.length} / {candidates.length}</span></div>
      <p className="expansion-hint">人工确认的样本可纳入本次版本，支持旧模型的复核数据。</p>
      {loading && <div className="expansion-empty" role="status"><Icon name="LoaderCircle" size={24} /><span>正在读取候选…</span></div>}
      {!loading && !candidateError && !candidates.length && <div className="expansion-empty"><Icon name="Inbox" size={28} /><strong>暂无待纳入样本</strong><span>完成图片复核后，训练候选会显示在这里。</span></div>}
      {candidateError && <p className="error-text" role="alert">候选暂不可用：{candidateError}</p>}
      {candidates.length > 0 && <>
        <button className="ghost-button" type="button" onClick={() => { resetRequest(); setSelected(selected.length === candidates.length ? [] : candidates.map(item => item.id)); }}>{selected.length === candidates.length ? "取消全选" : "选择全部候选"}</button>
        <PaginatedList items={candidates} label="训练候选分页" unit="张">{items => <div className="training-candidate-list">{items.map(item => <label key={item.id}>
          <input type="checkbox" checked={selected.includes(item.id)} onChange={event => { resetRequest(); setSelected(current => event.target.checked ? [...current, item.id] : current.filter(id => id !== item.id)); }} />
          <span><strong>{item.label}</strong><small>复核 {item.review_id} · 来源模型 {item.model_version_id}</small></span>
        </label>)}</div>}</PaginatedList>
      </>}
      </section>
      </div>
      <div className="expansion-footer"><div><strong>本次已选 <span>{(files.length + selected.length).toLocaleString()}</span> 张图片</strong><p>上传 {files.length} 张 · 复核 {selected.length} 张 · 保存时按图片内容去重</p><small>单次上传上限 10000 张 / 512 MiB；合并后上限 100000 张 / 5 GiB。</small></div>
        <button className="primary-button" type="button" onClick={publish} disabled={busy || Boolean(fileError) || (!files.length && !selected.length)}><Icon name={busy ? "LoaderCircle" : "Plus"} size={16} />{busy ? "正在校验并生成版本…" : `生成新版本 v${next}`}</button>
      </div>
    </fieldset>
    {error && <p className="error-text" role="alert">{error} <button type="button" className="ghost-button" onClick={onPublished}>刷新数据版本</button></p>}
  </Panel></div>;
}
