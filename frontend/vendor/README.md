# Frontend vendor assets

- `echarts.min.js`: Apache ECharts 6.1.0 full browser distribution.
- `ECHARTS-LICENSE.txt`: Apache License 2.0 text shipped by the package.
- `ECHARTS-NOTICE.txt`: upstream notice shipped by the package.

The runtime is vendored so the internal frontend does not depend on an external
CDN. Update it from the official `echarts` npm package and retain the license and
notice files.

- `flint-chart` 0.4.1 is bundled at build time into `frontend/dist/chart-runtime.js`.
  It is distributed under the MIT License; see `FLINT-CHART-LICENSE.txt`.
- Rebuild the browser bundle with `npm ci && npm run build:frontend`.
