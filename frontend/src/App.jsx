import { useEffect, useState } from "react";
import { Navigate, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { AppShell } from "./components/AppShell.jsx";
import { Icon } from "./components/icons.jsx";
import {
  DashboardPage,
  DatasetDetailPage,
  DatasetsPage,
  InferencePage,
  FeedbackPage,
  ModelDetailPage,
  ModelsPage,
  PipelineRunPage,
  PipelinesPage,
  ReviewDetailPage,
  ReviewPage,
  TrainingDetailPage,
  TrainingPage,
  WeightManagementPage,
} from "./pages/pages.jsx";

const legacyPageMap = {
  dashboard: "/",
  datasets: "/datasets",
  "dataset-detail": ({ id = "bird", tab }) => `/datasets/${id}${tab ? `?tab=${tab}` : ""}`,
  training: "/training",
  "training-detail": ({ id = "run-042" }) => `/training/${id}`,
  inference: "/inference",
  weights: "/weights",
  review: "/review",
  "review-detail": ({ id }) => (id ? `/review/${id}` : "/review"),
  feedback: "/feedback",
  models: "/models",
  "model-detail": ({ id }) => (id ? `/models/${id}` : "/models"),
  pipelines: "/pipelines",
  "pipeline-run": ({ id }) => (id ? `/pipelines/${id}` : "/pipelines"),
};

const legacyAliases = {
  command: "dashboard",
  dataset: "datasets",
  modelops: "models",
  pipeline: "pipelines",
};

function titleForPath(pathname) {
  if (pathname.startsWith("/datasets/")) return "数据集详情";
  if (pathname === "/datasets") return "数据集";
  if (pathname.startsWith("/training/")) return "训练详情";
  if (pathname === "/training") return "训练任务";
  if (pathname === "/inference") return "推理实验室";
  if (pathname === "/weights") return "权重管理";
  if (pathname.startsWith("/review/")) return "复核详情";
  if (pathname === "/review") return "人工复核";
  if (pathname === "/feedback") return "反馈池";
  if (pathname.startsWith("/models/")) return "模型详情";
  if (pathname === "/models") return "模型版本";
  if (pathname.startsWith("/pipelines/")) return "流水线运行";
  if (pathname === "/pipelines") return "流水线";
  return "今日工作台";
}

function crumbForPath(pathname) {
  if (pathname.startsWith("/datasets/")) return "数据集 / 版本详情";
  if (pathname === "/datasets") return "数据资产";
  if (pathname.startsWith("/training/")) return "训练 / 运行详情";
  if (pathname === "/training") return "训练";
  if (pathname === "/inference") return "推理";
  if (pathname === "/weights") return "模型权重";
  if (pathname.startsWith("/review/")) return "复核 / 样本详情";
  if (pathname === "/review") return "复核队列";
  if (pathname === "/feedback") return "复核 / 反馈池";
  if (pathname.startsWith("/models/")) return "模型 / 版本详情";
  if (pathname === "/models") return "模型注册表";
  if (pathname.startsWith("/pipelines/")) return "流水线 / 运行详情";
  if (pathname === "/pipelines") return "编排";
  return "首页 / 工作台";
}

function LegacyRouteBridge() {
  const location = useLocation();
  const navigate = useNavigate();

  useEffect(() => {
    const params = new URLSearchParams(location.search);
    const pageParam = params.get("page") ?? legacyAliases[params.get("v")] ?? params.get("v");
    if (!pageParam) return;

    const target = legacyPageMap[pageParam];
    if (!target) return;
    const next = typeof target === "function" ? target({ id: params.get("id"), tab: params.get("tab") }) : target;
    navigate(next, { replace: true });
  }, [location.search, navigate]);

  return null;
}

function Toast({ message }) {
  return (
    <div className={`toast ${message ? "visible" : ""}`}>
      <Icon name="CheckCircle2" size={18} />
      <span>{message}</span>
    </div>
  );
}

function DatasetTitleRoute({ showToast }) {
  return <DatasetDetailPage showToast={showToast} />;
}

export default function App() {
  const location = useLocation();
  const [toast, setToast] = useState("");
  const title = titleForPath(location.pathname);
  const crumb = crumbForPath(location.pathname);

  function showToast(message) {
    setToast(message);
    window.clearTimeout(showToast.timer);
    showToast.timer = window.setTimeout(() => setToast(""), 2200);
  }

  return (
    <>
      <LegacyRouteBridge />
      <AppShell title={title} crumb={crumb} onToast={showToast}>
        <Routes>
          <Route path="/" element={<DashboardPage showToast={showToast} />} />
          <Route path="/dashboard" element={<Navigate to="/" replace />} />
          <Route path="/datasets" element={<DatasetsPage showToast={showToast} />} />
          <Route path="/datasets/:datasetId" element={<DatasetTitleRoute showToast={showToast} />} />
          <Route path="/training" element={<TrainingPage showToast={showToast} />} />
          <Route path="/training/:runId" element={<TrainingDetailPage showToast={showToast} />} />
          <Route path="/inference" element={<InferencePage showToast={showToast} />} />
          <Route path="/weights" element={<WeightManagementPage showToast={showToast} />} />
          <Route path="/review" element={<ReviewPage />} />
          <Route path="/review/:reviewItemId" element={<ReviewDetailPage showToast={showToast} />} />
          <Route path="/feedback" element={<FeedbackPage showToast={showToast} />} />
          <Route path="/models" element={<ModelsPage />} />
          <Route path="/models/:modelId" element={<ModelDetailPage showToast={showToast} />} />
          <Route path="/pipelines" element={<PipelinesPage />} />
          <Route path="/pipelines/:pipelineRunId" element={<PipelineRunPage showToast={showToast} />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </AppShell>
      <Toast message={toast} />
    </>
  );
}
