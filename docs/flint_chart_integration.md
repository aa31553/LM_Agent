# Flint chart integration

LM Agent uses [Microsoft Flint](https://github.com/microsoft/flint-chart) 0.4.1 as
its semantic chart compiler and Apache ECharts 6.1.0 as the browser renderer.
Dependencies are locked by `package-lock.json`; the console never loads chart code
from a CDN.

## Architecture

The Python analysis services continue to emit the versioned LM Agent Chart Schema.
`frontend/src/lm-chart-to-flint.js` converts safe, declarative chart data to Flint's
`ChartAssemblyInput`. Flint compiles it to a native ECharts option, and the stable
`LMAnalysisCharts` facade renders it. Model-generated JavaScript and Flint `data.url`
are not accepted.

Flint renders general bar, line, scatter, period-overlay and heatmap charts. Pareto,
boxplot, control and process-capability charts retain their tested legacy ECharts
builders until Flint offers parity for their dual axes, summary data or limit lines.
A compiler error is isolated to one chart and the console shows its data as a fallback.

## Build and run

```bash
npm ci
npm run build:frontend
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Open `http://127.0.0.1:8000/console/`. Docker builds the same bundle in a Node build
stage before copying it into the Python runtime image.

## Verification and upgrades

Run `npm run test:frontend`, the Python test suite, and a Docker build. Flint upgrades
must update the exact version and lockfile together, retain the upstream MIT license,
and pass all chart fixtures. Roll back by restoring those files; the legacy builders
remain available for specialized quality charts.
