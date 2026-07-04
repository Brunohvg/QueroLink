var CACHE_NAME = 'querolink-static-v2';
var ASSETS = [
    '/static/js/alpine.min.js',
    '/static/js/format.js',
    '/static/icons/icon-192.png',
    '/static/icons/icon-512.png',
];

var AUTH_PATHS = [
    '/dashboard/',
    '/api/',
];

function isAuthPath(url) {
    return AUTH_PATHS.some(function(path) {
        return url.indexOf(path) >= 0;
    });
}

function isStaticAsset(url) {
    return ASSETS.some(function(asset) {
        return url.indexOf(asset) >= 0;
    }) || url.indexOf('/static/') >= 0;
}

self.addEventListener('install', function(event) {
    event.waitUntil(
        caches.open(CACHE_NAME).then(function(cache) {
            return cache.addAll(ASSETS);
        })
    );
});

self.addEventListener('activate', function(event) {
    event.waitUntil(
        caches.keys().then(function(keys) {
            return Promise.all(
                keys.filter(function(key) { return key !== CACHE_NAME; })
                    .map(function(key) { return caches.delete(key); })
            );
        })
    );
});

self.addEventListener('fetch', function(event) {
    if (event.request.method !== 'GET') return;

    var url = event.request.url;

    if (isAuthPath(url)) {
        event.respondWith(fetch(event.request).catch(function() {
            return new Response(
                '<!DOCTYPE html><html lang="pt"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Mérito</title><style>body{font-family:-apple-system,sans-serif;display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0;background:#f8fafc;color:#2d3748;text-align:center;padding:20px}h1{color:#1463FF;font-size:1.5rem}p{color:#64748b;margin-top:.5rem}</style></head><body><div><h1>Sem conexao</h1><p>Pagina nao disponivel offline.</p></div></body></html>',
                { status: 200, headers: { 'Content-Type': 'text/html' } }
            );
        }));
        return;
    }

    event.respondWith(
        caches.match(event.request).then(function(cached) {
            var fetchPromise = fetch(event.request).then(function(response) {
                if (response && response.status === 200 && isStaticAsset(url)) {
                    var clone = response.clone();
                    caches.open(CACHE_NAME).then(function(cache) {
                        cache.put(event.request, clone);
                    });
                }
                return response;
            }).catch(function() {
                return cached || new Response(
                    '<!DOCTYPE html><html lang="pt"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Mérito</title><style>body{font-family:-apple-system,sans-serif;display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0;background:#f8fafc;color:#2d3748;text-align:center;padding:20px}h1{color:#1463FF;font-size:1.5rem}p{color:#64748b;margin-top:.5rem}</style></head><body><div><h1>Sem conexao</h1><p>Verifique sua internet e tente novamente.</p></div></body></html>',
                    { status: 200, headers: { 'Content-Type': 'text/html' } }
                );
            });
            return cached || fetchPromise;
        })
    );
});