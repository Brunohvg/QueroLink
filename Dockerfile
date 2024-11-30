FROM python:3.12.3

# Definir a variável de ambiente PYTHONUNBUFFERED
ENV PYTHONUNBUFFERED=1

# Definir o diretório de trabalho dentro do container
WORKDIR /app

# Copiar apenas o arquivo de requisitos primeiro para otimizar o cache
COPY requirements.txt ./

# Instalar as dependências do Python
RUN pip install --no-cache-dir -r requirements.txt

# Copiar o restante do código para o contêiner
COPY . .

# Expor a porta 8000, que o Django usará
EXPOSE 8000

# Copiar o script de entrada e garantir que tenha permissões de execução
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Usar o script de entrada para iniciar o Django e o Gunicorn
ENTRYPOINT ["/entrypoint.sh"]
