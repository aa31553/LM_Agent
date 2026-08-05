const CHART_TYPES = Object.freeze({
  bar: "Bar Chart",
  category_bar: "Bar Chart",
  data_quality: "Bar Chart",
  missing_values: "Bar Chart",
  histogram: "Bar Chart",
  line: "Line Chart",
  trend_line: "Line Chart",
  period_overlay: "Line Chart",
  stacked_bar: "Bar Chart",
  scatter: "Scatter Plot",
  heatmap: "Heatmap",
});

const FORBIDDEN_KEYS = new Set(["__proto__", "constructor", "prototype"]);

function safeField(field, label) {
  if (typeof field !== "string" || !field || FORBIDDEN_KEYS.has(field)) {
    throw new TypeError(`${label} must be a safe field name`);
  }
  return field;
}

function encoding(field, type) {
  return { field: safeField(field, "encoding field"), type };
}

function inferType(chart, field, fallback) {
  const declared = chart.field_semantics?.[field]?.data_type;
  if (["quantitative", "nominal", "ordinal", "temporal"].includes(declared)) {
    return declared;
  }
  if (field === chart.x_field && chart.x_type === "time") return "temporal";
  if (field === chart.x_field && chart.x_type === "value") return "quantitative";
  return fallback;
}

function semanticTypes(chart, fields) {
  return Object.fromEntries(
    fields.map((field) => {
      const declared = chart.field_semantics?.[field];
      if (declared?.semantic_type) {
        const annotation = { semanticType: declared.semantic_type };
        if (declared.unit) annotation.unit = declared.unit;
        if (declared.sort_order) annotation.sortOrder = declared.sort_order;
        if (declared.intrinsic_domain) annotation.intrinsicDomain = declared.intrinsic_domain;
        return [field, annotation];
      }
      return [field, inferType(chart, field, "nominal") === "quantitative" ? "Quantity" : "Category"];
    }),
  );
}

function safeRows(rows) {
  return rows.map((row) => {
    if (!row || typeof row !== "object" || Array.isArray(row)) {
      throw new TypeError("chart rows must be objects");
    }
    return Object.fromEntries(
      Object.entries(row).map(([key, value]) => {
        safeField(key, "data field");
        if (typeof value === "number" && !Number.isFinite(value)) {
          throw new TypeError(`data field ${key} must contain a finite number`);
        }
        if (value !== null && typeof value === "object") {
          throw new TypeError(`data field ${key} must contain a scalar value`);
        }
        if (typeof value === "function") {
          throw new TypeError(`data field ${key} must not contain executable code`);
        }
        return [key, value];
      }),
    );
  });
}

export function supportsFlint(chart) {
  if (!chart || typeof chart !== "object") return false;
  const key = chart.semantic_type || chart.chart_type || chart.type;
  return Boolean(CHART_TYPES[key]);
}

export function toFlintInput(chart, size = {}) {
  if (!supportsFlint(chart)) {
    throw new TypeError(`Unsupported Flint chart: ${String(chart?.semantic_type || chart?.chart_type || chart?.type)}`);
  }
  if (!Array.isArray(chart.data)) throw new TypeError("chart.data must be an array");
  if (chart.data.length > 5000) throw new RangeError("chart.data exceeds the frontend point limit");

  const semantic = chart.semantic_type || chart.chart_type || chart.type;
  const chartType = CHART_TYPES[semantic];
  const fields = chart.fields || {};
  const xField = safeField(chart.x_field || fields.x, "x field");
  const yField = safeField(chart.y_field || fields.value, "y field");
  const encodings = {
    x: encoding(xField, inferType(chart, xField, "nominal")),
    y: encoding(yField, inferType(chart, yField, "quantitative")),
  };
  if (semantic === "heatmap") {
    encodings.color = encoding(yField, "quantitative");
    encodings.y = encoding(safeField(fields.y, "heatmap y field"), "nominal");
  } else if (chart.series_field) {
    encodings.color = encoding(chart.series_field, "nominal");
  }

  const usedFields = [...new Set(Object.values(encodings).map((item) => item.field))];
  const width = Math.max(240, Math.floor(size.width || 640));
  const height = Math.max(240, Math.floor(size.height || 420));
  return {
    data: { values: safeRows(chart.data) },
    semantic_types: semanticTypes(chart, usedFields),
    chart_spec: {
      chartType,
      encodings,
      baseSize: { width: Math.min(width, 800), height: Math.min(height, 500) },
      canvasSize: { width, height },
    },
    options: { addTooltips: chart.interaction?.tooltip !== false },
    field_display_names: chart.field_display_names || {},
  };
}
