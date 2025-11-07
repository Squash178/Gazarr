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

RUN apt-get update \
    && apt-get install -y --no-install-recommends gosu \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt \
    && rm -rf /root/.cache/pip

COPY backend/app ./app
# Create a default .env (values can be overridden at runtime)
RUN printf "# generated at build time\n" > .env

# Copy built frontend into the backend image
COPY --from=frontend_builder /app/build ./static

RUN adduser --disabled-password --gecos "" gazarr \
    && mkdir -p /app/data \
    && mkdir -p /app/covers \
    && chown -R gazarr:gazarr /app \
    && chmod 0777 /app/covers

COPY backend/docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

EXPOSE 8000

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--app-dir", "app", "--host", "0.0.0.0", "--port", "8000"]
