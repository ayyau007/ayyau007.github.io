const CACHE_NAME = 'badminton-cache-v1';
const ASSETS = [
  '/',
  '/badminton.html',
  '/badminton.js',
  '/badminton-manifest.json',
  '/sw.js'
];
self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE_NAME).then(cache => cache.addAll(ASSETS))
  );
});
self.addEventListener('fetch', event => {
  event.respondWith(
    caches.match(event.request).then(resp => resp || fetch(event.request))
  );
});
