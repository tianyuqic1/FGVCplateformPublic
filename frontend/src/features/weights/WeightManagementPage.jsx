import { PageHero } from "../../components/AppShell.jsx";
import { Icon } from "../../components/icons.jsx";
import { GateRow, MetricCard, Panel, StatusChip } from "../../components/ui.jsx";
import { useModelWeights } from "../../hooks/useModelWeights.js";
import { formatBytes, modelWeightDetails, modelWeightLabel, modelWeightTone } from "./presentation.js";

function extractorLabel(extractor) {
  if (["dinov3_vits", "dinov3_vits16_lvd1689m"].includes(extractor)) return "ViT-S · DINOv3";
  if (["imagenet_vits", "imagenet_vits16_augreg_in21k_ft_in1k"].includes(extractor)) return "ViT-S · ImageNet";
  if (["imagenet_resnet50", "imagenet_resnet50_a1_in1k"].includes(extractor)) return "ResNet-50 · ImageNet";
  return extractor;
}

function usageLabel(extractor) {
  if (["dinov3_vits", "dinov3_vits16_lvd1689m"].includes(extractor)) return "DINOv3 自监督 ViT-S 基线。";
  if (["imagenet_vits", "imagenet_vits16_augreg_in21k_ft_in1k"].includes(extractor))
    return "ImageNet-21K 预训练并在 ImageNet-1K 微调的 ViT-S。";
  if (["imagenet_resnet50", "imagenet_resnet50_a1_in1k"].includes(extractor))
    return "ImageNet-1K 监督预训练 ResNet-50 对照基线。";
  return "仅支持 manifest 已登记的预训练权重。";
}

function TechnicalDetails({ summary = "技术详情", children }) {
  return (
    <details className="technical-details">
      <summary>{summary}</summary>
      <code>{children}</code>
    </details>
  );
}

export function WeightManagementPage() {
  const { weights, source, loading, error, refresh } = useModelWeights();
  const managedCount = weights.filter((weight) => weight.state === "managed").length;
  const cacheUnreported =
    managedCount > 0 ||
    loading ||
    source !== "api" ||
    weights.length === 0 ||
    weights.some((weight) => !["cached", "partial", "missing"].includes(weight.state));
  const totalCachedBytes = weights.reduce(
    (sum, weight) => sum + (weight.state === "cached" ? weight.cacheBytes : 0),
    0,
  );
  const cachedCount = weights.filter((weight) => weight.state === "cached").length;
  const partialCount = weights.filter((weight) => weight.state === "partial").length;
  const sourceLabel = loading ? "正在读取权重目录" : source === "api" ? "权重目录已同步" : "权重目录暂不可用";
  return (
    <>
      <PageHero
        title="权重管理"
        description="查看 DINOv3 ViT-S、ImageNet ViT-S 与 ImageNet ResNet-50 权重登记信息及已上报的缓存状态。"
        actions={
          <button type="button" className="ghost-button" onClick={() => refresh()} disabled={loading}>
            <Icon name="RefreshCw" size={16} />
            刷新
          </button>
        }
      />
      <div className="grid metrics">
        <MetricCard
          title="受管权重"
          value={`${managedCount}/${weights.length}`}
          caption={sourceLabel}
          fill="#0f766e"
          percent={weights.length ? (managedCount / weights.length) * 100 : 0}
          icon="HardDrive"
        />
        <MetricCard
          title="已上报缓存"
          value={cacheUnreported ? "未上报" : `${cachedCount}`}
          caption={managedCount ? "当前接口不检查计算节点缓存" : sourceLabel}
          fill="#315fbd"
          percent={0}
          icon="DatabaseZap"
        />
        <MetricCard
          title="已上报缓存体积"
          value={cacheUnreported ? "—" : formatBytes(totalCachedBytes)}
          caption="仅统计明确上报的本地缓存"
          fill="#0f766e"
          percent={0}
          icon="HardDrive"
        />
        <MetricCard
          title="未完成下载"
          value={cacheUnreported ? "未上报" : `${partialCount}`}
          caption="仅统计明确上报的下载状态"
          fill="#a15c07"
          percent={0}
          icon="LoaderCircle"
        />
      </div>
      <div className="grid two section-gap">
        <Panel title="预训练权重" caption="已纳入管理表示目录登记，不代表实时存储校验或计算节点缓存状态。">
          {error && (
            <div className="route-box">
              <strong>权重服务不可用</strong>
              <div className="row-meta">{error.message}</div>
            </div>
          )}
          <div className="timeline">
            {weights.map((weight) => (
              <div className="timeline-item" key={weight.extractor}>
                <div className="timeline-icon">
                  <Icon name="HardDrive" size={18} />
                </div>
                <div>
                  <strong>
                    {extractorLabel(weight.extractor)} · {weight.backboneId}
                  </strong>
                  <div className="row-meta">
                    {weight.modelName} · {modelWeightDetails(weight)}
                  </div>
                  <div className="row-meta">{weight.description || usageLabel(weight.extractor)}</div>
                </div>
                <div className="queue-row-actions">
                  <StatusChip tone={modelWeightTone(weight.state ?? "missing")}>
                    {modelWeightLabel(weight.state ?? "missing")}
                  </StatusChip>
                </div>
              </div>
            ))}
            {!loading && weights.length === 0 && (
              <div className="timeline-item">
                <div className="timeline-icon">
                  <Icon name="AlertTriangle" size={18} />
                </div>
                <div>
                  <strong>没有权重记录</strong>
                  <div className="row-meta">请确认控制面已启动。</div>
                </div>
                <StatusChip tone="warn">空</StatusChip>
              </div>
            )}
          </div>
        </Panel>
        <Panel title="权重说明" caption="预训练权重用于初始化骨干；训练结果与发布产物单独管理。">
          <div className="timeline">
            <GateRow
              title="预训练权重"
              description="通过 Git LFS 发布并提升到 MinIO 的受管 backbone 参数；这个页面管理的是它。"
              result="pass"
            />
            <GateRow
              title="当前训练策略"
              description="DINOv3 冻结骨干，可选 LoRA r=8/16；ImageNet ViT-S / ResNet-50 更新全部参数。图片直接进入分类模型，不做离线特征提取。"
              result="pending"
            />
            <GateRow
              title="训练与发布产物"
              description="训练保存完整模型检查点；发布时导出完整图片分类 ONNX，并登记校验和、评估报告与阈值策略。"
              result="pending"
            />
          </div>
          <TechnicalDetails>
            training_input: 图片直接输入分类模型
            <br />
            image_size_default: 224（可在训练任务中调整）
            <br />
            integrity: sha256 + size
            <br />
            runtime_source: MinIO content-addressed object
          </TechnicalDetails>
          {weights[0]?.cacheDir && (
            <TechnicalDetails summary="权重存储路径">
              cache_root_hint: {weights[0].cacheDir.replace(/\/models--timm--.*/, "")}
            </TechnicalDetails>
          )}
        </Panel>
      </div>
    </>
  );
}
