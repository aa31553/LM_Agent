"use strict";

const assert = require("node:assert/strict");
const path = require("node:path");

const adapter = require(
  path.resolve(__dirname, "../../frontend/echarts-adapter.js"),
);

const chart = {
  schema_version: "1.0",
  type: "line",
  title: "Yield trend",
  x_field: "date",
  y_field: "yield",
  data: [
    { date: "2026-07-01", yield: 98.5 },
    { date: "2026-07-02", yield: 97.2 },
  ],
};

const option = adapter.toEChartsOption(chart);
assert.equal(option.title.text, "Yield trend");
assert.deepEqual(option.dataset.dimensions, ["date", "yield"]);
assert.equal(option.series[0].type, "line");
assert.deepEqual(option.series[0].encode.tooltip, ["date", "yield"]);
assert.equal(option.xAxis.type, "category");

const multiSeries = adapter.toEChartsOption({
  schema_version: "2.0",
  chart_type: "line",
  type: "line",
  title: "Yield by line",
  x_field: "month",
  y_field: "yield",
  series_field: "line",
  x_type: "time",
  format: { y_unit: "%", decimal_places: 2 },
  interaction: { zoom: true },
  data: [
    { month: "2026-07-01", yield: 98, line: "L1" },
    { month: "2026-07-01", yield: 94, line: "L2" },
  ],
});
assert.equal(multiSeries.xAxis.type, "time");
assert.equal(multiSeries.yAxis.name, "yield (%)");
assert.deepEqual(
  multiSeries.series.map((series) => series.name),
  ["L1", "L2"],
);

assert.throws(
  () => adapter.toEChartsOption({ ...chart, type: "pie" }),
  /Unsupported chart type/,
);

const pareto = adapter.toEChartsOption({
  schema_version: "3.0",
  semantic_type: "pareto",
  chart_type: "composite",
  title: "Defects",
  fields: {
    category: "category",
    bar: "count",
    line: "cumulative_ratio",
  },
  data: [
    { category: "Scratch", count: 8, cumulative_ratio: 0.8 },
    { category: "Dust", count: 2, cumulative_ratio: 1 },
  ],
});
assert.equal(pareto.series[0].type, "bar");
assert.equal(pareto.series[1].type, "line");
assert.equal(pareto.series[1].yAxisIndex, 1);
assert.deepEqual(pareto.xAxis.data, ["Scratch", "Dust"]);

const boxplot = adapter.toEChartsOption({
  schema_version: "3.0",
  semantic_type: "boxplot",
  chart_type: "boxplot",
  fields: {
    group: "group",
    min: "min",
    q1: "q1",
    median: "median",
    q3: "q3",
    max: "max",
    outliers: "outliers",
  },
  data: [
    {
      group: "A",
      min: 1,
      q1: 2,
      median: 3,
      q3: 4,
      max: 5,
      outliers: [10],
    },
  ],
});
assert.equal(boxplot.series[0].type, "boxplot");
assert.equal(boxplot.series[1].type, "scatter");
assert.deepEqual(boxplot.series[1].data, [[0, 10]]);

const heatmap = adapter.toEChartsOption({
  schema_version: "3.0",
  semantic_type: "heatmap",
  chart_type: "heatmap",
  fields: { x: "x", y: "y", value: "correlation", sample_size: "n" },
  data: [{ x: "A", y: "B", correlation: 0.8, n: 10 }],
});
assert.equal(heatmap.series[0].type, "heatmap");
assert.deepEqual(heatmap.series[0].data, [[0, 0, 0.8]]);

const controlChart = adapter.toEChartsOption({
  schema_version: "3.0",
  semantic_type: "control_chart",
  chart_type: "line",
  fields: {
    period: "period",
    value: "value",
    center_line: "center_line",
    ucl: "ucl",
    lcl: "lcl",
  },
  data: [{ period: 1, value: 10, center_line: 9, ucl: 12, lcl: 6 }],
});
assert.equal(controlChart.series.length, 4);
assert.deepEqual(controlChart.series[2].data, [12]);

const capability = adapter.toEChartsOption({
  schema_version: "3.0",
  semantic_type: "spec_capability",
  chart_type: "bar",
  fields: {
    cp: "cp",
    cpk: "cpk",
    pp: "pp",
    ppk: "ppk",
    lsl: "lsl",
    usl: "usl",
    mean: "mean",
  },
  data: [{ cp: 1.3, cpk: 1.2, pp: 1.1, ppk: 1.0, lsl: 5, usl: 15, mean: 10 }],
});
assert.deepEqual(capability.series[0].data, [1.3, 1.2, 1.1, 1.0]);

let renderedOption;
const instance = {
  setOption(value, replace) {
    renderedOption = value;
    assert.equal(replace, true);
  },
};
const runtime = {
  getInstanceByDom() {
    return null;
  },
  init() {
    return instance;
  },
};
assert.equal(adapter.render({}, chart, runtime), instance);
assert.equal(renderedOption.series[0].type, "line");

console.log("ECharts adapter tests passed");
