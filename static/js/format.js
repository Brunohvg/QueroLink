function formatMoneyBRL(cents) {
    if (cents === null || cents === undefined || isNaN(cents)) return 'R$ 0,00';
    var value = cents / 100;
    var parts = value.toFixed(2).split('.');
    var intPart = parts[0].replace(/\B(?=(\d{3})+(?!\d))/g, '.');
    return 'R$ ' + intPart + ',' + parts[1];
}
