FROM node:20-alpine AS tailwind-build

WORKDIR /app
COPY package.json tailwind.config.js ./
RUN npm install
COPY static/css/input.css ./static/css/
COPY templates/ ./templates/
RUN npm run build:css

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        curl \
        unzip \
        postgresql-client \
        libpq-dev \
        gcc \
        libpango-1.0-0 \
        libpangocairo-1.0-0 \
        libgdk-pixbuf-2.0-0 \
        libffi-dev \
        shared-mime-info \
    && rm -rf /var/lib/apt/lists/*

COPY requirements/base.txt ./requirements/base.txt
COPY requirements/production.txt ./requirements/production.txt

RUN pip install --no-cache-dir -r requirements/production.txt \
    && rm -rf /root/.cache/pip /var/lib/apt/lists/*

RUN ARCH=$(dpkg --print-architecture) \
    && case "$ARCH" in \
        amd64) RCLONE_ARCH=amd64 ;; \
        arm64) RCLONE_ARCH=arm64 ;; \
        *) echo "ERROR: unsupported architecture: $ARCH" && exit 1 ;; \
       esac \
    && curl -fsSL "https://downloads.rclone.org/rclone-current-linux-${RCLONE_ARCH}.deb" -o /tmp/rclone.deb \
    && dpkg -i /tmp/rclone.deb \
    && rm /tmp/rclone.deb \
    && rclone version

COPY . .

COPY --from=tailwind-build /app/static/css/tailwind.css ./static/css/tailwind.css

RUN chmod +x /app/entrypoint.sh /app/scripts/*.sh \
    && useradd -m -u 1000 appuser \
    && mkdir -p /app/celerybeat-schedule /app/backups \
    && chown -R appuser:appuser /app

USER appuser

EXPOSE 8000

ENTRYPOINT ["/app/entrypoint.sh"]
