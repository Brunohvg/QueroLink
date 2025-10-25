QueroLink / LinkPay
Este é um projeto Django desenvolvido para atuar como um gerador de links de pagamento, integrando-se diretamente com a API v5 do Pagar.me. A aplicação permite a criação de links de pagamento (checkout) e armazena um registro das transações no banco de dados. Adicionalmente, possui uma integração para enviar os links gerados via WhatsApp para os clientes.

A aplicação é projetada para ser executada em containers Docker e está pré-configurada para deploy por trás de um reverse proxy Traefik.

Funcionalidades Principais
Geração de Link de Pagamento: Cria links de pagamento dinâmicos utilizando a API do Pagar.me, especificando valor total, nome e número de parcelas.

API REST: Expõe uma API REST (construída com Django Rest Framework) para listar e gerenciar os registros de pagamentos criados.

Integração com WhatsApp: Conecta-se a uma API externa de WhatsApp (api.lojabibelo.com.br) para enviar mensagens de texto, provavelmente o link de pagamento, para o cliente.

Deploy com Docker: O projeto inclui um Dockerfile e um docker-compose.yml para facilitar o build e a execução da aplicação em um ambiente containerizado.

Serviço de Arquivos Estáticos: Utiliza o Whitenoise para servir arquivos estáticos em produção de forma eficiente.

Tecnologias Utilizadas
Backend: Python 3.12, Django 5.1, Django Rest Framework

Servidor WSGI: Gunicorn

Arquivos Estáticos: Whitenoise

Configuração: Python Decouple

Deploy: Docker, Docker Compose

Banco de Dados: SQLite (padrão de desenvolvimento)

APIs Externas: Pagar.me v5, API de WhatsApp (Lojabibelo)

Configuração
O projeto é configurado via variáveis de ambiente, gerenciadas pelo python-decouple. É esperado que um arquivo .env seja criado na raiz do projeto (ou que as variáveis sejam injetadas no ambiente de deploy).

As seguintes variáveis são necessárias:

SECRET_KEY: Chave secreta do Django.

DEBUG: Define o modo debug (ex: True ou False).

ALLOWED_HOSTS: Lista de hosts permitidos (ex: linkpay.lojabibelo.com.br,localhost).

API_KEY_PAGAR_ME: Chave de API (Basic Auth) para a integração com o Pagar.me.

API_KEY_INSTANCIA: Chave de API para a integração com o serviço de WhatsApp.

INSTANCE: Nome da instância utilizada no serviço de WhatsApp.

Executando com Docker
Clone o repositório.

Crie um arquivo .env na raiz do projeto com as variáveis de ambiente descritas acima.

O docker-compose.yml está configurado para usar redes Docker externas (traefik_public e app_network). Certifique-se de que essas redes existam (docker network create traefik_public) ou remova a diretiva external: true do docker-compose.yml para que o compose as crie.

Execute o build e suba o serviço:

Bash

docker-compose up -d --build
O script entrypoint.sh irá automaticamente aplicar as migrações do banco de dados (migrate), coletar os arquivos estáticos (collectstatic) e iniciar o servidor Gunicorn.

A aplicação estará disponível na porta 8082 do host, ou será roteada pelo Traefik se ele estiver configurado na mesma rede, respondendo pelo host linkpay.lojabibelo.com.br.

Estrutura do Projeto
core/: Contém as configurações principais do Django (settings.py, urls.py).

link/: É o app principal do Django.

models.py: Define o modelo PagarMePayment para armazenar os dados da transação.

urls.py: Define as rotas da aplicação web e da API.

views.py: (Não fornecido) Controla as views da interface web (index, create_link).

viewsets.py: (Não fornecido) Define o ViewSet para a API PagarMePaymentViewSet.

api/pagar_me.py: Contém a lógica de cliente para se comunicar com a API do Pagar.me.

api/whatsapp.py: Contém a lógica de cliente para se comunicar com a API de WhatsApp.

Dockerfile: Instruções para construir a imagem Docker da aplicação.

docker-compose.yml: Define o serviço web para orquestração com Docker Compose.

entrypoint.sh: Script executado quando o container inicia.

requirements.txt: Lista de dependências Python.

API
A aplicação expõe uma API REST para o modelo PagarMePayment.

Endpoint Base: /api/v1/pagamentos/

Autenticação: A API está configurada para usar TokenAuthentication do Django Rest Framework.

Rotas Disponíveis (via DefaultRouter):

GET /api/v1/pagamentos/: Lista todos os pagamentos registrados.

POST /api/v1/pagamentos/: Cria um novo registro de pagamento.

GET /api/v1/pagamentos/<id>/: Detalha um pagamento específico.

PUT /api/v1/pagamentos/<id>/: Atualiza um pagamento específico.

PATCH /api/v1/pagamentos/<id>/: Atualiza parcialmente um pagamento específico.

DELETE /api/v1/pagamentos/<id>/: Deleta um pagamento específico.
