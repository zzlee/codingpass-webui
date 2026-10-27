# =============================================================================
# Stage 1: Build Frontend SPA
# =============================================================================
FROM node:20-slim AS frontend-builder

WORKDIR /build/frontend

# Copy package manifests first to leverage Docker layer caching
COPY frontend/package*.json ./
RUN npm ci

# Copy frontend source and build static bundle
COPY frontend/ ./
RUN npm run build

# =============================================================================
# Stage 2: Production Python Judge & API Runtime
# =============================================================================
FROM python:3.12-slim

# Install system dependencies:
# - build-essential, gcc, g++: C/C++ compilation toolchain
# - bubblewrap: process sandbox with namespace isolation
# - nodejs: runtime for TeDa testcase generation scripts
# - ca-certificates, curl: network certs and container healthchecks
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    gcc \
    g++ \
    bubblewrap \
    nodejs \
    ca-certificates \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python backend dependencies
COPY backend/requirements.txt /app/backend/
RUN pip install --no-cache-dir -r /app/backend/requirements.txt

# Copy built frontend distribution from builder stage
COPY --from=frontend-builder /build/frontend/dist /app/frontend/dist

# Copy backend application source
COPY backend/ /app/backend/

# Copy local agent and auxiliary scripts
COPY local_agent/ /app/local_agent/
COPY scripts/ /app/scripts/

# Create persistent storage directories for SQLite and problem packs
RUN mkdir -p /app/data /app/data/problem_packs && \
    chmod -R 777 /app/data

# Default production environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    DATABASE_URL=sqlite:////app/data/codingpass.db \
    PROBLEM_PACK_PDF_DIR=/app/data/problem_packs \
    FRONTEND_DIST=/app/frontend/dist \
    HOST=0.0.0.0 \
    PORT=8010

EXPOSE 8010

# Health check probe
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://127.0.0.1:8010/api/health || exit 1

CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8010"]
