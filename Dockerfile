FROM node:20-alpine AS tailwind-build

WORKDIR /app
COPY package.json tailwind.config.js ./
RUN npm install
COPY static/css/input.css ./static/css/
COPY templates/ ./templates/
RUN npm run build:css

FROM python:3.12.3-slim

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        curl \
        libpq-dev \
        gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements/base.txt ./requirements/base.txt
COPY requirements/production.txt ./requirements/production.txt

RUN pip install --no-cache-dir -r requirements/production.txt

COPY . .

COPY --from=tailwind-build /app/static/css/tailwind.css ./static/css/tailwind.css

RUN chmod +x /app/entrypoint.sh

EXPOSE 8000

ENTRYPOINT ["/app/entrypoint.sh"]
