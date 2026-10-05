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
                giris_adet, kalan_adet, geri_cagrildi)
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
kalan_adet boş bırakılırsa giris_adet ile dolar. Admin'de Uretim/UretimTuketim salt
okunur (üretim yalnızca services ile); üretim ve satış kayıtları admin'den silinemez.
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

Tasarım: zemin #F6F5F1, kart #FFFFFF, kenar #E2DFD7, ana renk #1F4E8C,
uyarı/geri çağırma #FDEBD9 / #8A3B00, font IBM Plex Sans, kod/lot/seri IBM Plex Mono.

## Teknik
- Python 3 + Django, SQLite (ileride PostgreSQL)
- Django şablonları + tek CSS dosyası, gerektiğinde az JavaScript
- Django admin açık kalsın
- Arayüz metinleri ve model adları Türkçe

## Klasör yapısı
```
lot-takip/
  CLAUDE.md, requirements.txt, manage.py
  config/                 settings, urls
  takip/                  models, admin, services, forms, views, urls, tests
    templates/takip/      base.html + ekran şablonları
    static/takip/style.css
    management/commands/ornek_veri.py
  docs/mockup/
```

## Çalışma şekli
- Her adımdan sonra çalışır durumda bırak, nasıl test edileceğini söyle.
- Şemayı değiştirirsen bu dosyayı güncelle.
