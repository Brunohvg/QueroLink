import glob
from pathlib import Path

from django.test import TestCase


class OrphanScreenTests(TestCase):
    GESTOR_ROUTE_NAMES = [
        ('gestor_home', '/gestor/'),
        ('gestor_ranking', '/gestor/ranking/'),
        ('gestor_vendedores', '/gestor/vendedores/'),
        ('gestor_importar_vendas', '/gestor/importar-vendas/'),
        ('gestor_vendedor_detalhe', '/gestor/vendedores/'),
        ('gestor_fechamento', '/gestor/fechamento/'),
        ('gestor_contabilidade', '/gestor/contabilidade/'),
        ('gestor_previa_fechamento', '/gestor/previa-fechamento/'),
        ('gestor_configuracoes', '/gestor/configuracoes/'),
        ('gestor_links', '/gestor/links/'),
        ('gestor_link_detalhe', '/gestor/links/'),
        ('assinatura', '/assinatura/'),
    ]

    def test_toda_tela_do_gestor_tem_link(self):
        templates_src = ''
        for path in glob.glob('templates/**/*.html', recursive=True):
            with open(path, encoding='utf-8') as fh:
                templates_src += fh.read()

        missing = []
        for name, url_path in self.GESTOR_ROUTE_NAMES:
            url_ref = f"dashboard:{name}'"
            url_ref2 = f'dashboard:{name}"'
            if url_ref in templates_src or url_ref2 in templates_src:
                continue
            if url_path in templates_src:
                continue
            missing.append(name)

        self.assertEqual(
            missing, [],
            f'Telas sem nenhum link apontando para elas (orfas): {missing}',
        )


class NavigationPerformanceRegressionTests(TestCase):
    def test_desktop_layout_is_responsive(self):
        base = Path('templates/dashboard/base_desktop.html').read_text()
        sidebar = Path('templates/components/_sidebar.html').read_text()
        self.assertIn('@click="sidebarOpen=true"', base)
        self.assertIn('lg:hidden', base)
        self.assertIn('lg:static', sidebar)
        self.assertIn("sidebarOpen ? 'translate-x-0'", sidebar)

    def test_chart_is_local_and_only_requested_by_chart_pages(self):
        base = Path('templates/dashboard/base_desktop.html').read_text()
        self.assertNotIn('cdn.jsdelivr.net', base)
        self.assertIn('{% block chart_js %}', base)
        for template in ('home.html', 'ranking.html', 'vendedor_detalhe.html'):
            source = Path('templates/dashboard/gestor', template).read_text()
            self.assertIn("static 'js/chart.umd.js'", source)

    def test_no_external_font_cdn_in_templates(self):
        for path in Path('templates').rglob('*.html'):
            self.assertNotIn('fonts.googleapis.com', path.read_text())

    def test_seller_page_uses_single_bulk_endpoint(self):
        source = Path('templates/dashboard/gestor/vendedores.html').read_text()
        self.assertIn('/api/sellers/dashboard-summary/', source)
        self.assertNotIn('list.map(async', source)
        self.assertNotIn("'/api/manager/seller/' + s.uuid", source)

    def test_seller_page_supports_no_commission_status(self):
        source = Path('templates/dashboard/gestor/vendedores.html').read_text()
        self.assertIn(
            "s.financial_status==='SEM_COMISSAO' ? 'Sem comissão'",
            source,
        )
        self.assertIn(
            "'bg-slate-50 text-slate-600': "
            "s.financial_status==='SEM_COMISSAO'",
            source,
        )
        self.assertIn(
            "s.financial_status === 'ABERTA' ? a + "
            "(s.commission_amount || 0) : a",
            source,
        )

    def test_seller_page_counts_only_unresolved_today(self):
        source = Path('templates/dashboard/gestor/vendedores.html').read_text()
        self.assertIn('!s.has_day_resolved_today', source)
        self.assertNotIn('!s.has_sale_today', source)

    def test_pagarme_health_card_has_actionable_copy(self):
        source = Path('templates/dashboard/gestor/home.html').read_text()
        self.assertNotIn('Requer atenção</p>', source)
        self.assertIn('Webhooks pendentes', source)
        self.assertIn('Verificar eventos', source)
        self.assertIn('Configurar Pagar.me', source)

    def test_links_prefetches_payments(self):
        source = Path('app/apps/dashboard/desktop_views.py').read_text()
        links_view = source.split('def gestor_links', 1)[1].split(
            'def gestor_link_detalhe', 1,
        )[0]
        self.assertIn("prefetch_related(\n        'payments'", links_view)
