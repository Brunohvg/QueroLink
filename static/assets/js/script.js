const form = document.getElementById('linkForm')
const messageElement = document.getElementById('message')

form.addEventListener('submit', function (e) {
  e.preventDefault()

  const linkName = encodeURIComponent(document.getElementById('linkName').value.trim())
  // Coleta o valor apenas como número
  const linkValue = parseFloat(
    document
      .getElementById('linkValue')
      .value.replace(/[R$ ,]/g, '')
      .replace(',', '.')
  )
  const whatsapp = encodeURIComponent(document.getElementById('whatsapp').value.trim())

  // Validação dos campos obrigatórios
  if (!linkName || isNaN(linkValue) || !whatsapp) {
    showMessage('Por favor, preencha todos os campos obrigatórios.', 'error')
    return
  }

  // Aqui você pode gerar o link com os dados coletados...
  const generatedLink = `https://linkpay.pro/pagamento?nome=${linkName}&valor=${linkValue.toFixed(2)}&whatsapp=${whatsapp}`

  const generatedLinkElement = document.getElementById('generatedLink')
  generatedLinkElement.textContent = generatedLink
  document.getElementById('generatedLinkContainer').style.display = 'block'
  showMessage('Link de pagamento gerado com sucesso!', 'success')
})

// Máscara para o campo do telefone
document.getElementById('whatsapp').addEventListener('input', function (e) {
  const value = e.target.value.replace(/\D/g, '') // Remove caracteres não numéricos
  const formattedValue = value.replace(/(\d{2})(\d{5})(\d{4})/, '($1) $2-$3').replace(/^(\d{2})(\d)/, '($1) $2')
  e.target.value = formattedValue
})

// Máscara para o campo do valor
document.getElementById('linkValue').addEventListener('input', function (e) {
  let value = e.target.value.replace(/\D/g, '') // Remove caracteres não numéricos
  if (value) {
    value = (value / 100).toFixed(2).replace('.', ',') // Converte para formato de moeda
    e.target.value = 'R$ ' + value // Adiciona o símbolo de moeda
  } else {
    e.target.value = '' // Se não houver valor, limpa o campo
  }
})

function showMessage(text, type) {
  messageElement.textContent = text
  messageElement.className = `message ${type} fade-in-up`
  messageElement.style.opacity = '1'
  setTimeout(() => {
    messageElement.style.opacity = '0'
  }, 4000)
}