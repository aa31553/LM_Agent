(function attachAnalysisChartAdapter(root, factory) {
  const adapter = factory();
  if (typeof module === "object" && module.exports) {
    module.exports = adapter;
  }
  if (root) {
    root.LMAnalysisCharts = adapter;
  }
})(typeof globalThis !== "undefined" ? globalThis : this, function createAdapter() {
  "use strict";

  const SUPPORTED_TYPES = new Set(["bar", "line", "scatter"]);

  function assertChart(chart) {
    if (!chart || typeof chart !== "object") {
      throw new TypeError("chart must be an object");
    }
    if (!SUPPORTED_TYPES.has(chart.type)) {
      throw new TypeError(`Unsupported chart type: ${String(chart.type)}`);
    }
    if (!chart.x_field || !chart.y_field) {
      throw new TypeError("chart.x_field and chart.y_field are required");
    }
    if (!Array.isArray(chart.data)) {
      throw new TypeError("chart.data must be an array");
    }
  }

  function numericAxis(values) {
    const populated = values.filter((value) => value !== null && value !== undefined);
    return populated.length > 0 && populated.every((value) => typeof value === "number");
  }

  function toEChartsOption(chart) {
    assertChart(chart);
    const xValues = chart.data.map((row) => row?.[chart.x_field]);
    const scatterHasNumericX = chart.type === "scatter" && numericAxis(xValues);
    const dimensions = [chart.x_field, chart.y_field];
    const option = {
      animation: chart.data.length <= 2000,
      title: {
        text: chart.title || "Analysis result",
        left: "center",
      },
      tooltip: {
        trigger: chart.type === "scatter" ? "item" : "axis",
      },
      grid: {
        left: 56,
        right: 28,
        top: 64,
        bottom: chart.data.length > 30 ? 72 : 48,
        containLabel: true,
      },
      dataset: {
        dimensions,
        source: chart.data,
      },
      xAxis: {
        type: scatterHasNumericX ? "value" : "category",
        name: chart.x_field,
        axisLabel: {
          hideOverlap: true,
        },
      },
      yAxis: {
        type: "value",
        name: chart.y_field,
        scale: true,
      },
      series: [
        {
          name: chart.y_field,
          type: chart.type,
          encode: {
            x: chart.x_field,
            y: chart.y_field,
            tooltip: dimensions,
          },
          showSymbol: chart.type !== "line" || chart.data.length <= 200,
          large: chart.type !== "line" && chart.data.length > 2000,
        },
      ],
      toolbox: {
        right: 12,
        feature: {
          dataView: { readOnly: true },
          restore: {},
          saveAsImage: {},
        },
      },
    };
    if (chart.data.length > 30 && !scatterHasNumericX) {
      option.dataZoom = [
        { type: "inside", xAxisIndex: 0 },
        { type: "slider", xAxisIndex: 0, bottom: 14 },
      ];
    }
    return option;
  }

  function render(container, chart, runtime) {
    if (!container) {
      throw new TypeError("A chart container is required");
    }
    const echartsRuntime = runtime || globalThis.echarts;
    if (!echartsRuntime) {
      throw new Error("ECharts runtime is not loaded");
    }
    const instance =
      echartsRuntime.getInstanceByDom(container) || echartsRuntime.init(container);
    instance.setOption(toEChartsOption(chart), true);
    return instance;
  }

  return Object.freeze({
    supportedTypes: Object.freeze([...SUPPORTED_TYPES]),
    toEChartsOption,
    render,
  });
});
