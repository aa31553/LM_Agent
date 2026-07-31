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
    "boxplot",
    "heatmap",
    "control_chart",
    "spec_capability",
  ]);

  function assertChart(chart) {
    if (!chart || typeof chart !== "object") {
      throw new TypeError("chart must be an object");
    }
    const chartType = chart.chart_type || chart.type;
    if (
      chart.schema_version === "3.0" &&
      new Set(["pareto", "boxplot", "heatmap", "control_chart", "spec_capability"]).has(
        chart.semantic_type,
      )
    ) {
      if (!chart.fields || typeof chart.fields !== "object") {
        throw new TypeError("Semantic chart fields are required");
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
    if (chart.schema_version === "3.0" && chart.semantic_type === "boxplot") {
      return toBoxplotOption(chart);
    }
    if (chart.schema_version === "3.0" && chart.semantic_type === "heatmap") {
      return toHeatmapOption(chart);
    }
    if (chart.schema_version === "3.0" && chart.semantic_type === "control_chart") {
      return toControlChartOption(chart);
    }
    if (chart.schema_version === "3.0" && chart.semantic_type === "spec_capability") {
      return toCapabilityOption(chart);
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

  function toBoxplotOption(chart) {
    const fields = chart.fields;
    const groups = chart.data.map((row) => row?.[fields.group]);
    const outliers = [];
    chart.data.forEach((row, groupIndex) => {
      (row?.[fields.outliers] || []).forEach((value) => {
        outliers.push([groupIndex, value]);
      });
    });
    return {
      title: { text: chart.title || "Boxplot", left: "center" },
      tooltip: { trigger: "item" },
      grid: { left: 56, right: 28, top: 64, bottom: 48, containLabel: true },
      xAxis: { type: "category", data: groups, boundaryGap: true },
      yAxis: { type: "value", scale: true },
      series: [
        {
          name: "distribution",
          type: "boxplot",
          data: chart.data.map((row) => [
            row?.[fields.min],
            row?.[fields.q1],
            row?.[fields.median],
            row?.[fields.q3],
            row?.[fields.max],
          ]),
        },
        { name: "outliers", type: "scatter", data: outliers },
      ],
      toolbox: {
        right: 12,
        feature: { dataView: { readOnly: true }, restore: {}, saveAsImage: {} },
      },
    };
  }

  function toHeatmapOption(chart) {
    const fields = chart.fields;
    const xCategories = [...new Set(chart.data.map((row) => row?.[fields.x]))];
    const yCategories = [...new Set(chart.data.map((row) => row?.[fields.y]))];
    const values = chart.data
      .map((row) => row?.[fields.value])
      .filter((value) => typeof value === "number");
    const absoluteMax = Math.max(1, ...values.map((value) => Math.abs(value)));
    return {
      title: { text: chart.title || "Heatmap", left: "center" },
      tooltip: { position: "top" },
      grid: { left: 72, right: 72, top: 64, bottom: 72, containLabel: true },
      xAxis: { type: "category", data: xCategories, splitArea: { show: true } },
      yAxis: { type: "category", data: yCategories, splitArea: { show: true } },
      visualMap: {
        min: -absoluteMax,
        max: absoluteMax,
        calculable: true,
        orient: "horizontal",
        left: "center",
        bottom: 8,
      },
      series: [
        {
          name: fields.value,
          type: "heatmap",
          data: chart.data.map((row) => [
            xCategories.indexOf(row?.[fields.x]),
            yCategories.indexOf(row?.[fields.y]),
            row?.[fields.value],
          ]),
          label: { show: chart.data.length <= 100 },
        },
      ],
      toolbox: { right: 12, feature: { saveAsImage: {} } },
    };
  }

  function toControlChartOption(chart) {
    const fields = chart.fields;
    const periods = chart.data.map((row) => row?.[fields.period]);
    const series = [
      [fields.value, "solid"],
      [fields.center_line, "dashed"],
      [fields.ucl, "dashed"],
      [fields.lcl, "dashed"],
    ].map(([field, lineType]) => ({
      name: field,
      type: "line",
      showSymbol: chart.data.length <= 200,
      lineStyle: { type: lineType },
      data: chart.data.map((row) => row?.[field]),
    }));
    return {
      title: { text: chart.title || "Control chart", left: "center" },
      tooltip: { trigger: "axis" },
      legend: { top: 30, data: series.map((item) => item.name) },
      grid: { left: 56, right: 28, top: 68, bottom: 56, containLabel: true },
      xAxis: { type: "category", data: periods, axisLabel: { hideOverlap: true } },
      yAxis: { type: "value", scale: true },
      series,
      dataZoom:
        chart.data.length > 30
          ? [
              { type: "inside", xAxisIndex: 0 },
              { type: "slider", xAxisIndex: 0, bottom: 14 },
            ]
          : undefined,
      toolbox: {
        right: 12,
        feature: { dataView: { readOnly: true }, restore: {}, saveAsImage: {} },
      },
    };
  }

  function toCapabilityOption(chart) {
    const row = chart.data[0] || {};
    const fields = chart.fields;
    const names = ["cp", "cpk", "pp", "ppk"];
    return {
      title: { text: chart.title || "Process capability", left: "center" },
      tooltip: { trigger: "axis" },
      grid: { left: 56, right: 28, top: 64, bottom: 48, containLabel: true },
      xAxis: { type: "category", data: names.map((name) => name.toUpperCase()) },
      yAxis: { type: "value", min: 0 },
      series: [
        {
          name: "capability",
          type: "bar",
          data: names.map((name) => row?.[fields[name]]),
          markLine: { data: [{ yAxis: 1, name: "1.0" }] },
        },
      ],
      toolbox: {
        right: 12,
        feature: { dataView: { readOnly: true }, restore: {}, saveAsImage: {} },
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
