var CACHE_NAME = 'merito-static-v1';
var STATIC_ASSETS = [
    '/static/js/alpine.min.js',
    '/static/js/format.js',
    '/static/icons/icon-192.png',
    '/static/icons/icon-512.png',
    '/static/icons/favicon-48.png',
];

var PRECACHE_PREFIX = 'merito-static-v';

self.addEventListener('install', function(event) {
    self.skipWaiting();
    if (self.registration.navigationPreload) {
        self.registration.navigationPreload.enable().catch(function(){});
    }
    event.waitUntil(
        caches.open(CACHE_NAME).then(function(cache) {
            return cache.addAll(STATIC_ASSETS);
        })
    );
});

self.addEventListener('activate', function(event) {
    event.waitUntil(
        caches.keys().then(function(keys) {
            return Promise.all(
                keys.filter(function(key) {
                    return key.indexOf(PRECACHE_PREFIX) === 0 && key !== CACHE_NAME;
                }).map(function(key) {
                    return caches.delete(key);
                })
            );
        }).then(function() {
            return self.clients.claim();
        })
    );
});

self.addEventListener('fetch', function(event) {
    if (event.request.method !== 'GET') return;

    var url = event.request.url;

    var isAuthHtml = url.indexOf('/dashboard/') >= 0
        && event.request.mode === 'navigate'
        && event.request.destination === 'document';

    var isApi = url.indexOf('/api/') >= 0;

    if (isAuthHtml || isApi) {
        event.respondWith(networkOnlyWithOfflineFallback(event));
        return;
    }

    if (url.indexOf('/static/') >= 0) {
        event.respondWith(staleWhileRevalidate(event));
        return;
    }

    event.respondWith(fetch(event.request));
});

function networkOnlyWithOfflineFallback(event) {
    if (self.registration.navigationPreload && event.preloadResponse) {
        return event.preloadResponse.then(function(response) {
            if (response) return response;
            return fetch(event.request).catch(offlineHtml);
        });
    }
    return fetch(event.request).catch(offlineHtml);
}

function staleWhileRevalidate(event) {
    return caches.match(event.request).then(function(cached) {
        var fetchPromise = fetch(event.request).then(function(response) {
            if (response && response.status === 200) {
                var clone = response.clone();
                caches.open(CACHE_NAME).then(function(cache) {
                    cache.put(event.request, clone);
                });
            }
            return response;
        });
        return cached || fetchPromise;
    });
}

var OFFLINE_HTML = '<!DOCTYPE html><html lang="pt"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Merito</title><style>body{font-family:-apple-system,sans-serif;display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0;background:#f8fafc;color:#2d3748;text-align:center;padding:20px}h1{color:#1463FF;font-size:1.5rem}p{color:#64748b;margin-top:.5rem}</style></head><body><div><h1>Sem conexao</h1><p>Verifique sua internet e tente novamente.</p></div></body></html>';

function offlineHtml() {
    return new Response(OFFLINE_HTML, { status: 200, headers: { 'Content-Type': 'text/html' } });
}

self.addEventListener('push', function(event) {
    var data = event.data ? event.data.json() : {};
    var title = data.title || 'Merito';
    var options = {
        body: data.body || '',
        icon: data.icon || '/static/icons/icon-192.png',
        badge: '/static/icons/favicon-48.png',
        data: { url: data.url || '/dashboard/mobile/' },
        vibrate: [100, 50, 100],
    };
    event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener('notificationclick', function(event) {
    event.notification.close();
    var url = event.notification.data && event.notification.data.url
        ? event.notification.data.url : '/dashboard/mobile/';
    event.waitUntil(clients.openWindow(url));
});
