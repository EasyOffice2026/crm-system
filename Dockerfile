FROM node:24-bookworm-slim AS frontend
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim-bookworm
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app/backend \
    CRM_DATABASE_URL=sqlite:////data/unused-single.db \
    CRM_UPLOAD_DIR=/data/uploads
WORKDIR /app/backend
COPY backend/pyproject.toml ./
COPY backend/app ./app
COPY backend/migrations ./migrations
RUN pip install --no-cache-dir . supervisor==4.2.5 setuptools==80.9.0 \
    && useradd --system --uid 10001 --create-home crm
COPY --from=frontend /build/dist /app/frontend/dist
COPY deploy/supervisord.conf /etc/crm-supervisord.conf
COPY deploy/entrypoint.sh /usr/local/bin/crm-entrypoint
RUN chmod 755 /usr/local/bin/crm-entrypoint
EXPOSE 8000
ENTRYPOINT ["crm-entrypoint"]
CMD ["supervisord", "-c", "/etc/crm-supervisord.conf"]
