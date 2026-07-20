(function (global) {
    'use strict';

    global.parseJsonScript = function (elementId, fallback) {
        var element = document.getElementById(elementId);
        var parsed;
        try {
            parsed = JSON.parse(element ? (element.textContent || 'null') : 'null');
            if (typeof parsed === 'string') parsed = JSON.parse(parsed);
        } catch (error) {
            parsed = fallback;
        }
        if (Array.isArray(fallback)) {
            return Array.isArray(parsed) ? parsed : [];
        }
        if (fallback && typeof fallback === 'object') {
            return parsed && typeof parsed === 'object' && !Array.isArray(parsed)
                ? parsed : fallback;
        }
        return parsed == null ? fallback : parsed;
    };
}(window));
