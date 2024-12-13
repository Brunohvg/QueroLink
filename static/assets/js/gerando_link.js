
document.getElementById('create-link-form').addEventListener('submit', function (event) {
    var btn = document.getElementById('generate-link-btn')
    btn.disabled = true // Desabilita o botão
    btn.innerHTML = '<i class="fas fa-spinner fa-spin me-2"></i>Aguarde...' // Exibe um ícone de carregamento e muda o texto

    // Após o envio do formulário, o botão permanece desabilitado para evitar múltiplos cliques
    // Não é necessário adicionar tempo de espera aqui, pois o processamento da requisição já acontece no backend
})

