FROM python:3.11-slim

WORKDIR /app

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PATH="/root/.local/bin:${PATH}"

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    git \
    build-essential \
    ca-certificates \
    gnupg \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && node --version \
    && npm --version \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

COPY frontend/package*.json frontend/
RUN cd frontend && npm install

COPY . .

RUN cd frontend && npm run build

# Install Coral CLI for Linux using the official installer.
# If install fails, stop the image build with a clear message.
RUN set -eux; \
    curl -fsSL https://withcoral.com/install.sh | sh; \
    if ! command -v coral >/dev/null 2>&1; then \
      echo "ERROR: Coral CLI installation failed. OmniSprint requires Coral in Docker Space." >&2; \
      echo "See https://withcoral.com/docs/getting-started/installation for Linux install options." >&2; \
      exit 1; \
    fi; \
    coral --version

RUN chmod +x scripts/*.sh || true
RUN chmod +x scripts/start_hf.sh || true

EXPOSE 7860

CMD ["bash", "scripts/start_hf.sh"]
