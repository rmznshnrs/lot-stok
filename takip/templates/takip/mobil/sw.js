/* Lot Takip service worker – sürüm {{ surum }}
 *
 * Sayfalar her zaman ağdan gelir (stok bilgisi güncel olmalı); ağ yoksa
 * çevrimdışı sayfası gösterilir. CSS, JS ve ikonlar önbellekten gelir.
 * Form gönderimleri (POST) ve veri istekleri asla önbelleğe alınmaz.
 */
const ONBELLEK = 'lot-takip-{{ surum }}';
const CEVRIMDISI = '{{ cevrimdisi_url }}';
const DOSYALAR = [CEVRIMDISI{% for d in dosyalar %}, '{{ d|escapejs }}'{% endfor %}];

self.addEventListener('install', (olay) => {
  olay.waitUntil(
    caches.open(ONBELLEK).then((c) => c.addAll(DOSYALAR)).then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (olay) => {
  olay.waitUntil(
    caches.keys()
      .then((adlar) => Promise.all(
        adlar.filter((ad) => ad.startsWith('lot-takip-') && ad !== ONBELLEK)
             .map((ad) => caches.delete(ad))
      ))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (olay) => {
  const istek = olay.request;
  if (istek.method !== 'GET') return;
  const url = new URL(istek.url);
  if (url.origin !== self.location.origin) return;

  if (istek.mode === 'navigate') {
    olay.respondWith(fetch(istek).catch(() => caches.match(CEVRIMDISI)));
    return;
  }
  if (DOSYALAR.includes(url.pathname)) {
    olay.respondWith(caches.match(istek).then((kayitli) => kayitli || fetch(istek)));
  }
});
