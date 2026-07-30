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
