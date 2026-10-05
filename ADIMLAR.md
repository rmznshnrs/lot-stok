# Claude Code adımları

Her adımı sırayla ver. Bir adım çalışmadan sonrakine geçme.
Her adım bitince: "commit at" de.

## Adım 5 – Proje iskeleti
CLAUDE.md'yi oku. Django projesini klasör yapısına göre kur, git init yap,
requirements.txt oluştur, tüm modelleri ve admin kayıtlarını yaz, migration'ları
çalıştır. Test için örnek veri yükleyen ornek_veri komutunu yaz ve çalıştır.
Bana admin kullanıcısı oluşturmayı ve sunucuyu başlatmayı göster.

Kontrol: python manage.py runserver → http://127.0.0.1:8000/admin

## Adım 6 – İş mantığı
takip/services.py içinde şunları yaz ve her biri için tests.py'de test yaz:
- uretim_yap(urun, adet): FIFO lot düşümü, geri çağrılan lotları atlama,
  yetersiz stok kontrolü, seri no ataması, transaction.atomic
- uretim_onizle(urun, adet): kaydetmeden hangi lottan kaç adet düşüleceği ve eksikler
- lot_izle(lot_no): kullanıldığı üretimler (seri no) ve satıldığı müşteriler
- seri_izle(seri_no): kullanılan parçalar, lotlar, adetler, satış bilgisi
- satis_yap(musteri, seri_listesi): geri çağrılan lot içerenler için uyarı

Kontrol: python manage.py test

## Adım 7a – Temel arayüz
docs/mockup/ görsellerine göre base.html, üst menü ve style.css'i yap.
Parçalar ve Ürünler (ürün ağacı düzenleme dahil) ekranlarını yap.

## Adım 7b – Stok ve üretim
Stok ekranını (mal girişi + lot bazında/parça bazında liste) ve
Üretim ekranını (ürün seç, adet, önizleme, eksik kontrolü, kaydet) yap.

## Adım 7c – Satış, izlenebilirlik, ana sayfa
Satış ekranını, İzlenebilirlik ekranını (lot ve seri no sekmeleri) ve
Ana Sayfa'yı (lot/seri no'yu otomatik algılayan arama) yap.

## Adım 8 – Uçtan uca test
Şu senaryoyu çalıştır, hataları düzelt, README.md yaz:
1. 3 parça tanımla, İndikatör Paneli'ni bunlarla tanımla
2. Diyottan farklı tarihli 2 lot gir
3. 5 panel üret → en eski lottan düşmeli
4. 2 paneli bir müşteriye sat
5. Diyot lotunu geri çağır → müşteri ve depodaki paneller listelenmeli
6. Yeni üretim yap → geri çağrılan lot atlanmalı

## Hata olursa
Terminaldeki hata mesajını olduğu gibi kopyala ve şunu yaz:
"Şu hatayı aldım, düzelt: <hata>"
