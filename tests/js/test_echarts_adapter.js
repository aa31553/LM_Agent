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
