FROM node:22-alpine AS frontend_builder

WORKDIR /app

# Allow overriding the API base at build time; default to same-origin
ARG PUBLIC_API_BASE=/
ENV PUBLIC_API_BASE=$PUBLIC_API_BASE

COPY frontend/package*.json ./
RUN npm install
COPY frontend ./
RUN npm run build

FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

WORKDIR /app

COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt \
    && rm -rf /root/.cache/pip

COPY backend/app ./app
COPY backend/.env.example .env

# Copy built frontend into the backend image
COPY --from=frontend_builder /app/build ./static

RUN adduser --disabled-password --gecos "" gazarr \
    && mkdir -p /app/data \
    && chown -R gazarr:gazarr /app

USER gazarr

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--app-dir", "app", "--host", "0.0.0.0", "--port", "8000"]
