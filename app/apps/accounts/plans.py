OFFERED_PLAN_CODES = ('STARTER', 'PRO', 'BUSINESS')

PLAN_DISPLAY_FEATURES = {
    'STARTER': [
        'Ate 5 vendedores',
        'App do vendedor e painel do gestor',
        'Lancamento e acompanhamento de vendas',
        'Fechamento de comissoes',
        'Links de pagamento',
        'Lembretes automaticos por WhatsApp',
    ],
    'PRO': [
        'Ate 15 vendedores',
        'Tudo do Essencial',
        'Importacao de vendas por CSV',
        'Relatorios em PDF e previa de fechamento',
        'Pacote contabil automatico por e-mail',
        'Notificacoes push para vendedores',
        'Boletos e cobrancas integrados',
        'Acompanhamento de recebimentos',
        'Integracao com vendas e comissoes',
        'Historico de clientes e pagamentos',
    ],
    'BUSINESS': [
        'Ate 50 vendedores',
        'Tudo do Pro',
        'Acesso para a equipe financeira',
        'Auditoria detalhada das operacoes',
        'Revisao de estornos e impactos em comissoes',
        'Controles avancados de aprovacao',
        'Implantacao assistida',
        'Suporte prioritario',
    ],
    'ENTERPRISE': [
        'Vendedores ilimitados (legado)',
        'Tudo do Business',
        'Suporte dedicado',
    ],
}


def offered_plan_choices():
    from app.apps.accounts.models import Tenant

    labels = dict(Tenant.Plan.choices)
    return [(code, labels[code]) for code in OFFERED_PLAN_CODES]


def is_offered_plan(plan_code):
    return plan_code in OFFERED_PLAN_CODES
