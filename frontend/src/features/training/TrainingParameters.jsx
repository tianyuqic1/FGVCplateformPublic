import { augmentationOptions, resolutions } from "./trainingParameters.js";
import "./training-parameters.css";

export function TrainingParameters({ form, setForm }) {
  const isDino = form.backboneKey.startsWith("dinov3_");
  const update = (key, value) => setForm(current => ({ ...current, [key]: value }));
  const rates = [["headLearningRate", "分类头学习率"], ...(!isDino ? [["backboneLearningRate", "骨干学习率"]] : form.loraEnabled ? [["loraLearningRate", "LoRA 学习率"]] : [])];
  return <>
    <section className="fv-parameter-section"><header><strong>优化器参数</strong><small>AdamW · 独立参数组</small></header>
      <div className="fv-parameter-grid">{rates.map(([key, label]) => <label key={key}><span>{label}</span><input aria-label={label} type="number" required min="0.000000001" max="1" step="any" value={form[key]} onChange={e => update(key, e.target.value)} /></label>)}</div>
      <p>{isDino ? form.loraEnabled ? "骨干保持冻结，仅更新分类头与 LoRA A/B 矩阵。" : "骨干保持冻结，仅更新分类头。" : "骨干与分类头分别使用自己的学习率，全量更新。"} 支持科学计数法，如 1e-4。</p>
    </section>
    <section className="fv-parameter-section"><header><strong>输入分辨率</strong><small>正方形图像 · RGB</small></header>
      <select aria-label="输入图片分辨率" value={form.imageSize} onChange={e => update("imageSize", e.target.value)}>{resolutions.map(size => <option key={size} value={size}>{size} × {size}{size === 224 ? " · 标准" : size >= 384 ? " · 高分辨率" : ""}</option>)}</select>
      <p>更高分辨率会增加显存和计算开销，必要时降低批大小。验证、推理与发布模型沿用该尺寸。</p>
    </section>
    <section className="fv-parameter-section"><header><strong>数据增强</strong><small>仅训练集</small></header>
      <div className="fv-augmentation-grid">{augmentationOptions.map(([key, label, hint]) => <label className={form.augmentations[key] ? "is-selected" : ""} key={key}>
        <input type="checkbox" checked={Boolean(form.augmentations[key])} onChange={e => update("augmentations", { ...form.augmentations, [key]: e.target.checked })} /><span><strong>{label}</strong><small>{hint}</small></span>
      </label>)}</div>
      <p>验证集、测试集不应用随机增强。细粒度分类请谨慎使用颜色扰动、翻转与擦除，避免破坏类别线索。</p>
    </section>
  </>;
}
