#!/bin/bash
set -e

# Esperar o banco de dados estar disponível
echo "Waiting for database to be ready..."
sleep 5 # Para evitar timeout antes do healthcheck

# Executar migrações
echo "Applying database migrations..."
python manage.py migrate --noinput

# Coletar arquivos estáticos
echo "Collecting static files..."
python manage.py collectstatic --noinput

# Iniciar o Gunicorn
echo "Starting Gunicorn..."
exec gunicorn app.config.wsgi:application --bind 0.0.0.0:8000 --workers 3