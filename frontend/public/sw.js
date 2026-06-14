const CACHE_NAME = 'llmdash-v2'
const STATIC_ASSETS = [
  '/',
  '/manifest.json',
]

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => {
      return cache.addAll(STATIC_ASSETS)
    })
  )
})

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) => {
      return Promise.all(
        keys
          .filter((k) => k !== CACHE_NAME)
          .map((k) => caches.delete(k))
      )
    })
  )
})

self.addEventListener('fetch', (event) => {
  if (event.request.method !== 'GET') return
  event.respondWith(
    fetch(event.request)
      .then((response) => {
        const clone = response.clone()
        if (
          response.type === 'basic' ||
          response.type === 'cors' ||
          response.type === 'opaque'
        ) {
          caches.open(CACHE_NAME).then((cache) => {
            try { cache.put(event.request, clone) } catch {}
          })
        }
        return response
      })
      .catch(() => {
        return caches.match(event.request)
      })
  )
})
