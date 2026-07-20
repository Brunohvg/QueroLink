(function () {
    'use strict';
    function idempotencyKey() {
        if (window.crypto && typeof window.crypto.randomUUID === 'function') {
            return window.crypto.randomUUID();
        }
        return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function (c) {
            var r = Math.random() * 16 | 0;
            return (c === 'x' ? r : (r & 3 | 8)).toString(16);
        });
    }
    function fillEmpty(target, data, fields) {
        fields.forEach(function (field) {
            if (!String(target[field] || '').trim() && data[field]) target[field] = data[field];
        });
    }
    function lookup(url) {
        return fetch(url, {headers: {'Accept': 'application/json'}}).then(function (response) {
            return response.json().catch(function () { return {}; }).then(function (data) {
                if (!response.ok) throw new Error(data.detail || 'Consulta indisponível. Preencha manualmente.');
                return data;
            });
        });
    }
    window.MeritoBoleto = {idempotencyKey: idempotencyKey, fillEmpty: fillEmpty, lookup: lookup};
}());
