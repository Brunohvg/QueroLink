var CACHE_NAME = 'querolink-v1';
var ASSETS = [
    '/dashboard/mobile/',
    '/static/icons/icon-192.png',
    '/static/icons/icon-512.png',
];

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

    event.respondWith(
        caches.match(event.request).then(function(cached) {
            return cached || fetch(event.request).then(function(response) {
                if (response && response.status === 200 &&
                    (response.headers.get('content-type') || '').includes('text/html')) {
                    var clone = response.clone();
                    caches.open(CACHE_NAME).then(function(cache) {
                        cache.put(event.request, clone);
                    });
                }
                return response;
            }).catch(function() {
                return new Response(
                    '<!DOCTYPE html><html lang="pt"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>QueroLink</title><style>body{font-family:-apple-system,sans-serif;display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0;background:#f8fafc;color:#2d3748;text-align:center;padding:20px}h1{color:#4361ee;font-size:1.5rem}p{color:#64748b;margin-top:.5rem}</style></head><body><div><h1>Sem conexao</h1><p>Voce esta offline. Verifique sua internet e tente novamente.</p></div></body></html>',
                    { status: 200, headers: { 'Content-Type': 'text/html' } }
                );
            });
        })
    );
});
