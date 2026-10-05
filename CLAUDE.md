# Lot Takip Sistemi

Elektronik ürün üreten bir firma için stok, üretim ve satış takibi yapan,
lot/seri numarası bazlı izlenebilirlik sağlayan web uygulaması.
Şu an bu işler Excel'de yapılıyor; sistem Excel'in yerini alacak.

## Amaç
- Parçadan bütüne: bu lottaki parça ne zaman, nereden alındı; hangi ürünlerde
  (seri no) kullanıldı; o ürünler kime satıldı?
- Bütünden parçaya: bu seri no'lu ürünün içinde hangi parçalar, hangi lotlardan,
  kaçar adet var?

## İş akışı
Parça tanımla → Ürün tanımla + ürün ağacı → Mal girişi (lot) → Üretim (FIFO, seri no)
→ Satış → İzlenebilirlik / geri çağırma

## Veritabanı şeması
```
Parca          (kod, ad, birim, min_stok)
Tedarikci      (ad, iletisim)
StokLotu       (parca, tedarikci, lot_no, siparis_tarihi, siparis_no,
                giris_adet, kalan_adet, geri_cagrildi, giris_zamani)
Urun           (kod, ad, seri_oneki)            -- ör. IP, "SN-IP-"
UrunAgaci      (urun, parca, adet)              -- 1 ürün için gereken parça
Uretim         (urun, seri_no, tarih)           -- her ürün tek seri no
UretimTuketim  (uretim, stok_lotu, adet)        -- izlenebilirliğin kalbi
Musteri        (ad, iletisim)
Satis          (musteri, tarih)
SatisKalemi    (satis, uretim)                  -- satılan seri numaraları
```
Notlar: lot_no, seri_no, Parca.kod, Urun.kod/seri_oneki tekil. SatisKalemi.uretim
OneToOne (bir seri no bir kez satılır). İlişkiler PROTECT (UrunAgaci.urun hariç).
kalan_adet boş bırakılırsa giris_adet ile dolar. giris_zamani mal girişinin
kaydedildiği an (son hareketlerde kullanılır); FIFO yine siparis_tarihi'ne göredir.
Admin'de Uretim/UretimTuketim salt okunur (üretim yalnızca services ile); üretim ve
satış kayıtları admin'den silinemez.
Sanal ortam: .venv (`.venv\Scripts\python manage.py ...`).

## Kurallar
- Stok düşümü FIFO: siparis_tarihi en eski lot önce. Bir parça için gerekirse
  birden fazla lottan düşülür.
- Geri çağrılan lotlar (geri_cagrildi=True) üretimde kullanılmaz.
- Yetersiz stokta üretim kaydedilmez; eksik parçalar ve adetleri gösterilir.
- Üretimde N adet istenirse N ayrı Uretim kaydı ve ardışık seri no oluşur
  (seri_oneki + 4 haneli sıra: SN-IP-0001).
- Üretim + stok düşümü tek transaction (transaction.atomic).
- Geri çağrılan lot içeren seri no satışa eklenirse uyarı gösterilir.
- Üretim ve satış kayıtları silinmez.
- Ürün ağacı değişse de eski üretimler UretimTuketim sayesinde doğru kalır.
- İş mantığı views.py'de değil takip/services.py'de; her fonksiyonun testi tests.py'de.

## Ekranlar (mockup: docs/mockup/)
Üst menülü düzen: Ana Sayfa, İzlenebilirlik, Üretim, Stok, Satış, Ürünler, Parçalar.
- Ana Sayfa: büyük arama kutusu (lot/seri no otomatik algılanır), 3 hızlı işlem, son hareketler
- İzlenebilirlik: iki sekme — Lot → Ürün → Müşteri (3 sütun akış) ve Seri No → Parçalar
- Üretim: ürün seç → adet → parça kontrolü (FIFO lot önizleme, eksikler) → kaydet
- Stok: mal girişi formu + stok listesi (lot bazında / parça bazında, toplam ve kullanılabilir)
- Satış: müşteri, tarih, seri no ekleme + satış geçmişi
- Ürünler: ürün listesi + ürün ağacı düzenleme
- Parçalar: parça tanımlama + parça listesi (toplam stok, lot sayısı, kullanıldığı ürünler)

## Mobil (PWA, /m/) (mockup: docs/mockup/Mobile*.dc.html)
Alt sekme menülü 3 ekran; telefonda ana ekrana eklenebilir. Masaüstüyle aynı
services.py'yi kullanır, ayrı iş mantığı yoktur.
- Sorgula (/m/): kamera ile barkod/karekod okutma veya elle giriş; numara
  numara_ara ile algılanır. Lot → kullanıldığı ürünler ve müşteriler özeti;
  seri no → içindeki parçalar ve lotlar; parça kodu → stoktaki lotlar.
- Mal girişi (/m/giris/): parça kod/adla yazılır (parca_bul), lot no kamerayla
  okutulabilir (Tara), mal_girisi ile kaydedilir.
- Satış (/m/satis/): müşteri, tarih; seri numaraları okutulur veya elle girilir.
  Her numara /m/api/seri/ ile (satilabilir_uretim + satis_uyarilari) sayfa
  yenilemeden kontrol edilir; geri çağrılan lot içeren ürün uyarıyla gösterilir ve
  onay kutusu işaretlenmeden satış kaydedilmez. Kayıt masaüstüyle ortak
  views.satis_formu_isle ile yapılır.
- Kod: views_mobil.py, templates/takip/mobil/, static/takip/mobil.js.
  Barkod: tarayıcının BarcodeDetector'ı varsa o, yoksa ZXing
  (static/takip/vendor/zxing.min.js, Apache-2.0, projeye kopyalandı).
- PWA: /m/manifest.json, /m/sw.js (kapsam /m/), ikonlar static/takip/ikon/.
  Service worker sayfaları her zaman ağdan alır (ağ yoksa /m/cevrimdisi/),
  yalnız CSS/JS/ikonları önbellekler; POST ve API isteklerini önbelleğe almaz.
  Önbellek sürümü dosyaların değişiklik zamanından hesaplanır; dosya değişince
  telefon yeni sürümü kendisi alır.
- Kamera yalnızca HTTPS (veya localhost) üzerinde açılır; JavaScript/kamera yoksa
  her ekran elle girişle çalışır.

Tasarım: zemin #F6F5F1, kart #FFFFFF, kenar #E2DFD7, ana renk #1F4E8C,
uyarı/geri çağırma #FDEBD9 / #8A3B00, font IBM Plex Sans, kod/lot/seri IBM Plex Mono.

## Teknik
- Python 3 + Django, SQLite (ileride PostgreSQL)
- Django şablonları + tek CSS dosyası, gerektiğinde az JavaScript
- Django admin açık kalsın
- Ortam değişkenleri: DJANGO_DB (başka SQLite dosyası), DJANGO_DEBUG,
  DJANGO_SECRET_KEY, DJANGO_ALLOWED_HOSTS ve DJANGO_CSRF_TRUSTED_ORIGINS
  (telefonda HTTPS tüneliyle deneme için, virgülle ayrılır)
- İkonlar Pillow ile üretildi; Pillow çalışma zamanında gerekmez (requirements'ta yok)
- Arayüz metinleri ve model adları Türkçe

## Klasör yapısı
```
lot-takip/
  CLAUDE.md, requirements.txt, manage.py
  config/                 settings, urls
  takip/                  models, admin, services, forms, views, views_mobil, urls, tests
    templates/takip/      base.html + ekran şablonları
      mobil/              mobil base.html, sorgula, giris, satis, cevrimdisi, sw.js
    static/takip/style.css   (masaüstü + mobil, tek dosya)
    static/takip/mobil.js    kamera / barkod okuma, satış listesi, SW kaydı
    static/takip/vendor/     zxing.min.js + lisansı
    static/takip/ikon/       PWA ikonları
    management/commands/ornek_veri.py
  docs/mockup/
```

## Çalışma şekli
- Her adımdan sonra çalışır durumda bırak, nasıl test edileceğini söyle.
- Şemayı değiştirirsen bu dosyayı güncelle.
