FROM node:22-alpine AS builder

WORKDIR /app

ARG PUBLIC_API_BASE=http://localhost:8000
ENV PUBLIC_API_BASE=$PUBLIC_API_BASE

COPY frontend/package*.json ./
RUN npm install

COPY frontend ./
RUN npm run build

FROM nginx:1.27-alpine

COPY --from=builder /app/build /usr/share/nginx/html
COPY frontend/nginx.conf /etc/nginx/conf.d/default.conf
