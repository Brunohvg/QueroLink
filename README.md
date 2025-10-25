# QueroLink / LinkPay

Um projeto **Django** para gerar links de pagamento integrados à **API v5 do Pagar.me**, com envio automático de links via WhatsApp. Desenvolvido para rodar em **Docker**, pré-configurado para deploy por trás de um **proxy reverso Traefik**.

---

## 🚀 Funcionalidades

- **Geração de Link de Pagamento:** Cria links dinâmicos usando a API do Pagar.me, com valor, nome e número de parcelas.  
- **API REST:** CRUD completo de pagamentos usando Django Rest Framework.  
- **Integração WhatsApp:** Envia links de pagamento via API externa (`api.lojabibelo.com.br`).  
- **Deploy com Docker:** Inclui `Dockerfile` e `docker-compose.yml` prontos para rodar em containers.  
- **Serviço de Arquivos Estáticos:** Whitenoise para servir arquivos estáticos em produção de forma eficiente.

---

## 🛠 Tecnologias

- **Backend:** Python 3.12, Django 5.1, Django Rest Framework  
- **Servidor WSGI:** Gunicorn  
- **Arquivos Estáticos:** Whitenoise  
- **Configuração:** Python Decouple  
- **Deploy:** Docker, Docker Compose  
- **Banco de Dados:** SQLite (desenvolvimento)  
- **APIs Externas:** Pagar.me v5, API WhatsApp (Lojabibelo)

---

## ⚙️ Configuração

O projeto utiliza **variáveis de ambiente** via `python-decouple`. Crie um arquivo `.env` na raiz do projeto ou configure as variáveis no ambiente de deploy.

Variáveis principais:

```env
SECRET_KEY=your_django_secret_key
DEBUG=True
ALLOWED_HOSTS=localhost,linkpay.lojabibelo.com.br
API_KEY_PAGAR_ME=your_pagarme_api_key
API_KEY_INSTANCIA=your_whatsapp_api_key
INSTANCE=nome_da_instancia_whatsapp
🐳 Executando com Docker
Clone o repositório:

bash
Copiar código
git clone <seu-repo-url>
cd linkpay
Crie o .env com as variáveis descritas acima.

Certifique-se de que as redes Docker traefik_public e app_network existam, ou remova external: true do docker-compose.yml.

bash
Copiar código
docker network create traefik_public
docker network create app_network
Build e start:

bash
Copiar código
docker-compose up -d --build
O script entrypoint.sh aplicará migrações, coletará arquivos estáticos e iniciará o servidor Gunicorn.

Porta padrão: 8082

Se estiver usando Traefik, disponível via linkpay.lojabibelo.com.br.

📂 Estrutura do Projeto
bash
Copiar código
core/                  # Configurações principais do Django
link/                  # App principal
  ├─ models.py         # Modelo PagarMePayment
  ├─ urls.py           # Rotas web e API
  ├─ views.py          # Views web
  ├─ viewsets.py       # ViewSets da API
  └─ api/
      ├─ pagar_me.py   # Cliente Pagar.me
      └─ whatsapp.py   # Cliente WhatsApp
Dockerfile
docker-compose.yml
entrypoint.sh
requisitos.txt
📡 API REST
Base: /api/v1/pagamentos/
Autenticação: TokenAuthentication do Django Rest Framework

Rotas
Método	Endpoint	Descrição
GET	/api/v1/pagamentos/	Lista todos os pagamentos
POST	/api/v1/pagamentos/	Cria novo pagamento
GET	/api/v1/pagamentos/<id>/	Detalha pagamento
PUT	/api/v1/pagamentos/<id>/	Atualiza pagamento
PATCH	/api/v1/pagamentos/<id>/	Atualiza parcialmente
DELETE	/api/v1/pagamentos/<id>/	Remove pagamento

💡 Observações
Pronto para produção usando Traefik e Docker.

Arquivos estáticos servidos com Whitenoise, sem necessidade de Nginx extra.

Integração completa com Pagar.me e WhatsApp, facilitando envios automáticos.

📌 Desenvolvido por [Seu Nome / Lojabibelo]

arduino
Copiar código

Se quiser, posso fazer uma **versão ainda mais “profissional GitHub”**, com badges, demo, setup rápido e t
