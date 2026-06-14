// Service worker: app-shell caching for offline use and faster loads.
// API responses are always fetched fresh (network-first); static assets are
// cache-first.

const CACHE = 'smt-v2';
// '/' is intentionally omitted: it now redirects to the configurable home page,
// and a redirect response can't be reliably cached.
const SHELL = [
  '/static/style.css',
  '/static/app.js',
  '/static/icon.svg',
  '/manifest.webmanifest',
];

self.addEventListener('install', function (event) {
  event.waitUntil(
    caches.open(CACHE).then(function (cache) {
      return cache.addAll(SHELL).catch(function () {});
    })
  );
  self.skipWaiting();
});

self.addEventListener('activate', function (event) {
  event.waitUntil(
    caches.keys().then(function (keys) {
      return Promise.all(keys.filter(function (k) { return k !== CACHE; })
        .map(function (k) { return caches.delete(k); }));
    })
  );
  self.clients.claim();
});

self.addEventListener('fetch', function (event) {
  const req = event.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);

  // Never cache API calls -- the data must be current.
  if (url.pathname.startsWith('/api/')) {
    event.respondWith(fetch(req).catch(function () {
      return new Response('{"error":"offline"}', {
        headers: { 'Content-Type': 'application/json' },
        status: 503,
      });
    }));
    return;
  }

  // Cache-first for everything else, falling back to network and caching it.
  event.respondWith(
    caches.match(req).then(function (cached) {
      if (cached) return cached;
      return fetch(req).then(function (res) {
        if (res.ok && url.origin === self.location.origin) {
          const copy = res.clone();
          caches.open(CACHE).then(function (cache) { cache.put(req, copy); });
        }
        return res;
      });
    })
  );
});
