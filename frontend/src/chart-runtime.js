import { assembleECharts } from "flint-chart/echarts";
import { supportsFlint, toFlintInput } from "./lm-chart-to-flint.js";

function compile(chart, size) {
  const option = assembleECharts(toFlintInput(chart, size));
  if (chart.title) option.title = { ...(option.title || {}), text: chart.title, left: "center" };
  if (chart.interaction?.zoom && option.xAxis?.type === "category") {
    option.dataZoom = [{ type: "inside", xAxisIndex: 0 }, { type: "slider", xAxisIndex: 0 }];
  }
  return option;
}

function render(container, chart, runtime = globalThis.echarts) {
  if (!container) throw new TypeError("A chart container is required");
  if (!runtime) throw new Error("ECharts runtime is not loaded");
  const option = compile(chart, {
    width: container.clientWidth || 640,
    height: container.clientHeight || 420,
  });
  const instance = runtime.getInstanceByDom(container) || runtime.init(container);
  instance.setOption(option, true);
  return { instance, warnings: option._warnings || [] };
}

const api = Object.freeze({ compile, render, supportsFlint, toFlintInput });
globalThis.LMFlintCharts = api;
export { compile, render, supportsFlint, toFlintInput };

