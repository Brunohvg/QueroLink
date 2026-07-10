import glob

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
        ('gestor_webhooks', '/gestor/webhooks/'),
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
