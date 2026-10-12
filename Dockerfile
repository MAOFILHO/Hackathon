# One container serves the API and the built frontend on port 7860 (deployed with deploy/azure/deploy.sh).

FROM node:22-slim AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim
RUN useradd -m -u 1000 user
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy \
    PATH="/app/backend/.venv/bin:$PATH" \
    REQUIRE_PASSWORD=1 \
    STATIC_DIR=/app/frontend/dist
COPY backend/pyproject.toml backend/uv.lock backend/
RUN cd backend && uv sync --frozen --no-dev --no-install-project
COPY backend/ backend/
COPY --from=web /web/dist frontend/dist
RUN chown -R user:user /app
USER user
WORKDIR /app/backend
EXPOSE 7860
CMD ["uvicorn", "finagent.api:app", "--host", "0.0.0.0", "--port", "7860"]
