function formatMoneyBRL(cents) {
    if (cents === null || cents === undefined || isNaN(cents)) return 'R$ 0,00';
    const value = cents / 100;
    return value.toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' });
}
