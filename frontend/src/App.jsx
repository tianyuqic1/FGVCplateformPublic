import { lazy, Suspense, useEffect, useState } from "react";
import { Navigate, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { AppShell } from "./components/AppShell.jsx";
import { Icon } from "./components/icons.jsx";
import { AdminUsersPage } from "./auth/AdminUsersPage.jsx";
import { AuthPage } from "./auth/AuthPage.jsx";
import { canAccessPath, homeForRole, useAuth } from "./auth/AuthContext.jsx";

function lazyNamed(loader, name) {
  return lazy(() => loader().then((module) => ({ default: module[name] })));
}

const DashboardPage = lazyNamed(() => import("./features/dashboard/DashboardPage.jsx"), "DashboardPage");
const DatasetsPage = lazyNamed(() => import("./features/datasets/DatasetsPage.jsx"), "DatasetsPage");
const DatasetDetailPage = lazyNamed(() => import("./features/datasets/DatasetDetailPage.jsx"), "DatasetDetailPage");
const AnnotationPage = lazyNamed(() => import("./features/annotation/AnnotationPage.jsx"), "AnnotationPage");
const InferencePage = lazyNamed(() => import("./features/inference/InferencePage.jsx"), "InferencePage");
const HardwarePage = lazyNamed(() => import("./features/hardware/HardwarePage.jsx"), "HardwarePage");
const WorkersPage = lazyNamed(() => import("./features/workers/WorkersPage.jsx"), "WorkersPage");
const TrainingPage = lazyNamed(() => import("./features/training/TrainingPages.jsx"), "TrainingPage");
const TrainingDetailPage = lazyNamed(() => import("./features/training/TrainingPages.jsx"), "TrainingDetailPage");
const ModelVersionsPage = lazyNamed(() => import("./features/model-versions/ModelVersionPages.jsx"), "ModelVersionsPage");
const ModelComparisonPage = lazyNamed(() => import("./features/model-versions/ModelVersionPages.jsx"), "ModelComparisonPage");
const ModelVersionDetailPage = lazyNamed(() => import("./features/model-versions/ModelVersionPages.jsx"), "ModelVersionDetailPage");
const PipelinesPage = lazyNamed(() => import("./features/pipelines/PipelinePages.jsx"), "PipelinesPage");
const PipelineRunPage = lazyNamed(() => import("./features/pipelines/PipelinePages.jsx"), "PipelineRunPage");
const WeightManagementPage = lazyNamed(() => import("./features/weights/WeightManagementPage.jsx"), "WeightManagementPage");
const FeedbackPage = lazyNamed(() => import("./features/review/ReviewPages.jsx"), "FeedbackPage");
const ReviewDetailPage = lazyNamed(() => import("./features/review/ReviewPages.jsx"), "ReviewDetailPage");
const ReviewPage = lazyNamed(() => import("./features/review/ReviewPages.jsx"), "ReviewPage");

const legacyPageMap = {
  hardware: "/hardware",
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
  if (pathname.startsWith("/workers")) return "Worker 状态";
  if (pathname === "/admin/users") return "用户管理";
  if (pathname === "/annotation") return "AI 辅助标注";
  if (pathname.startsWith("/datasets/")) return "数据集详情";
  if (pathname === "/hardware") return "计算资源";
  if (pathname === "/datasets") return "数据集";
  if (pathname.startsWith("/training/")) return "训练详情";
  if (pathname === "/training") return "训练任务";
  if (pathname === "/inference") return "模型推理";
  if (pathname === "/weights") return "预训练权重";
  if (pathname.startsWith("/review/")) return "复核详情";
  if (pathname === "/review") return "人工复核";
  if (pathname === "/feedback") return "反馈池";
  if (pathname === "/models/compare") return "模型性能对比";
  if (pathname.startsWith("/models/")) return "模型详情";
  if (pathname === "/models") return "模型版本";
  if (pathname.startsWith("/pipelines/")) return "流水线任务详情";
  if (pathname === "/pipelines") return "任务流水线";
  return "工作台";
}

function crumbForPath(pathname) {
  if (pathname.startsWith("/workers")) return "系统 / Worker 状态";
  if (pathname === "/admin/users") return "系统 / 用户与角色";
  if (pathname === "/annotation") return "数据资产 / AI 辅助标注工作区";
  if (pathname.startsWith("/datasets/")) return "数据集 / 版本详情";
  if (pathname === "/hardware") return "系统 / 计算资源";
  if (pathname === "/datasets") return "数据资产";
  if (pathname.startsWith("/training/")) return "训练 / 运行详情";
  if (pathname === "/training") return "训练";
  if (pathname === "/inference") return "推理";
  if (pathname === "/weights") return "模型权重";
  if (pathname.startsWith("/review/")) return "复核 / 样本详情";
  if (pathname === "/review") return "复核队列";
  if (pathname === "/feedback") return "复核 / 反馈池";
  if (pathname === "/models/compare") return "模型 / 性能对比";
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

function RouteFallback() {
  return (
    <div className="route-fallback" role="status" aria-live="polite">
      <Icon name="LoaderCircle" size={18} />
      <span>正在加载页面…</span>
    </div>
  );
}

function DiagnosticToast() {
  const [diagnostic, setDiagnostic] = useState(null);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    const receive = (event) => {
      setCopied(false);
      setDiagnostic(event.detail);
    };
    window.addEventListener("finevision:diagnostic-error", receive);
    return () => window.removeEventListener("finevision:diagnostic-error", receive);
  }, []);

  if (!diagnostic) return null;
  async function copyRequestId() {
    try {
      await navigator.clipboard.writeText(diagnostic.requestId);
      setCopied(true);
    } catch {
      setCopied(false);
    }
  }
  return (
    <div className="diagnostic-toast" role="alert">
      <Icon name="TriangleAlert" size={19} />
      <div>
        <strong>请求未完成</strong>
        <span>{diagnostic.method} {diagnostic.path} · 诊断编号 {diagnostic.requestId}</span>
      </div>
      <button type="button" className="diagnostic-copy" onClick={copyRequestId}>{copied ? "已复制" : "复制编号"}</button>
      <button type="button" className="diagnostic-close" aria-label="关闭诊断提示" onClick={() => setDiagnostic(null)}>×</button>
    </div>
  );
}

function DatasetTitleRoute({ showToast }) {
  return <DatasetDetailPage showToast={showToast} />;
}

export default function App() {
  const location = useLocation();
  const { user, status } = useAuth();
  const [toast, setToast] = useState("");
  const title = titleForPath(location.pathname);
  const crumb = crumbForPath(location.pathname);

  function showToast(message) {
    setToast(message);
    window.clearTimeout(showToast.timer);
    showToast.timer = window.setTimeout(() => setToast(""), 2200);
  }

  if (status === "loading") return <div className="route-fallback" role="status">正在验证登录状态…</div>;
  if (location.pathname === "/login" || location.pathname === "/register") {
    const requested = location.state?.from;
    const destination = typeof requested === "string" && requested.startsWith("/") && canAccessPath(user?.role, requested) ? requested : homeForRole(user?.role);
    return user ? <Navigate to={destination} replace /> : <AuthPage mode={location.pathname === "/register" ? "register" : "login"} />;
  }
  if (!user) return <Navigate to="/login" replace state={{ from: location.pathname + location.search }} />;
  if (location.pathname === "/" && user.role === "annotator") return <Navigate to="/annotation" replace />;
  if (!canAccessPath(user.role, location.pathname)) {
    return <main className="fv-forbidden"><h1>此页面不在你的工作权限内</h1><p>如需访问，请联系平台管理员调整账号角色。</p><button type="button" onClick={() => window.location.assign(homeForRole(user.role))}>返回工作区</button></main>;
  }

  return (
    <>
      <LegacyRouteBridge />
      <AppShell title={title} crumb={crumb}>
        <Suspense fallback={<RouteFallback />}>
          <Routes>
            <Route path="/" element={<DashboardPage />} />
            <Route path="/dashboard" element={<Navigate to="/" replace />} />
            <Route path="/datasets" element={<DatasetsPage showToast={showToast} />} />
            <Route path="/annotation" element={<AnnotationPage />} />
            <Route path="/datasets/:datasetId" element={<DatasetTitleRoute showToast={showToast} />} />
            <Route path="/training" element={<TrainingPage showToast={showToast} />} />
            <Route path="/training/:runId" element={<TrainingDetailPage showToast={showToast} />} />
            <Route path="/inference" element={<InferencePage showToast={showToast} />} />
            <Route path="/hardware" element={<HardwarePage />} />
            <Route path="/workers" element={<WorkersPage />} />
            <Route path="/workers/:workerId" element={<WorkersPage />} />
            <Route path="/admin/users" element={<AdminUsersPage />} />
            <Route path="/weights" element={<WeightManagementPage showToast={showToast} />} />
            <Route path="/review" element={<ReviewPage />} />
            <Route path="/review/:reviewItemId" element={<ReviewDetailPage showToast={showToast} />} />
            <Route path="/feedback" element={<FeedbackPage showToast={showToast} />} />
            <Route path="/models" element={<ModelVersionsPage />} />
            <Route path="/models/compare" element={<ModelComparisonPage />} />
            <Route path="/models/:modelId" element={<ModelVersionDetailPage showToast={showToast} />} />
            <Route path="/pipelines" element={<PipelinesPage />} />
            <Route path="/pipelines/:pipelineRunId" element={<PipelineRunPage showToast={showToast} />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </Suspense>
      </AppShell>
      <Toast message={toast} />
      <DiagnosticToast />
    </>
  );
}
