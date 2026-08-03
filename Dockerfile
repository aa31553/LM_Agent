FROM node:20-slim AS frontend-build

WORKDIR /build
COPY package.json package-lock.json ./
RUN npm ci
COPY frontend ./frontend
RUN npm run build:frontend

FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml /app/
RUN pip install --no-cache-dir .

COPY . /app
COPY --from=frontend-build /build/frontend/dist /app/frontend/dist

RUN test -s /app/frontend/dist/chart-runtime.js

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
