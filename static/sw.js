// Service Worker: кэш app-shell, API никогда не кэшируем.
const CACHE = 'ai-botanik-v9';
const SHELL = [
  './',
  './index.html',
  './manifest.webmanifest',
  './templates.json',
  './css/styles.css',
  './js/app.js',
  './js/api.js',
  './js/db.js',
  './js/icons.js',
  './js/md.js',
  './js/ui.js',
  './icons/icon-192.png',
  './icons/icon-512.png',
  './icons/apple-touch-icon.png'
];

self.addEventListener('install', (e) => {
  e.waitUntil(
    caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (e) => {
  e.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (e) => {
  const url = new URL(e.request.url);
  // Запросы к API и cross-origin — только сеть, без перехвата.
  if (url.origin !== self.location.origin || url.pathname.includes('/api/')) return;
  if (e.request.method !== 'GET') return;

  // Навигация: сеть с фолбэком на закэшированный shell (офлайн-запуск).
  if (e.request.mode === 'navigate') {
    e.respondWith(
      fetch(e.request)
        .then((r) => {
          const cp = r.clone();
          caches.open(CACHE).then((c) => c.put('./index.html', cp));
          return r;
        })
        .catch(() => caches.match('./index.html'))
    );
    return;
  }

  // Статика: cache-first с фоновым обновлением.
  e.respondWith(
    caches.match(e.request).then((hit) => {
      if (hit) return hit;
      return fetch(e.request).then((r) => {
        if (r.ok) {
          const cp = r.clone();
          caches.open(CACHE).then((c) => c.put(e.request, cp));
        }
        return r;
      });
    })
  );
});
