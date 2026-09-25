import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { uploadImagefolder } from "../../api/datasets.js";
import { PageHero } from "../../components/AppShell.jsx";
import { Icon } from "../../components/icons.jsx";
import { Panel, StatusChip } from "../../components/ui.jsx";
import { useDatasets } from "../../hooks/useDatasets.js";
import { DashboardDatasetPagination } from "./DashboardDatasetPagination.jsx";
import { DatasetImportJobs } from "./DatasetImportJobs.jsx";
import { DatasetVersionList } from "./DatasetVersions.jsx";
import { analyzeImageFolderFiles } from "./imageFolder.js";
import { datasetFilterMatch } from "./presentation.js";
import { pathWithSearch } from "../../utils/urls.js";

function uiStateLabel(status) {
  const labels = {
    idle: "未开始",
    running: "运行中",
    queued: "已排队",
    succeeded: "已完成",
    failed: "失败",
  };
  return labels[status] ?? status;
}

function DatasetTable({ items = [] }) {
  if (!items.length)
    return (
      <div className="empty-state">
        <Icon name="Database" size={24} />
        <strong>暂无数据集</strong>
        <span>填写名称并导入图片，系统自动创建 v1。</span>
      </div>
    );
  return (
    <div className="dataset-groups">
      {items.map((dataset) => (
        <details className="dataset-group" key={dataset.id}>
          <summary>
            <span>
              <strong>{dataset.name}</strong>
              <small>
                {dataset.classes} 类 · {dataset.images.toLocaleString()} 张 · {dataset.versions.length} 个版本
              </small>
            </span>
            <span>最新 v{dataset.versionNumber || 1}</span>
            <StatusChip tone={dataset.versions[0]?.hasWeights ? "default" : "neutral"}>
              {dataset.versions[0]?.hasWeights ? `已有 ${dataset.versions[0].modelCount} 份权重` : "暂无权重"}
            </StatusChip>
            <Link
              className="ghost-button dataset-view-link"
              to={`/datasets/${encodeURIComponent(dataset.id)}`}
              onClick={(event) => event.stopPropagation()}
            >
              <Icon name="ExternalLink" size={15} />
              查看数据集
            </Link>
          </summary>
          <div className="dataset-group-body">
            <div className="toolbar spread">
              <span className="row-meta">{dataset.pendingCandidateCount} 张复核候选待纳入</span>
              <Link className="ghost-button" to={`/datasets/${encodeURIComponent(dataset.id)}`}>
                管理数据集 / 扩充训练集
              </Link>
            </div>
            <DatasetVersionList dataset={dataset} />
          </div>
        </details>
      ))}
    </div>
  );
}

export function DatasetsPage({ showToast }) {
  const { datasets: datasetItems, source, loading, refresh } = useDatasets();
  const folderInputRef = useRef(null);
  const uploadController = useRef(null);
  const [submittedJob, setSubmittedJob] = useState(null);
  const [uploadProgress, setUploadProgress] = useState(null);
  useEffect(() => () => uploadController.current?.abort(), []);
  const [showImport, setShowImport] = useState(false);
  const [datasetFilter, setDatasetFilter] = useState("all");
  const [importForm, setImportForm] = useState({ name: "" });
  const importRequestId = useRef(crypto.randomUUID());
  const [folderSelection, setFolderSelection] = useState({
    valid: false,
    error: "请选择一个本地 ImageFolder 文件夹。",
    files: [],
    rootName: "",
  });
  const [importState, setImportState] = useState({ status: "idle", result: null, error: null });
  const sourceLabel = source === "api" ? "数据资产已同步" : "数据资产暂不可用";
  const visibleDatasets = datasetItems.filter((dataset) => datasetFilterMatch(dataset, datasetFilter));
  const canImport =
    importState.status !== "running" &&
    folderSelection.valid &&
    folderSelection.files.length > 0 &&
    importForm.name.trim();
  const importedDatasetId = importState.result?.dataset?.id ?? importState.result?.dataset?.dataset_id ?? "";
  const importedVersionId =
    importState.result?.version?.datasetVersionId ??
    importState.result?.version?.dataset_version_id ??
    importState.result?.version?.id ??
    "";

  function updateImportField(field, value) {
    importRequestId.current = crypto.randomUUID();
    setImportState({ status: "idle", result: null, error: null });
    setImportForm((current) => ({ ...current, [field]: value }));
  }

  function handleFolderSelection(files) {
    const summary = analyzeImageFolderFiles(files);
    setFolderSelection(summary);
    setImportState({ status: "idle", result: null, error: null });
    if (!summary.valid) return;
    importRequestId.current = crypto.randomUUID();
    setImportForm((current) => ({ name: current.name || summary.rootName }));
  }

  async function handleImportDataset() {
    if (!canImport || uploadController.current) return;
    const controller = new AbortController();
    uploadController.current = controller;
    setImportState({ status: "running", result: null, error: null });
    try {
      const result = await uploadImagefolder({
        files: folderSelection.files,
        name: importForm.name.trim(),
        request_id: importRequestId.current,
        signal: controller.signal,
        onProgress: setUploadProgress,
      });
      if (result.job) {
        setSubmittedJob(result.job);
        setImportState({ status: "queued", result: null, error: null });
        showToast("上传完成，已加入后台导入队列");
      } else {
        setImportState({ status: "succeeded", result, error: null });
        refresh();
        showToast("数据集导入完成，列表已刷新");
      }
      setFolderSelection({ valid: false, error: "可继续选择下一个文件夹。", files: [], rootName: "" });
      if (folderInputRef.current) folderInputRef.current.value = "";
    } catch (error) {
      setImportState({ status: "failed", result: null, error });
      showToast(error.name === "AbortError" ? "上传已停止，请查看后台任务状态" : "数据集上传失败");
    } finally {
      uploadController.current = null;
      setUploadProgress(null);
    }
  }

  return (
    <>
      <PageHero
        title="每个分类任务都是一个独立资产。"
        description="数据集不仅是图片目录，还包括类别体系、样本质量、特征索引、OOD 压力集、阈值策略和模型版本绑定。"
        actions={
          <button className="primary-button" onClick={() => setShowImport((value) => !value)}>
            <Icon name="FolderInput" size={16} />
            导入数据集
          </button>
        }
      />
      {showImport && (
        <Panel
          title="导入本地 ImageFolder"
          caption="选择本地分类图片文件夹，上传完成后由后台队列校验并自动创建 v1，可离开页面等待。上限为 10 万张、5 GiB，单张 32 MiB。"
          action={
            <StatusChip
              tone={importState.status === "failed" ? "risk" : importState.status === "succeeded" ? "default" : "info"}
            >
              {uiStateLabel(importState.status)}
            </StatusChip>
          }
        >
          <div className="field-grid">
            <div className="field full-span">
              <label>本地文件夹</label>
              <div className="file-picker folder-picker">
                <input
                  ref={folderInputRef}
                  disabled={importState.status === "running"}
                  type="file"
                  multiple
                  webkitdirectory=""
                  directory=""
                  onChange={(event) => handleFolderSelection(event.target.files)}
                />
                <Icon name="FolderInput" size={18} />
                <span>{folderSelection.valid ? folderSelection.rootName : "选择 ImageFolder 文件夹"}</span>
                <button
                  className="ghost-button"
                  disabled={importState.status === "running"}
                  type="button"
                  onClick={(event) => {
                    event.preventDefault();
                    folderInputRef.current?.click();
                  }}
                >
                  选择
                </button>
              </div>
              <div className={`row-meta ${folderSelection.valid ? "" : "error-text"}`}>
                {folderSelection.valid
                  ? `${folderSelection.format} · ${folderSelection.imageCount} images · ${folderSelection.classes.length} classes${folderSelection.ignoredCount ? ` · 已忽略 ${folderSelection.ignoredCount} 个非图片文件` : ""}`
                  : folderSelection.error}
              </div>
            </div>
            <div className="field">
              <label htmlFor="dataset-import-name">数据集名称</label>
              <input
                id="dataset-import-name"
                required
                maxLength={120}
                disabled={importState.status === "running"}
                value={importForm.name}
                onChange={(event) => updateImportField("name", event.target.value)}
                placeholder="例如：鸟类识别数据集"
              />
            </div>
            <div className="field">
              <label>初始版本</label>
              <div className="row-meta">系统自动创建 v1，并分配唯一标识。</div>
            </div>
            <div className="field">
              <label>执行</label>
              <button className="primary-button" onClick={handleImportDataset} disabled={!canImport}>
                <Icon name={importState.status === "running" ? "LoaderCircle" : "FolderInput"} size={16} />
                {importState.status === "running" ? "上传中" : "上传并导入"}
              </button>
            </div>
          </div>
          {uploadProgress && (
            <div className="row-meta section-gap-small" role="status">
              {uploadProgress.stage === "preparing"
                ? "准备上传"
                : uploadProgress.stage === "persisting"
                  ? "文件已发送，正在存储并加入后台队列"
                  : "正在上传"}
              {uploadProgress.percent != null && ` · ${uploadProgress.percent}%`}
              <button type="button" className="ghost-button" onClick={() => uploadController.current?.abort()}>
                停止上传
              </button>
            </div>
          )}
          {importState.status === "queued" && (
            <div className="row-meta section-gap-small" role="status">
              上传完成，已加入后台导入队列。可以离开页面，任务会继续处理。
            </div>
          )}
          {folderSelection.valid && (
            <div className="chips section-gap-small">
              <StatusChip tone="default">格式合法</StatusChip>
              <StatusChip tone="info">{folderSelection.format}</StatusChip>
              {folderSelection.splits?.length > 0 && (
                <StatusChip tone="info">{folderSelection.splits.join(" / ")}</StatusChip>
              )}
              <StatusChip tone="info">
                {folderSelection.classes.slice(0, 4).join(", ")}
                {folderSelection.classes.length > 4 ? " ..." : ""}
              </StatusChip>
            </div>
          )}
          {importState.result?.version && (
            <div className="next-step-card section-gap-small">
              <div>
                <div className="chips">
                  <StatusChip tone={importState.result.version.readiness?.ready ? "default" : "warn"}>
                    {importState.result.version.readiness?.ready ? "可训练" : "未就绪"}
                  </StatusChip>
                  <StatusChip tone="info">{importState.result.version.sample_count ?? 0} 张样本</StatusChip>
                  <StatusChip tone="info">{importState.result.version.class_count ?? 0} 类</StatusChip>
                  <StatusChip tone="info">v{importState.result.version.version_number ?? 1} · 暂无权重</StatusChip>
                </div>
                <strong>导入成功，下一步选择数据资产或训练任务</strong>
                <div className="row-meta">数据集已生成不可变版本；训练会使用该版本，不会直接读取本地文件夹。</div>
                {importState.result.upload?.stored_path && (
                  <div className="row-meta">存储路径：{importState.result.upload.stored_path}</div>
                )}
              </div>
              <div className="next-step-actions">
                <Link className="ghost-button" to={`/datasets/${encodeURIComponent(importedDatasetId)}`}>
                  <Icon name="Database" size={16} />
                  打开数据集
                </Link>
                <Link
                  className="primary-button"
                  to={pathWithSearch("/training", [
                    ["create", "1"],
                    ["dataset_version_id", importedVersionId],
                  ])}
                >
                  <Icon name="FlaskConical" size={16} />
                  创建训练
                </Link>
              </div>
            </div>
          )}
          {importState.error && <div className="row-meta section-gap-small">{importState.error.message}</div>}
        </Panel>
      )}
      <DatasetImportJobs submittedJob={submittedJob} onCompleted={refresh} />
      <Panel
        title="数据集列表"
        caption={`${loading ? "正在读取数据资产" : sourceLabel} · 共 ${datasetItems.length} 个数据集 · 每页 6 个 · 筛选结果数量见页脚。`}
        action={
          <div className="tabs">
            {[
              ["all", "全部"],
              ["production", "可推理"],
              ["ready", "可训练"],
            ].map(([value, label]) => (
              <button
                className={`tab-button ${datasetFilter === value ? "active" : ""}`}
                key={value}
                onClick={() => setDatasetFilter(value)}
              >
                {label}
              </button>
            ))}
          </div>
        }
      >
        <DashboardDatasetPagination key={datasetFilter} items={visibleDatasets} showStatus={false}>
          {(items) => <DatasetTable items={items} />}
        </DashboardDatasetPagination>
      </Panel>
    </>
  );
}
