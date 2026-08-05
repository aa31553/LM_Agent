"use strict";

const assert = require("node:assert/strict");
const path = require("node:path");

require(path.resolve(__dirname, "../../frontend/dist/chart-runtime.js"));
const flint = globalThis.LMFlintCharts;
assert.ok(flint, "Flint browser runtime is exposed");

const bar = {
  schema_version: "3.0",
  semantic_type: "category_bar",
  chart_type: "bar",
  title: "Defects",
  x_field: "category",
  y_field: "count",
  field_semantics: {
    category: { semantic_type: "Category", data_type: "nominal" },
    count: { semantic_type: "Quantity", data_type: "quantitative" },
  },
  data: [{ category: "Scratch", count: 8 }],
};
const input = flint.toFlintInput(bar, { width: 600, height: 400 });
assert.equal(input.chart_spec.chartType, "Bar Chart");
assert.equal(input.chart_spec.encodings.x.field, "category");
assert.equal(input.chart_spec.encodings.y.type, "quantitative");
assert.deepEqual(input.data.values, bar.data);

const option = flint.compile(bar, { width: 600, height: 400 });
assert.equal(option.title.text, "Defects");
assert.equal(option.series[0].type, "bar");
assert.deepEqual(option.series[0].data, [8]);

const overlay = flint.compile({
  schema_version: "3.0",
  semantic_type: "period_overlay",
  chart_type: "line",
  x_field: "day",
  y_field: "value",
  series_field: "series",
  x_type: "value",
  data: [
    { day: 1, value: 10, series: "2026-01" },
    { day: 1, value: 12, series: "2026-02" },
  ],
});
assert.equal(overlay.series.length, 2);
assert.ok(overlay.series.every((series) => series.type === "line"));

const heatmap = flint.compile({
  schema_version: "3.0",
  semantic_type: "heatmap",
  chart_type: "heatmap",
  fields: { x: "left", y: "right", value: "correlation" },
  data: [{ left: "A", right: "B", correlation: 0.8 }],
});
assert.equal(heatmap.series[0].type, "heatmap");

assert.equal(flint.supportsFlint({ semantic_type: "control_chart" }), false);
assert.throws(
  () => flint.toFlintInput({ ...bar, x_field: "__proto__" }),
  /safe field name/,
);
assert.throws(
  () => flint.toFlintInput({ ...bar, data: Array.from({ length: 5001 }, () => ({})) }),
  /point limit/,
);
assert.throws(
  () => flint.toFlintInput({ ...bar, data: [{ category: "A", count: Infinity }] }),
  /finite number/,
);

console.log("Flint chart adapter tests passed");
