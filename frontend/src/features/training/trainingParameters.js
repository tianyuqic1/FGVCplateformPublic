export const resolutions = [224, 256, 320, 384, 448, 512];
export const augmentationOptions = [
  ["random_resized_crop", "随机裁剪", "保留 70%–100% 面积，缩放到输入尺寸"],
  ["horizontal_flip", "水平翻转", "50% 概率左右翻转"],
  ["vertical_flip", "垂直翻转", "50% 概率上下翻转，注意物体方向"],
  ["color_jitter", "颜色扰动", "轻度调整亮度、对比度、饱和度与色相"],
  ["random_rotation", "随机旋转", "−15° 至 +15° 小角度旋转"],
  ["random_erasing", "随机擦除", "25% 概率遮挡 2%–15% 图像面积"],
];

export function trainingHeadConfig(form) {
  const dino = form.backboneKey.startsWith("dinov3_");
  return {
    head_type: "image_classifier_v2", epochs: Number(form.epochs), batch_size: Number(form.batchSize),
    lora_enabled: dino && form.loraEnabled, lora_rank: Number(form.loraRank),
    head_learning_rate: Number(form.headLearningRate),
    ...(!dino ? { backbone_learning_rate: Number(form.backboneLearningRate) } : form.loraEnabled ? { lora_learning_rate: Number(form.loraLearningRate) } : {}),
    augmentations: { ...form.augmentations },
  };
}

export function trainingParameterRows(run) {
  const config = run.headConfig || {};
  const modern = config.head_type === "image_classifier_v2";
  const full = config.training_mode === "full" || run.backboneId?.startsWith("imagenet_");
  const rate = key => config[key] ?? (modern ? config.learning_rate : null) ?? "未记录";
  return [
    ["输入分辨率", run.imageSize ? `${run.imageSize} × ${run.imageSize}` : "未记录"],
    ["训练轮数", config.epochs ?? "未记录"], ["图片批大小", config.batch_size ?? "未记录"],
    ["分类头学习率", rate("head_learning_rate")],
    ...(modern && full ? [["骨干学习率", rate("backbone_learning_rate")]] : []),
    ...(modern && config.lora_enabled ? [["LoRA 学习率", rate("lora_learning_rate")], ["LoRA Rank", config.lora_rank ?? "未记录"]] : []),
    ["权重衰减", config.weight_decay ?? "未记录"],
    ["训练集增强", config.augmentations ? augmentationOptions.filter(([key]) => config.augmentations[key]).map(([, label]) => label).join("、") || "未启用" : "未记录（旧版图像训练默认无增强）"],
  ];
}
