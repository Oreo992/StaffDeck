ARG NODE_IMAGE=node:22-bookworm-slim
ARG PYTHON_IMAGE=python:3.11-slim

FROM ${NODE_IMAGE} AS frontend-build
ARG NPM_REGISTRY=https://registry.npmmirror.com
WORKDIR /src

COPY frontend-enterprise/package.json frontend-enterprise/package-lock.json ./frontend-enterprise/
RUN --mount=type=cache,target=/root/.npm \
    npm --prefix frontend-enterprise ci --registry "${NPM_REGISTRY}"

COPY frontend-enterprise ./frontend-enterprise
RUN npm --prefix frontend-enterprise run build

FROM ${PYTHON_IMAGE} AS runtime
ARG PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HOME=/data/home \
    XDG_CACHE_HOME=/data/cache

RUN groupadd --gid 10001 staffdeck \
    && useradd --uid 10001 --gid 10001 --no-create-home --home-dir /data/home staffdeck \
    && mkdir -p /app/backend /app/frontend-enterprise /data \
    && chown -R staffdeck:staffdeck /data

WORKDIR /app
COPY backend ./backend
RUN --mount=type=cache,target=/root/.cache/pip \
    python -m pip install --index-url "${PIP_INDEX_URL}" ./backend

COPY --from=frontend-build /src/frontend-enterprise/dist ./frontend-enterprise/dist

USER staffdeck
WORKDIR /app/backend

EXPOSE 5173

HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=4 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:5173/api/health', timeout=3).read()"

CMD ["python", "-m", "uvicorn", "single_port_app:app", "--host", "0.0.0.0", "--port", "5173", "--proxy-headers", "--forwarded-allow-ips=*"]
