import { useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { useDataset, useDatasetSamplePreviews } from "../../hooks/useDatasets.js";
import { Icon } from "../../components/icons.jsx";
import { Panel, StatusChip } from "../../components/ui.jsx";
import { DatasetVersionList, DatasetExpansionPanel } from "./DatasetVersions.jsx";
import { DatasetCardPanel } from "./DatasetCardPanel.jsx";
import { PaginatedList } from "../../design-system/components/PaginatedList.jsx";
import "./dataset-detail.css";

const tabs = [["overview", "数据概览"], ["samples", "样本浏览"], ["versions", "版本与模型"], ["expand", "扩充训练集"], ["description", "数据说明"]];
const splitNames = { train: "训练集", val: "验证集", test: "测试集" };
const className = item => typeof item === "string" ? item : item?.name ?? item?.label ?? item?.id ?? "";
const splitCount = counts => typeof counts === "number" ? counts : Object.values(counts ?? {}).reduce((n, value) => n + (Number(value) || 0), 0);

function SamplePreview({ versionId, browsing = false }) {
  const { samples, loading, error } = useDatasetSamplePreviews(versionId, browsing ? 12 : 6);
  const [query, setQuery] = useState("");
  const [split, setSplit] = useState("");
  const visible = samples.filter(sample => (!split || sample.split === split) && sample.label.toLowerCase().includes(query.trim().toLowerCase()));
  const base = (import.meta.env?.VITE_API_BASE_URL || "").replace(/\/$/, "");
  return <Panel title={browsing ? "样本浏览" : "样本预览"} caption={browsing ? "展示当前版本最多 12 张真实预览；筛选仅作用于已加载图片，不代表完整数据集。" : "当前最新版本的真实图片，最多展示 6 张。"}>
    {browsing && <div className="dataset-sample-filters"><input aria-label="搜索预览类别" value={query} onChange={e => setQuery(e.target.value)} placeholder="搜索预览中的类别…" /><select aria-label="预览数据划分" value={split} onChange={e => setSplit(e.target.value)}><option value="">全部划分</option>{Object.entries(splitNames).map(([id, label]) => <option key={id} value={id}>{label}</option>)}</select><span>{visible.length} / {samples.length} 张预览</span></div>}
    {loading ? <p className="dataset-detail-empty" role="status">正在读取样本…</p> : error ? <p role="alert" className="dataset-detail-empty">预览暂不可用：{error.message.startsWith("404") ? "当前服务尚未提供样本预览接口。" : "图片服务暂时无法访问，请稍后重试。"}</p> : !visible.length ? <p className="dataset-detail-empty">{samples.length ? "没有符合筛选条件的预览" : "暂无可用样本预览"}</p> : <div className="dataset-preview-cards">{visible.map((sample, index) => <article key={sample.sampleId || index}>
      <div className="dataset-preview-image">{sample.imageUrl ? <img loading="lazy" src={sample.imageUrl.startsWith("/") ? base + sample.imageUrl : sample.imageUrl} alt={sample.label} onError={e => { e.currentTarget.style.display = "none"; e.currentTarget.nextElementSibling.hidden = false; }} /> : null}<span hidden={Boolean(sample.imageUrl)}>图片暂不可用</span></div>
      <div className="dataset-preview-caption"><strong title={sample.label}>{sample.label}</strong><span>{splitNames[sample.split] || sample.split}</span></div>
    </article>)}</div>}
  </Panel>;
}

export function DatasetDetailPage({ showToast }) {
  const { datasetId } = useParams();
  const { dataset, loading, error, refresh } = useDataset(datasetId);
  const [params, setParams] = useSearchParams();
  const requested = params.get("tab") || "overview";
  const tab = tabs.some(([id]) => id === requested) ? requested : "overview";
  const back = <Link className="dataset-detail-back" to="/datasets"><Icon name="ArrowLeft" size={15} />返回数据集列表</Link>;
  if (loading || !dataset) return <div className="dataset-detail-page">{back}<Panel title={loading ? "正在加载数据集" : "数据集暂不可用"}><p className="dataset-detail-empty" role="status">{loading ? "读取数据与版本信息…" : error?.message || "未找到该数据集"}</p>{!loading && <button className="ghost-button" onClick={refresh}>重新加载</button>}</Panel></div>;
  const versions = dataset.versions || [];
  const latest = versions.find(v => v.id === dataset.latestVersionId);
  const modelCount = versions.reduce((total, v) => total + v.modelCount, 0);
  const counts = Object.fromEntries(Object.keys(splitNames).map(key => [key, splitCount(dataset.splitCounts?.[key])]));
  const total = Object.values(counts).reduce((a, b) => a + b, 0);
  const classes = dataset.classNames.map(className).filter(Boolean);
  const ready = dataset.readiness?.ready === true || latest?.readiness?.ready === true;
  return <div className="dataset-detail-page">
    {back}
    <header className="dataset-detail-hero">
      <div className="dataset-detail-heading"><span className="dataset-detail-symbol"><Icon name="Database" size={26} /></span><div><small>DATASET · 数据资产</small><h1>{dataset.name}</h1><p>最新数据版本 v{dataset.versionNumber || 1} · 每个版本独立保留图片、标签与模型记录</p></div></div>
      <div className="dataset-detail-actions"><button className="ghost-button" onClick={refresh} aria-label="刷新数据集"><Icon name="RefreshCw" size={15} /></button><button className="secondary-button" onClick={() => setParams({tab:"expand"})}><Icon name="Plus" size={15} />扩充训练集</button>{ready && dataset.datasetVersionId ? <Link className="primary-button" to={`/training?create=1&dataset_version_id=${encodeURIComponent(dataset.datasetVersionId)}`}><Icon name="FlaskConical" size={15} />创建训练</Link> : <button className="primary-button" disabled>数据待检查</button>}</div>
    </header>
    <div className="dataset-detail-stats">{[["图片总量", dataset.images, "最新版本"], ["类别数量", dataset.classes, "分类标签"], ["数据版本", versions.length, "独立数据快照"], ["模型数量", modelCount, "全部数据版本"]].map(([label, value, hint]) => <div key={label}><span>{label}</span><strong>{Number(value).toLocaleString()}</strong><small>{hint}</small></div>)}</div>
    <nav className="dataset-detail-tabs" aria-label="数据集详情导航">{tabs.map(([id,label]) => <button key={id} className={tab === id ? "active" : ""} aria-current={tab === id ? "page" : undefined} onClick={() => setParams(id === "overview" ? {} : {tab:id})}>{label}</button>)}</nav>
    {tab === "overview" && <>
      <div className="dataset-overview-columns">
        <Panel title="数据划分" caption="来自最新版本的实际样本统计。" action={<StatusChip tone={ready ? "default" : "neutral"}>{ready ? "可训练" : "待检查"}</StatusChip>}>
          {total ? <><div className="dataset-split-bar" aria-hidden="true">{Object.keys(splitNames).map(key => <span key={key} className={key} style={{width:`${counts[key]/total*100}%`}} />)}</div><div className="dataset-split-legend">{Object.entries(splitNames).map(([key,label]) => <div key={key}><span><i className={key} />{label}</span><strong>{counts[key].toLocaleString()} 张</strong><small>{(counts[key]/total*100).toFixed(1)}%</small></div>)}</div></> : <p className="dataset-detail-empty">暂未返回划分统计</p>}
        </Panel>
        <Panel title="版本管理" caption="扩充数据不会覆盖历史版本。"><div className="dataset-version-summary"><Icon name="Boxes" size={24} /><div><strong>当前 v{dataset.versionNumber || 1}</strong><p>{latest?.createdAt ? new Date(latest.createdAt).toLocaleString("zh-CN") : "创建时间未记录"}</p><small>{dataset.pendingCandidateCount} 张已复核候选待纳入</small></div></div><button className="ghost-button" onClick={() => setParams({tab:"versions"})}>查看版本与关联模型<Icon name="ChevronRight" size={15} /></button></Panel>
      </div>
      <SamplePreview key={dataset.datasetVersionId} versionId={dataset.datasetVersionId} />
      <Panel title="类别清单" caption={`共 ${dataset.classes} 类，名称来自数据集元数据。`}>{classes.length ? <PaginatedList items={classes} unit="类" label="类别分页">{items => <div className="dataset-class-chips">{items.map(name => <span key={name}>{name}</span>)}</div>}</PaginatedList> : <p className="dataset-detail-empty">暂未返回类别名称</p>}</Panel>
      <details className="dataset-detail-technical"><summary>技术标识</summary><dl><dt>数据集 ID</dt><dd>{dataset.id}</dd><dt>最新数据版本 ID</dt><dd>{dataset.datasetVersionId}</dd></dl></details>
    </>}
    {tab === "samples" && <SamplePreview key={dataset.datasetVersionId} versionId={dataset.datasetVersionId} browsing />}
    {tab === "versions" && <Panel title="版本与关联模型" caption="每个版本独立绑定模型；进入模型详情查看指标与发布信息。"><DatasetVersionList dataset={dataset} /></Panel>}
    {tab === "expand" && <DatasetExpansionPanel key={dataset.datasetVersionId} dataset={dataset} onPublished={refresh} showToast={showToast} />}
    {tab === "description" && <DatasetCardPanel dataset={dataset} showToast={showToast} />}
  </div>;
}
