const CACHE_NAME = 'tznobar-cache-v12';
const APP_SHELL = [
  '/',
  '/index.html',
  '/products.json',
  '/products_clean.json',
  '/manifest.webmanifest',
  '/icon-wg-192.png',
  '/icon-wg-512.png',
  '/icon-wg-512-maskable.png',
  '/logo-white.png',
  '/logo-black.png',
  '/logo.png',
  '/lib/jspdf.umd.min.js',
  '/lib/html2canvas.min.js'
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(APP_SHELL))
  );
  self.skipWaiting();
});

self.addEventListener('message', (event) => {
  if (event.data && event.data.type === 'SKIP_WAITING') {
    self.skipWaiting();
  }
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) => Promise.all(
      keys
        .filter((key) => key !== CACHE_NAME)
        .map((key) => caches.delete(key))
    ))
  );
  self.clients.claim();
});

self.addEventListener('fetch', (event) => {
  if (event.request.method !== 'GET') return;

  const requestUrl = new URL(event.request.url);
  const isSameOrigin = requestUrl.origin === self.location.origin;
  const isNavigation = event.request.mode === 'navigate';
  const isCatalog = isSameOrigin && /\/products(_clean)?\.json$/.test(requestUrl.pathname);

  event.respondWith(
    (async () => {
      // Keep HTML fresh so new deployments are visible immediately.
      if (isNavigation) {
        try {
          const networkResponse = await fetch(event.request);
          if (networkResponse && networkResponse.status === 200 && isSameOrigin) {
            const cache = await caches.open(CACHE_NAME);
            cache.put(event.request, networkResponse.clone());
          }
          return networkResponse;
        } catch {
          return (await caches.match(event.request)) || (await caches.match('/index.html')) || Response.error();
        }
      }

      // Catalog data: network-first so product updates show without a cache bump.
      if (isCatalog) {
        try {
          const networkResponse = await fetch(event.request, { cache: 'no-store' });
          if (networkResponse && networkResponse.status === 200) {
            const cache = await caches.open(CACHE_NAME);
            cache.put(event.request, networkResponse.clone());
          }
          return networkResponse;
        } catch {
          return (await caches.match(event.request)) || Response.error();
        }
      }

      const cached = await caches.match(event.request);
      if (cached) return cached;

      try {
        const networkResponse = await fetch(event.request);
        if (networkResponse && networkResponse.status === 200 && networkResponse.type === 'basic' && isSameOrigin) {
          const cache = await caches.open(CACHE_NAME);
          cache.put(event.request, networkResponse.clone());
        }
        return networkResponse;
      } catch {
        return Response.error();
      }
    })()
  );
});
