function formatMoneyBRL(cents) {
    if (cents === null || cents === undefined || isNaN(cents)) return 'R$ 0,00';
    var value = cents / 100;
    var parts = value.toFixed(2).split('.');
    var intPart = parts[0].replace(/\B(?=(\d{3})+(?!\d))/g, '.');
    return 'R$ ' + intPart + ',' + parts[1];
}

function parseCivilDate(dateIso) {
    if (!dateIso || typeof dateIso !== 'string') return null;

    var parts = dateIso.split('-').map(Number);
    if (parts.length !== 3 || parts.some(Number.isNaN)) return null;

    var year = parts[0];
    var month = parts[1];
    var day = parts[2];
    return new Date(year, month - 1, day);
}

function formatCivilDate(dateIso, options) {
    var d = parseCivilDate(dateIso);
    if (!d) return '';
    return d.toLocaleDateString('pt-BR', options);
}
