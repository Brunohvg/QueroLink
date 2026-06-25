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
    && apt-get purge -y gcc libffi-dev \
    && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

COPY . .

COPY --from=tailwind-build /app/static/css/tailwind.css ./static/css/tailwind.css

RUN chmod +x /app/entrypoint.sh \
    && useradd -m -u 1000 appuser \
    && chown -R appuser:appuser /app

USER appuser

EXPOSE 8000

ENTRYPOINT ["/app/entrypoint.sh"]
