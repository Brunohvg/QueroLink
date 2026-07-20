# Smoke test do fluxo de boleto

Este roteiro deve ser executado após a resolução do bloqueio de schema e a implementação do contrato.

## Preparação

- PostgreSQL novo e descartável, migrations limpas e provider de sandbox configurado.
- Tenants separados para feature ligada, feature desligada, plano inelegível e provider ausente.
- Usuários ADMIN, MANAGER, SELLER e FINANCEIRO; dois sellers e dois tenants.

## Cenários essenciais

1. ADMIN e MANAGER visualizam histórico e emitem para seller selecionado.
2. SELLER visualiza somente seus boletos, emite para si e não consegue enviar outro seller como autoridade.
3. FINANCEIRO visualiza histórico e allocation, mas não emite nem cancela.
4. Feature/plano/provider indisponível bloqueia nova emissão sem ocultar histórico.
5. CPF válido não consulta serviço externo; CNPJ e CEP válidos preenchem apenas campos vazios.
6. Timeout, 404 e payload inválido de CNPJ/CEP preservam o preenchimento manual e mostram mensagem útil.
7. Cliente de mesmo tenant e documento exato pode ser reutilizado; cliente de outro tenant nunca aparece.
8. Revisão mostra seller, pagador, valor em centavos formatado e vencimento antes do POST.
9. Duplo clique e retry usam a mesma chave UUID e produzem um único boleto local/remoto.
10. Sucesso mostra UUID, status, URL real, linha digitável e barcode reais; copiar/abrir funcionam por teclado.
11. Ausência temporária de URL/linha mostra processamento e reconciliação, sem valor inventado.
12. Cancelamento é idempotente, tenant-scoped e restrito a ADMIN/MANAGER em status elegível.
13. Central exibe link e boleto sem fundir aggregates e atualiza após emissão.
14. Desktop e mobile/PWA executam o mesmo contrato e apresentam os mesmos erros de domínio.
15. Logs, respostas e analytics não contêm CPF/CNPJ, telefone, e-mail ou payload externo.

## Gates

```text
python manage.py check
python manage.py test
python manage.py makemigrations --check --dry-run
git diff --check
grep -rn "json.dumps\|_json.dumps" app/apps/dashboard/*.py | grep -v test
```

Nenhuma execução manual contra provider real, tenant real ou ambiente de produção está autorizada por este roteiro.
