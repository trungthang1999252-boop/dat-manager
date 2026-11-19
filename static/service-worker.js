const CACHE_NAME = 'dat-manager-v3.9';
const ASSETS_TO_CACHE = [
  '/manifest.json',
  '/static/icons/apple-touch-icon.png',
  '/static/icons/icon-192.png'
];

self.addEventListener('install', (event) => {
  self.skipWaiting();
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => {
      return cache.addAll(ASSETS_TO_CACHE);
    })
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) => {
      return Promise.all(
        keys.map((key) => {
          if (key !== CACHE_NAME) {
            console.log('[SW] Xóa cache cũ:', key);
            return caches.delete(key);
          }
        })
      );
    }).then(() => {
      return self.clients.claim();
    })
  );
});

self.addEventListener('fetch', (event) => {
  const url = event.request.url;

  // 1. Luôn bỏ qua API và WebSocket để không bao giờ cache dữ liệu động
  if (url.includes('/api/') || event.request.method !== 'GET') {
    return;
  }

  // 2. NETWORK-FIRST cho trang chủ HTML và Điều Hướng:
  // Đảm bảo điện thoại luôn lấy giao diện mới nhất trực tiếp từ máy chủ!
  if (event.request.mode === 'navigate' || event.request.destination === 'document' || url.endsWith('/') || url.includes('/?')) {
    event.respondWith(
      fetch(event.request)
        .then((networkResponse) => {
          if (networkResponse && networkResponse.status === 200) {
            const responseClone = networkResponse.clone();
            caches.open(CACHE_NAME).then((cache) => {
              cache.put(event.request, responseClone);
            });
          }
          return networkResponse;
        })
        .catch(() => {
          // Chỉ dùng cache khi điện thoại hoàn toàn mất mạng Internet
          return caches.match(event.request);
        })
    );
    return;
  }

  // 3. Stale-While-Revalidate cho các tài nguyên tĩnh khác
  event.respondWith(
    caches.match(event.request).then((cachedResponse) => {
      const fetchPromise = fetch(event.request).then((networkResponse) => {
        if (networkResponse && networkResponse.status === 200) {
          const responseClone = networkResponse.clone();
          caches.open(CACHE_NAME).then((cache) => {
            cache.put(event.request, responseClone);
          });
        }
        return networkResponse;
      }).catch(() => {});

      return cachedResponse || fetchPromise;
    })
  );
});
