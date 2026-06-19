#!/bin/bash
set -e

# Esperar o banco de dados estar disponível
echo "Waiting for database to be ready..."
sleep 5 # Para evitar timeout antes do healthcheck

# Apenas o container principal (sem parâmetros ou gunicorn) deve rodar as migrações
if [ -z "$1" ] || [ "$1" = 'gunicorn' ]; then
    echo "Making database migrations..."
    python manage.py makemigrations --noinput

    echo "Applying database migrations..."
    python manage.py migrate --noinput

    echo "Collecting static files..."
    python manage.py collectstatic --noinput

    echo "Starting Gunicorn..."
    exec gunicorn app.config.wsgi:application --bind 0.0.0.0:8000 --workers 3
else
    echo "Starting background service: $@"
    exec "$@"
fi