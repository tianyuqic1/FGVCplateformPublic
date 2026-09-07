const grid = { top: 34, right: 18, bottom: 50, left: 52, containLabel: false };

function seriesFor(points, name, label, color) {
  return {
    name: label,
    type: "line",
    smooth: 0.22,
    symbol: "circle",
    symbolSize: 5,
    showSymbol: points.length < 36,
    data: points.filter((point) => point.name === name).map((point) => [point.step, point.value]),
    lineStyle: { color, width: 2.2 },
    itemStyle: { color },
    emphasis: { focus: "series" },
  };
}

function baseOption(yAxisName) {
  return {
    animationDuration: 260,
    backgroundColor: "transparent",
    color: ["#3569e8", "#6258d8"],
    grid,
    tooltip: { trigger: "axis", backgroundColor: "#17233a", borderWidth: 0, textStyle: { color: "#fff" } },
    xAxis: {
      type: "value",
      minInterval: 1,
      name: "Epoch",
      nameTextStyle: { color: "#8590a2" },
      axisLine: { lineStyle: { color: "#cfd6e2" } },
      axisLabel: { color: "#778399" },
      splitLine: { lineStyle: { color: "#edf0f5" } },
    },
    yAxis: {
      type: "value",
      name: yAxisName,
      scale: true,
      nameTextStyle: { color: "#8590a2" },
      axisLabel: { color: "#778399" },
      splitLine: { lineStyle: { color: "#e8ecf2" } },
    },
    dataZoom: [{ type: "inside" }, { type: "slider", height: 14, bottom: 6, borderColor: "transparent" }],
  };
}

export function lossChartOption(points) {
  return { ...baseOption("Loss"), series: [seriesFor(points, "train_loss", "训练损失", "#3569e8")] };
}

export function accuracyChartOption(points) {
  return {
    ...baseOption("Accuracy"),
    yAxis: { ...baseOption("Accuracy").yAxis, min: 0, max: 1, axisLabel: { color: "#778399", formatter: (value) => `${Math.round(value * 100)}%` } },
    series: [seriesFor(points, "eval_accuracy", "验证准确率", "#6258d8")],
  };
}
