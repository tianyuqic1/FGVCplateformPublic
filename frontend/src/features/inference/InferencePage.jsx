import { useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { datasetSnapshots } from "../../api/datasets.js";
import { listDeployments, deploymentLabel } from "../../api/deployments.js";
import { runInference, runInferenceUpload, runInferenceUploadFolder } from "../../api/inference.js";
import { inferenceModelLabel } from "../../api/inferenceModels.js";
import { getModelVersion } from "../../api/modelVersions.js";
import { Icon } from "../../components/icons.jsx";
import { CandidateBar, Panel, StatusChip } from "../../components/ui.jsx";
import { PaginatedSelect } from "../../design-system/components/PaginatedSelect.jsx";
import { IMAGE_FOLDER_EXTENSIONS } from "../datasets/imageFolder.js";
import { datasetStatusLabel } from "../datasets/presentation.js";
import { useDatasets } from "../../hooks/useDatasets.js";
import { useInferenceModels } from "../../hooks/useInferenceModels.js";
import { useLLMAssistance } from "../../hooks/useLLMAssistance.js";
import { LLMAssistanceBox } from "../llm/LLMAssistanceBox.jsx";
import "./inference.css";

function decisionValueLabel(value) {
  const labels = { accept: "自动通过", abstain: "模型弃权", reject_ood: "OOD 拦截" };
  return labels[value] ?? value;
}

function compactStatusLabel(value) {
  const labels = { failed: "失败", event: "事件" };
  return labels[value] ?? value;
}

function displayValue(value, fallback = "未返回") {
  return value === undefined || value === null || value === "" ? fallback : String(value);
}

function TechnicalDetails({ summary = "技术详情", children }) {
  return (
    <details className="technical-details">
      <summary>{summary}</summary>
      <code>{children}</code>
    </details>
  );
}

function inferenceDecisionStatus(decision) {
  if (decision?.value === "accept") return { label: "可直出", tone: "default", icon: "Check" };
  if (decision?.value === "reject_ood") return { label: "OOD 拦截", tone: "risk", icon: "ShieldAlert" };
  return { label: "进入复核", tone: "warn", icon: "UserCheck" };
}

function inferenceDecisionCopy(decision) {
  if (decision?.value === "accept")
    return {
      title: "模型接受该结果",
      body: "置信度、类别间隔和 OOD 距离都满足当前阈值；系统只记录推理事件，不默认进入人工复核。",
    };
  if (decision?.value === "reject_ood")
    return {
      title: "模型拒识为 OOD 候选",
      body: "样本离训练特征空间过远，系统会把它送入人工复核；人工确认后才进入 OOD 压力池。",
    };
  return {
    title: "模型选择弃权",
    body: "模型没有达到自动直出的阈值，通常是低置信或 top-1/top-2 太接近；系统会把它送入人工复核。",
  };
}

function reasonLabel(reason) {
  const labels = {
    confidence_below_threshold: "置信度低于阈值",
    top1_top2_margin_below_threshold: "Top-1 / Top-2 间隔不足",
    embedding_distance_above_threshold: "Embedding 距离超过 OOD 阈值",
    meets_acceptance_thresholds: "满足自动直出阈值",
  };
  return labels[reason] ?? reason;
}

export function InferencePage({ showToast }) {
  const [inferenceSearchParams] = useSearchParams();
  const requestedInferenceDatasetVersionId = inferenceSearchParams.get("dataset_version_id") || "";
  const requestedInferenceModelVersionId = inferenceSearchParams.get("model_version_id") || "";
  const batchFolderInputRef = useRef(null);
  const inferenceControllerRef = useRef(null);
  const { datasets: apiDatasets, source: datasetSource } = useDatasets();
  const { models: publishedModels, source: modelSource } = useInferenceModels();
  const llm = useLLMAssistance();
  const datasetOptions = datasetSnapshots(apiDatasets);
  const [form, setForm] = useState({
    datasetVersionId: requestedInferenceDatasetVersionId || datasetOptions[0]?.datasetVersionId || "",
    modelVersionId: requestedInferenceModelVersionId,
    imageFile: null,
    imageFiles: [],
    imageFolderName: "",
    imagePath: "",
    sampleId: inferenceSearchParams.get("sample_id") || "",
    topK: 3,
    evidenceK: 3,
  });
  const [state, setState] = useState({ status: "idle", result: null, error: null });
  const [elapsedSeconds, setElapsedSeconds] = useState(0);
  useEffect(() => () => inferenceControllerRef.current?.abort(), []);
  useEffect(() => {
    if (state.status !== "running") return undefined;
    const timer = window.setInterval(() => setElapsedSeconds((value) => value + 1), 1000);
    return () => window.clearInterval(timer);
  }, [state.status]);
  const [previewUrl, setPreviewUrl] = useState("");
  const [deployments, setDeployments] = useState({ model: "", rows: [], error: "" });
  const [deploymentId, setDeploymentId] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    setDeploymentId("");
    setDeployments({ model: "", rows: [], error: "" });
    if (form.modelVersionId)
      listDeployments(form.modelVersionId, controller.signal)
        .then((data) => {
          if (controller.signal.aborted) return;
          const rows = (data.deployments || []).filter((row) => row.status === "ready");
          setDeployments({ model: form.modelVersionId, rows, error: "" });
          setDeploymentId(rows[0]?.id || "");
        })
        .catch((e) => {
          if (!controller.signal.aborted) setDeployments({ model: form.modelVersionId, rows: [], error: e.message });
        });
    return () => controller.abort();
  }, [form.modelVersionId]);
  const datasetVersionOptions = datasetOptions.map((dataset) => dataset.datasetVersionId).filter(Boolean);
  const selectedDatasetVersionId = form.datasetVersionId.trim();
  const selectedDataset =
    datasetOptions.find((dataset) => dataset.datasetVersionId === selectedDatasetVersionId) ?? null;
  const modelVersionOptions = publishedModels.filter(
    (model) => model.status === "production" && model.datasetVersionId === selectedDatasetVersionId,
  );
  const modelVersionIds = modelVersionOptions.map((model) => model.modelVersionId);
  const canUseInferenceInputs =
    datasetSource === "api" &&
    modelSource === "api" &&
    datasetVersionOptions.length > 0 &&
    modelVersionIds.includes(form.modelVersionId);
  const canRun =
    canUseInferenceInputs &&
    deployments.model === form.modelVersionId &&
    deployments.rows.some((row) => row.id === deploymentId) &&
    state.status !== "running" &&
    form.datasetVersionId.trim() &&
    form.modelVersionId.trim() &&
    (form.imageFiles.length > 0 || form.imageFile || form.imagePath.trim() || form.sampleId.trim());
  const inferenceBlockReason = canUseInferenceInputs
    ? ""
    : datasetSource === "loading" || modelSource === "loading"
      ? "正在同步数据集与已发布模型；完成前不判断是否可推理。"
      : datasetSource !== "api"
        ? "数据资产暂不可用，不能运行推理。"
        : modelSource !== "api"
          ? "已发布模型列表暂不可用，不能运行推理。"
          : modelVersionIds.length === 0
            ? "当前数据版本暂无已发布模型，请先完成模型发布。"
            : "当前没有真实 dataset version 可用于推理。";

  useEffect(() => {
    if (datasetSource !== "api" || modelSource !== "api" || datasetVersionOptions.length === 0) return;
    setForm((current) => {
      const next = { ...current };
      if (!datasetVersionOptions.includes(next.datasetVersionId)) {
        next.datasetVersionId = datasetVersionOptions[0];
      }
      if (modelVersionIds.length > 0 && !modelVersionIds.includes(next.modelVersionId)) {
        next.modelVersionId = modelVersionIds[0];
      }
      if (modelVersionIds.length === 0) {
        next.modelVersionId = "";
      }
      return next;
    });
  }, [
    datasetSource,
    modelSource,
    datasetVersionOptions.join("|"),
    selectedDatasetVersionId,
    modelVersionIds.join("|"),
    requestedInferenceDatasetVersionId,
    requestedInferenceModelVersionId,
  ]);

  useEffect(() => {
    if (!form.imageFile) {
      setPreviewUrl("");
      return undefined;
    }
    const objectUrl = URL.createObjectURL(form.imageFile);
    setPreviewUrl(objectUrl);
    return () => URL.revokeObjectURL(objectUrl);
  }, [form.imageFile]);

  function updateField(field, value) {
    setForm((current) => ({ ...current, [field]: value }));
  }

  function updateImageFile(file) {
    setForm((current) => ({
      ...current,
      imageFile: file,
      imageFiles: [],
      imageFolderName: "",
      imagePath: file ? "" : current.imagePath,
      sampleId: file ? "" : current.sampleId,
    }));
  }

  function updateImageFolder(files) {
    const imageFiles = Array.from(files ?? []).filter((file) =>
      IMAGE_FOLDER_EXTENSIONS.has(`.${file.name.split(".").pop()?.toLowerCase()}`),
    );
    const folderName = imageFiles[0]?.webkitRelativePath?.split("/")?.[0] ?? "";
    setForm((current) => ({
      ...current,
      imageFile: null,
      imageFiles,
      imageFolderName: folderName,
      imagePath: "",
      sampleId: "",
    }));
  }

  async function handleRun() {
    if (!canRun) return;
    const controller = new AbortController();
    inferenceControllerRef.current = controller;
    setElapsedSeconds(0);
    setState({ status: "running", result: null, error: null });
    try {
      const latestModel = await getModelVersion(form.modelVersionId, controller.signal);
      controller.signal.throwIfAborted();
      if (latestModel.status !== "production" || latestModel.datasetVersionId !== form.datasetVersionId) {
        throw new Error("该模型已下架或不属于当前数据集，请刷新并选择已发布模型。");
      }
      const commonInput = {
        deployment_id: deploymentId,
        dataset_version_id: form.datasetVersionId.trim(),
        model_version_id: form.modelVersionId.trim(),
        top_k: Number(form.topK),
        evidence_k: Number(form.evidenceK),
        signal: controller.signal,
      };
      const result =
        form.imageFiles.length > 0
          ? await runInferenceUploadFolder({
              ...commonInput,
              images: form.imageFiles,
              route_all_to_review: true,
            })
          : form.imageFile
            ? await runInferenceUpload({
                ...commonInput,
                image: form.imageFile,
              })
            : await runInference({
                ...commonInput,
                image_path: form.imagePath.trim() || null,
                sample_id: form.sampleId.trim() || null,
              });
      controller.signal.throwIfAborted();
      setState({ status: "succeeded", result, error: null });
      showToast("推理完成，结果已更新");
    } catch (error) {
      if (controller.signal.aborted) {
        setState({ status: "cancelled", result: null, error: null });
        showToast("已停止等待推理结果；服务端可能仍在处理已提交的请求");
      } else {
        setState({ status: "failed", result: null, error });
        showToast(error?.name === "TimeoutError" ? "推理超时，请稍后重试" : "推理请求失败");
      }
    } finally {
      if (inferenceControllerRef.current === controller) inferenceControllerRef.current = null;
    }
  }

  const result = state.result;
  const isBatchResult = Boolean(result?.batch);
  const isBatchFolderMode = form.imageFiles.length > 0;
  const decisionState = inferenceDecisionStatus(isBatchResult ? null : result?.decision);
  const decisionCopy = inferenceDecisionCopy(isBatchResult ? null : result?.decision);
  const queryLabel = isBatchFolderMode
    ? `${form.imageFolderName || "文件夹"} · ${form.imageFiles.length} 张图片`
    : form.imageFile?.name || form.sampleId || form.imagePath || "query image";

  async function handleGenerateInferenceExplanation() {
    if (!result) return;
    const imageDataUrl = form.imageFile ? await readFileAsDataUrl(form.imageFile) : null;
    await llm.generate({
      task: "inference_explanation",
      context: {
        inference_event_id: result.inferenceEventId,
        review_item_id: result.reviewItemId,
        dataset_id: result.datasetId,
        dataset_version_id: result.datasetVersionId,
        model_version_id: result.modelVersionId,
        image_input: {
          ...result.input,
          local_preview_name: queryLabel,
          has_browser_upload_preview: Boolean(previewUrl),
          image_pixels_attached: Boolean(imageDataUrl),
          ...(imageDataUrl ? { image_data_url: imageDataUrl } : {}),
        },
        decision: result.decision,
        top_k: result.topK,
        nearest_neighbors: result.nearestNeighbors.slice(0, 5),
      },
    });
  }

  return (
    <div className="inference-lab">
      <header className="inference-intro">
        <div>
          <div className="inference-eyebrow">
            <Icon name="ScanSearch" size={16} />
            视觉验证工作台
          </div>
          <h2>从一张图片，读懂模型的判断</h2>
          <p>选择已发布模型，上传样本，查看预测结果与判断依据。</p>
        </div>
        <div className="inference-flow" aria-label="推理流程">
          <span>
            <b>01</b> 准备样本
          </span>
          <Icon name="ChevronRight" size={16} />
          <span>
            <b>02</b> 查看结果
          </span>
        </div>
      </header>
      <div className="inference-published-notice">
        <Icon name="ShieldCheck" size={16} />
        仅使用已发布模型 · 推理结果与复核记录自动留存
      </div>
      <div className="inference-stack">
        <Panel
          className="inference-input-panel"
          title="01 / 准备推理"
          caption="先选择数据与模型，再添加要验证的样本。"
          action={
            <StatusChip tone={state.status === "running" ? "info" : "neutral"}>
              {state.status === "running" ? "运行中" : isBatchFolderMode ? "批量模式" : "单图模式"}
            </StatusChip>
          }
        >
          <div className="field-grid section-gap-small">
            <div className="field inference-selection">
              <label>1 · 训练数据集</label>
              <PaginatedSelect
                aria-label="推理数据版本"
                value={form.datasetVersionId}
                onChange={(event) =>
                  setForm((current) => ({
                    ...current,
                    datasetVersionId: event.target.value,
                    modelVersionId: "",
                    sampleId: "",
                  }))
                }
                disabled={datasetVersionOptions.length === 0}
                placeholder={datasetSource === "loading" ? "正在加载训练数据集…" : "暂无训练数据集"}
                options={datasetOptions
                  .filter((dataset) => dataset.datasetVersionId)
                  .map((dataset) => ({
                    value: dataset.datasetVersionId,
                    label: dataset.name,
                    detail: `v${dataset.versionNumber || 1} · ${dataset.classes} 个类别 · ${dataset.images} 张样本 · ${dataset.hasWeights ? "已有权重" : "暂无权重"}`,
                    meta: datasetStatusLabel(dataset.status),
                  }))}
              />
              <span className="field-hint">
                这是模型训练时的数据集快照，决定识别的类别范围与关联样本，不是待识别图片的版本。
              </span>
            </div>
            <div className="field inference-selection">
              <label>识别模型</label>
              <PaginatedSelect
                aria-label="推理模型版本"
                value={form.modelVersionId}
                onChange={(event) => updateField("modelVersionId", event.target.value)}
                disabled={modelVersionIds.length === 0 || state.status === "running"}
                placeholder={
                  modelSource === "loading" || datasetSource === "loading"
                    ? "正在加载已发布模型…"
                    : "该数据集暂无已发布模型"
                }
                options={modelVersionOptions.map((model) => ({
                  value: model.modelVersionId,
                  label: inferenceModelLabel(model),
                }))}
              />
              <span className="field-hint">仅展示当前数据集的 {modelVersionIds.length} 个已发布模型</span>
            </div>
          </div>
          <div className="field inference-selection section-gap-small">
            <label>推理部署 / 运行精度</label>
            <PaginatedSelect
              aria-label="推理部署"
              value={deploymentId}
              onChange={(e) => setDeploymentId(e.target.value)}
              disabled={state.status === "running" || !deployments.rows.length}
              placeholder="暂无就绪部署"
              options={deployments.rows.map((row) => ({
                value: row.id,
                label: deploymentLabel(row),
                detail: `批上限 ${row.max_batch} · 已校验`,
              }))}
            />
            <span className="field-hint">
              {deployments.error || "明确选择实际运行后端；加速部署失败时不会自动切换到 CPU。"}
            </span>
            {result?.runtime && (
              <span className="field-hint">
                本次运行：{result.runtime.actual_runtime} · {result.runtime.precision} ·{" "}
                {Number(result.runtime.latency_ms).toFixed(0)} ms（含 RPC）
              </span>
            )}
          </div>
          <div className="inference-input-workspace">
            <div className="inference-preview-area">
              {isBatchFolderMode ? (
                <div className="empty-query-preview">
                  <Icon name="FolderInput" size={32} />
                  <strong>{form.imageFolderName || "批量图片文件夹"}</strong>
                  <span>{form.imageFiles.length} 张图片将批量推理，并默认进入人工复核队列。</span>
                </div>
              ) : previewUrl ? (
                <div className="uploaded-preview">
                  <img src={previewUrl} alt={queryLabel} />
                  <span>{queryLabel}</span>
                </div>
              ) : (
                <div className="empty-query-preview">
                  <Icon name="ImageUp" size={32} />
                  <strong>让模型看看你的图片</strong>
                  <span>支持单张图片或整个文件夹，也可以使用已有样本。</span>
                </div>
              )}
            </div>
            <div className="inference-upload-area">
              <div className="inference-section-label">添加验证样本</div>
              <div className="field full-span">
                <label>上传图片</label>
                <label className="file-picker">
                  <input
                    type="file"
                    aria-label="选择单张推理图片"
                    accept="image/png,image/jpeg,image/webp,image/bmp"
                    onChange={(event) => updateImageFile(event.target.files?.[0] ?? null)}
                  />
                  <Icon name="ImageUp" size={18} />
                  <span>{form.imageFile?.name || "选择图片 · PNG / JPG / WebP / BMP"}</span>
                </label>
              </div>
              <div className="field full-span">
                <label>批量上传文件夹</label>
                <div className="file-picker folder-picker">
                  <input
                    ref={batchFolderInputRef}
                    type="file"
                    aria-label="选择批量推理图片文件夹"
                    multiple
                    webkitdirectory=""
                    directory=""
                    onChange={(event) => updateImageFolder(event.target.files)}
                  />
                  <Icon name="FolderInput" size={18} />
                  <span>
                    {isBatchFolderMode
                      ? `${form.imageFolderName || "文件夹"} · ${form.imageFiles.length} 张图片`
                      : "选择图片文件夹批量推理"}
                  </span>
                  <button
                    className="ghost-button"
                    type="button"
                    onClick={(event) => {
                      event.preventDefault();
                      batchFolderInputRef.current?.click();
                    }}
                  >
                    选择
                  </button>
                </div>
                <span className="field-hint">批量模式会逐张运行推理，并忽略单样本 ID 和容器内图片路径输入。</span>
              </div>
            </div>
          </div>
          <details className="advanced-fields inference-parameters">
            <summary>
              推理参数与其他输入方式 <span>样本 ID · Top-k · 近邻数</span>
            </summary>
            <div className="field-grid section-gap-small">
              <div className="field">
                <label htmlFor="inference-sample-id">样本 ID</label>
                <input
                  id="inference-sample-id"
                  value={form.sampleId}
                  onChange={(event) => updateField("sampleId", event.target.value)}
                  disabled={Boolean(form.imageFile) || isBatchFolderMode}
                  placeholder={isBatchFolderMode ? "批量文件夹模式下忽略" : "当前数据集版本中的样本 ID"}
                />
                {isBatchFolderMode && <span className="field-hint">已选择文件夹，运行时不会发送 sample_id。</span>}
              </div>
              <div className="field">
                <label htmlFor="inference-top-k">Top-k 候选数</label>
                <input
                  id="inference-top-k"
                  type="number"
                  min="1"
                  max="10"
                  value={form.topK}
                  onChange={(event) => updateField("topK", event.target.value)}
                />
              </div>
              <div className="field">
                <label htmlFor="inference-evidence-k">检索近邻数</label>
                <input
                  id="inference-evidence-k"
                  type="number"
                  min="0"
                  max="10"
                  value={form.evidenceK}
                  onChange={(event) => updateField("evidenceK", event.target.value)}
                />
              </div>
              <span className="field-hint full-span">本地图片请使用上方上传入口；服务不读取任意服务器文件路径。</span>
            </div>
          </details>
          <div className="toolbar inference-run-bar">
            <span className="inference-run-hint">
              {state.status === "running"
                ? `正在${elapsedSeconds < 3 ? "验证模型与部署" : "计算推理并记录结果"} · 已等待 ${elapsedSeconds} 秒（单张最长 120 秒，批量最长 10 分钟）`
                : isBatchFolderMode
                  ? `已选 ${form.imageFiles.length} 张图片，运行后进入人工复核`
                  : form.imageFile || form.sampleId || form.imagePath
                    ? "样本已就绪，开始验证模型表现"
                    : "添加样本后即可开始推理"}
            </span>
            <button className="primary-button" onClick={handleRun} disabled={!canRun}>
              <Icon name={state.status === "running" ? "LoaderCircle" : "Play"} size={16} />
              {state.status === "running" ? "推理中" : "运行推理"}
            </button>
            {state.status === "running" && (
              <button
                type="button"
                className="secondary-button"
                onClick={() => inferenceControllerRef.current?.abort()}
              >
                停止等待
              </button>
            )}
            <button
              className="ghost-button"
              onClick={() => updateImageFile(null)}
              disabled={!form.imageFile || state.status === "running"}
            >
              <Icon name="RefreshCw" size={16} />
              清除图片
            </button>
            <button
              className="ghost-button"
              onClick={() => updateImageFolder([])}
              disabled={form.imageFiles.length === 0 || state.status === "running"}
            >
              <Icon name="RefreshCw" size={16} />
              清除文件夹
            </button>
          </div>
          {inferenceBlockReason && (
            <div className="route-box section-gap-small">
              <div>
                <strong>推理运行已暂停</strong>
                <div className="row-meta">{inferenceBlockReason}</div>
              </div>
              <StatusChip tone="warn">暂不可运行</StatusChip>
            </div>
          )}
        </Panel>
        <Panel
          className="inference-results-panel"
          title="02 / 推理结果"
          caption="预测类别、置信度与复核建议，在这里一起查看。"
          action={
            <StatusChip
              tone={
                state.status === "idle"
                  ? "neutral"
                  : state.status === "cancelled"
                    ? "warn"
                    : state.status === "failed"
                      ? "risk"
                      : state.status === "running"
                        ? "info"
                        : isBatchResult
                          ? "info"
                          : decisionState.tone
              }
            >
              {state.status === "idle"
                ? "待运行"
                : state.status === "cancelled"
                  ? "已停止等待"
                  : state.status === "running"
                    ? "运行中"
                    : state.status === "failed"
                      ? "失败"
                      : isBatchResult
                        ? "批量结果"
                        : decisionState.label}
            </StatusChip>
          }
        >
          {state.status === "idle" && (
            <div className="inference-result-empty">
              <div className="inference-empty-icon">
                <Icon name="ScanSearch" size={30} />
              </div>
              <strong>每一次预测，都有据可查</strong>
              <p>在上方添加样本并运行推理，结果将在这里展开。</p>
              <div className="inference-result-capabilities">
                <span>
                  <Icon name="BarChart3" size={16} />
                  候选类别与置信度
                </span>
                <span>
                  <Icon name="GitCompare" size={16} />
                  近邻样本证据
                </span>
                <span>
                  <Icon name="UserCheck" size={16} />
                  人工复核建议
                </span>
              </div>
            </div>
          )}
          {state.status === "running" && (
            <div className="timeline-item">
              <div className="timeline-icon">
                <Icon name="LoaderCircle" size={18} />
              </div>
              <div>
                <strong>正在运行推理</strong>
                <div className="row-meta">
                  {form.datasetVersionId} · {form.modelVersionId} · 进度由服务返回后确认
                </div>
                <div className="row-meta" role="status">
                  服务端尚未返回阶段进度；这里不显示虚构完成百分比。
                </div>
              </div>
              <StatusChip tone="info">运行中</StatusChip>
            </div>
          )}
          {state.status === "failed" && (
            <div className="timeline-item">
              <div className="timeline-icon">
                <Icon name="AlertTriangle" size={18} />
              </div>
              <div>
                <strong>推理失败</strong>
                <div className="row-meta">{state.error?.message ?? "推理请求失败"}</div>
              </div>
              <StatusChip tone="risk">失败</StatusChip>
            </div>
          )}
          {state.status === "cancelled" && (
            <div className="timeline-item" role="status">
              <div className="timeline-icon">
                <Icon name="Pause" size={18} />
              </div>
              <div>
                <strong>已停止等待推理结果</strong>
                <div className="row-meta">浏览器请求已取消；服务端可能仍在处理已提交的任务，可稍后查看复核记录。</div>
              </div>
              <StatusChip tone="warn">已停止等待</StatusChip>
            </div>
          )}
          {state.status === "succeeded" && isBatchResult && (
            <>
              <div className="decision-summary">
                <div className="decision-callout">
                  <div className="timeline-icon">
                    <Icon name="FolderInput" size={18} />
                  </div>
                  <div>
                    <strong>批量推理完成</strong>
                    <div className="row-meta">
                      {result.batch.succeeded}/{result.batch.total} 张完成，{result.batch.review_item_count}{" "}
                      条进入人工复核队列。
                    </div>
                    <div className="row-meta">
                      批次 {displayValue(result.batch.batch_id ?? result.batch.inference_run_id)}
                    </div>
                  </div>
                  <StatusChip tone={result.batch.failed > 0 ? "warn" : "default"}>
                    {result.batch.failed > 0 ? "部分失败" : "完成"}
                  </StatusChip>
                </div>
                <div className="evidence-metrics">
                  <div>
                    <span>total</span>
                    <strong>{result.batch.total}</strong>
                  </div>
                  <div>
                    <span>succeeded</span>
                    <strong>{result.batch.succeeded}</strong>
                  </div>
                  <div>
                    <span>review items</span>
                    <strong>{result.batch.review_item_count}</strong>
                  </div>
                </div>
                <div className="route-box">
                  <div>
                    <strong>进入人工复核队列</strong>
                    <div className="row-meta">批量上传默认创建复核项；你可以打开队列后按当前数据集连续处理。</div>
                  </div>
                  <Link
                    className="primary-button"
                    to={`/review?status=pending&dataset_id=${encodeURIComponent(selectedDataset?.id ?? "")}`}
                  >
                    <Icon name="UserCheck" size={16} />
                    打开复核队列
                  </Link>
                </div>
              </div>
              <div className="timeline section-gap-small">
                {result.results.slice(0, 8).map((item, index) => (
                  <div className="timeline-item" key={item.inferenceEventId || index}>
                    <div className="timeline-icon">
                      <Icon name={item.reviewItemId ? "UserCheck" : "CheckCircle2"} size={18} />
                    </div>
                    <div>
                      <strong>{item.input?.upload_filename ?? `image-${index + 1}`}</strong>
                      <div className="row-meta">
                        {item.decision.value} · {item.topK[0]?.label ?? "unknown"}{" "}
                        {item.topK[0] ? item.topK[0].score.toFixed(3) : ""}
                      </div>
                      <div className="row-meta">
                        运行 {displayValue(item.inferenceRunId)} · 事件 {displayValue(item.inferenceEventId)}
                      </div>
                    </div>
                    {item.reviewItemId ? (
                      <StatusChip tone="info">{item.reviewItemId}</StatusChip>
                    ) : (
                      <StatusChip tone="default">{compactStatusLabel("event")}</StatusChip>
                    )}
                  </div>
                ))}
                {result.results.length > 8 && (
                  <div className="row-meta">还有 {result.results.length - 8} 条结果未展开；请到复核队列继续处理。</div>
                )}
                {result.failures.length > 0 && (
                  <div className="route-box risk">
                    <div>
                      <strong>{result.failures.length} 张图片失败</strong>
                      <div className="row-meta">
                        {result.failures
                          .slice(0, 3)
                          .map((failure) => `${failure.filename}: ${failure.error}`)
                          .join("；")}
                      </div>
                    </div>
                    <StatusChip tone="risk">{compactStatusLabel("failed")}</StatusChip>
                  </div>
                )}
              </div>
            </>
          )}
          {state.status === "succeeded" && result && !isBatchResult && (
            <>
              <div className="decision-summary">
                <div className="decision-callout">
                  <div className="timeline-icon">
                    <Icon name={decisionState.icon} size={18} />
                  </div>
                  <div>
                    <strong>{decisionCopy.title}</strong>
                    <div className="row-meta">{decisionCopy.body}</div>
                  </div>
                  <StatusChip tone={decisionState.tone}>{decisionValueLabel(result.decision.value)}</StatusChip>
                </div>
                <div className="evidence-metrics">
                  <div>
                    <span>confidence</span>
                    <strong>{result.decision.confidence.toFixed(4)}</strong>
                  </div>
                  <div>
                    <span>margin</span>
                    <strong>{result.decision.margin.toFixed(4)}</strong>
                  </div>
                  <div>
                    <span>ood score</span>
                    <strong>{result.decision.oodScore?.toFixed?.(4) ?? "未返回"}</strong>
                  </div>
                </div>
                <div className="reason-box">
                  <strong>触发原因</strong>
                  <span>
                    {result.decision.reasons.length > 0
                      ? result.decision.reasons.map(reasonLabel).join("，")
                      : "API 未返回阈值原因"}
                  </span>
                </div>
                <div className="route-box">
                  <div>
                    <strong>{result.reviewItemId ? "已创建人工复核项" : "未进入人工复核队列"}</strong>
                    <div className="row-meta">
                      {result.reviewItemId
                        ? `${result.reviewItemId} · 可直接打开处理。`
                        : "accept 结果只记录 inference event；需要抽检时可后续增加 accept audit 策略。"}
                    </div>
                  </div>
                  {result.reviewItemId ? (
                    <Link
                      className="primary-button"
                      to={`/review/${result.reviewItemId}?status=pending&dataset_id=${encodeURIComponent(result.datasetId ?? "")}`}
                    >
                      <Icon name="UserCheck" size={16} />
                      打开复核项
                    </Link>
                  ) : (
                    <StatusChip tone="default">已记录事件</StatusChip>
                  )}
                </div>
              </div>
              <div className="inference-candidates section-gap-small">
                <h3>
                  候选类别 <span>Top {result.topK.length}</span>
                </h3>
                {result.topK.length > 0 ? (
                  result.topK.map((candidate, index) => (
                    <CandidateBar
                      label={candidate.label}
                      score={candidate.score}
                      fill={index === 0 ? "#0891b2" : index === 1 ? "#a15c07" : "#315fbd"}
                      key={`${candidate.label}-${index}`}
                    />
                  ))
                ) : (
                  <div className="row-meta">API 未返回候选类别。</div>
                )}
              </div>
              <details className="evidence-details section-gap-small">
                <summary>
                  <span>
                    <Icon name="GitCompare" size={16} />
                    高级证据：近邻样本
                  </span>
                  <StatusChip tone="neutral">{result.nearestNeighbors.length} 条</StatusChip>
                </summary>
                <div className="neighbor-list">
                  {result.nearestNeighbors.length > 0 ? (
                    result.nearestNeighbors.map((neighbor, index) => (
                      <div className="neighbor-row" key={neighbor.sampleId || `${neighbor.label}-${index}`}>
                        <div className="neighbor-rank">{index + 1}</div>
                        <div>
                          <strong>{neighbor.label}</strong>
                          <div className="row-meta">{neighbor.sampleId || "unknown sample"}</div>
                        </div>
                        <div className="neighbor-distance">
                          <span>distance</span>
                          <strong>{neighbor.distance?.toFixed(4) ?? "未返回"}</strong>
                        </div>
                      </div>
                    ))
                  ) : (
                    <div className="empty-evidence">
                      <Icon name="ImageOff" size={18} />
                      <span>暂无近邻证据。当前结果仍可基于 top-k 和阈值原因判断。</span>
                    </div>
                  )}
                </div>
              </details>
              <LLMAssistanceBox
                title="LLM 推理解释"
                caption="只解释当前推理证据，不改变 inference event 或复核路由。"
                assistance={llm.assistance}
                status={llm.status}
                error={llm.error}
                onGenerate={handleGenerateInferenceExplanation}
              />
              <TechnicalDetails>
                run: {displayValue(result.inferenceRunId)}
                <br />
                event: {displayValue(result.inferenceEventId)}
                <br />
                model: {result.modelVersionId}
                <br />
                strategy: {displayValue(result.thresholdStrategyId)}
                <br />
                feature: {displayValue(result.featureArtifactId)}
              </TechnicalDetails>
            </>
          )}
        </Panel>
      </div>
    </div>
  );
}

function readFileAsDataUrl(file) {
  return new Promise((resolve) => {
    const reader = new FileReader();
    reader.addEventListener("load", () => resolve(typeof reader.result === "string" ? reader.result : null));
    reader.addEventListener("error", () => resolve(null));
    reader.readAsDataURL(file);
  });
}
