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
  const SUPPORTED_SEMANTICS = new Set([
    "histogram",
    "category_bar",
    "trend_line",
    "stacked_bar",
    "pareto",
    "data_quality",
    "missing_values",
  ]);

  function assertChart(chart) {
    if (!chart || typeof chart !== "object") {
      throw new TypeError("chart must be an object");
    }
    const chartType = chart.chart_type || chart.type;
    if (chart.schema_version === "3.0" && chart.semantic_type === "pareto") {
      if (!chart.fields?.category || !chart.fields?.bar || !chart.fields?.line) {
        throw new TypeError("Pareto chart fields are required");
      }
      if (!Array.isArray(chart.data)) {
        throw new TypeError("chart.data must be an array");
      }
      return;
    }
    if (!SUPPORTED_TYPES.has(chartType)) {
      throw new TypeError(`Unsupported chart type: ${String(chartType)}`);
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
    if (chart.schema_version === "3.0" && chart.semantic_type === "pareto") {
      return toParetoOption(chart);
    }
    const chartType = chart.chart_type || chart.type;
    const xValues = chart.data.map((row) => row?.[chart.x_field]);
    const scatterHasNumericX = chartType === "scatter" && numericAxis(xValues);
    const dimensions = [
      chart.x_field,
      chart.y_field,
      chart.series_field,
      ...(chart.tooltip_fields || []),
    ].filter((value, index, values) => value && values.indexOf(value) === index);
    const interaction = chart.interaction || {};
    const format = chart.format || {};
    const option = {
      animation: chart.data.length <= 2000,
      title: {
        text: chart.title || "Analysis result",
        left: "center",
      },
      tooltip: {
        trigger: chartType === "scatter" ? "item" : "axis",
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
        type:
          chart.x_type === "time"
            ? "time"
            : chart.x_type === "value" || scatterHasNumericX
              ? "value"
              : "category",
        name: chart.x_field,
        axisLabel: {
          hideOverlap: true,
        },
      },
      yAxis: {
        type: "value",
        name: format.y_unit
          ? `${chart.y_field} (${format.y_unit})`
          : chart.y_field,
        scale: true,
        axisLabel: format.y_unit
          ? { formatter: `{value}${format.y_unit}` }
          : undefined,
      },
      series: [],
      toolbox: {
        right: 12,
        feature: {
          dataView: { readOnly: true },
          restore: {},
          saveAsImage: {},
        },
      },
    };
    if (chart.series_field) {
      const names = [
        ...new Set(chart.data.map((row) => String(row?.[chart.series_field] ?? ""))),
      ];
      option.legend = { top: 30, data: names };
      delete option.dataset;
      option.series = names.map((name) => ({
        name,
        type: chartType,
        stack: chart.semantic_type === "stacked_bar" ? "total" : undefined,
        data: chart.data
          .filter((row) => String(row?.[chart.series_field] ?? "") === name)
          .map((row) => [row?.[chart.x_field], row?.[chart.y_field]]),
        showSymbol: chartType !== "line" || chart.data.length <= 200,
        large: chartType !== "line" && chart.data.length > 2000,
      }));
    } else {
      option.series = [
        {
          name: chart.y_field,
          type: chartType,
          encode: {
            x: chart.x_field,
            y: chart.y_field,
            tooltip: dimensions,
          },
          showSymbol: chartType !== "line" || chart.data.length <= 200,
          large: chartType !== "line" && chart.data.length > 2000,
        },
      ];
    }
    if (
      (interaction.zoom || chart.data.length > 30) &&
      option.xAxis.type === "category"
    ) {
      option.dataZoom = [
        { type: "inside", xAxisIndex: 0 },
        { type: "slider", xAxisIndex: 0, bottom: 14 },
      ];
    }
    return option;
  }

  function toParetoOption(chart) {
    const category = chart.fields.category;
    const bar = chart.fields.bar;
    const line = chart.fields.line;
    return {
      animation: chart.data.length <= 2000,
      title: { text: chart.title || "Pareto", left: "center" },
      tooltip: { trigger: "axis" },
      legend: { top: 30, data: [bar, line] },
      grid: {
        left: 56,
        right: 64,
        top: 68,
        bottom: chart.data.length > 30 ? 72 : 48,
        containLabel: true,
      },
      xAxis: {
        type: "category",
        data: chart.data.map((row) => row?.[category]),
        axisLabel: { hideOverlap: true },
      },
      yAxis: [
        { type: "value", name: bar, min: 0 },
        {
          type: "value",
          name: line,
          min: 0,
          max: 1,
          axisLabel: { formatter: (value) => `${Math.round(value * 100)}%` },
        },
      ],
      series: [
        {
          name: bar,
          type: "bar",
          data: chart.data.map((row) => row?.[bar]),
        },
        {
          name: line,
          type: "line",
          yAxisIndex: 1,
          showSymbol: chart.data.length <= 200,
          data: chart.data.map((row) => row?.[line]),
        },
      ],
      dataZoom:
        chart.data.length > 30
          ? [
              { type: "inside", xAxisIndex: 0 },
              { type: "slider", xAxisIndex: 0, bottom: 14 },
            ]
          : undefined,
      toolbox: {
        right: 12,
        feature: {
          dataView: { readOnly: true },
          restore: {},
          saveAsImage: {},
        },
      },
    };
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
    supportedSemanticTypes: Object.freeze([...SUPPORTED_SEMANTICS]),
    toEChartsOption,
    render,
  });
});
