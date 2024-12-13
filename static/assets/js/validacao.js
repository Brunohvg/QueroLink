// Validação do campo de valor (linkValue)
document.getElementById('linkValue').addEventListener('input', function (e) {
    let value = e.target.value.replace(/\D/g, '') // Remove caracteres não numéricos
    if (value) {
        value = (value / 100).toFixed(2).replace('.', ',') // Converte para formato de moeda
        e.target.value = 'R$ ' + value // Adiciona o símbolo de moeda
    } else {
        e.target.value = '' // Se não houver valor, limpa o campo
    }
})

// Validação e máscara para o campo de telefone (whatsapp)
document.getElementById('whatsapp').addEventListener('input', function (e) {
    let value = e.target.value.replace(/\D/g, '') // Remove caracteres não numéricos

    // Limita o tamanho máximo de dígitos a 11 (2 dígitos do DDD + 9 do número)
    if (value.length > 11) {
        value = value.slice(0, 11)
    }

    // Aplica a máscara para formato (xx) xxxx-xxxx ou (xx) xxxxx-xxxx
    if (value.length <= 10) {
        value = value.replace(/(\d{2})(\d{4})(\d{0,4})/, '($1) $2-$3')
    } else {
        value = value.replace(/(\d{2})(\d{5})(\d{0,4})/, '($1) $2-$3')
    }

    e.target.value = value
})

function showMessage(text, type) {
    const messageElement = document.getElementById('message')
    messageElement.textContent = text
    messageElement.className = `message mt-3 fade-in-up ${type}` // Adiciona a classe de tipo
    messageElement.style.opacity = 1 // Torna a mensagem visível

    // Oculta a mensagem após 3 segundos
    setTimeout(() => {
        messageElement.style.opacity = 0 // Diminui a opacidade
    }, 3000)
}

function copyLink() {
    const generatedLinkElement = document.getElementById('generatedLink')
    const linkText = generatedLinkElement.textContent

    navigator.clipboard
        .writeText(linkText)
        .then(() => {
            showMessage('Link copiado para a área de transferência!', 'success')
        })
        .catch((err) => {
            console.error('Erro ao copiar o link: ', err)
            showMessage('Erro ao copiar o link. Por favor, tente novamente.', 'error')
        })
}
