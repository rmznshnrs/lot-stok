# Lot Takip Sistemi

Elektronik ürün üreten bir firma için stok, üretim ve satışı **lot / seri numarası
bazında** izleyen web uygulaması. Excel'de tutulan takibin yerini alır.

İki soruyu birkaç tıklamayla yanıtlar:

- **Parçadan bütüne:** Bu lottaki parça ne zaman, kimden geldi? Hangi ürünlerde (seri no)
  kullanıldı? O ürünler kime satıldı?
- **Bütünden parçaya:** Bu seri numaralı ürünün içinde hangi parçalar, hangi lotlardan,
  kaçar adet var?

## Kurulum

Gereken: Python 3.11 veya üstü. Komutlar proje klasöründe çalıştırılır (Windows örneği).

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt
.venv/Scripts/python manage.py migrate
.venv/Scripts/python manage.py createsuperuser
.venv/Scripts/python manage.py runserver
```

Ardından tarayıcıda **http://127.0.0.1:8000** adresini açın. Yönetim paneli
**http://127.0.0.1:8000/admin** adresindedir; `createsuperuser` ile oluşturduğunuz
kullanıcıyla girilir.

macOS / Linux'ta `.venv/Scripts/python` yerine `.venv/bin/python` yazın.

### Örnek veri

Denemek için hazır veri yüklenebilir. Yüklenen veri: 7 parça, 3 tedarikçi, 10 lot
(biri geri çağrılmış), 2 ürün, 9 üretim ve 2 satış.

```bash
.venv/Scripts/python manage.py ornek_veri
```

Veritabanında zaten kayıt varsa komut durur. `--sifirla` tüm takip verisini silip
örnek veriyi yeniden yükler (kullanıcı hesapları silinmez):

```bash
.venv/Scripts/python manage.py ornek_veri --sifirla
```

## Kullanım

Günlük akış: **Parça tanımla → Ürün ve ürün ağacı → Mal girişi → Üretim → Satış**.
Bir sorun çıktığında **İzlenebilirlik** ekranından lot veya seri no ile geri çağırma yapılır.

| Ekran | Ne yapılır |
|---|---|
| **Ana Sayfa** | Büyük arama kutusuna lot no, seri no veya parça kodu yazılır; sistem türünü kendisi anlar ve ilgili ekranı açar. Büyük/küçük harf önemsizdir. Tam eşleşme yoksa numarayı içeren adaylar listelenir. Ayrıca 3 hızlı işlem kartı ve son hareketler (giriş, üretim, satış) vardır. |
| **Parçalar** | Parça tanımlanır (kod, ad, birim, minimum stok). Listede kullanılabilir stok, lot sayısı ve parçanın kullanıldığı ürünler görünür. Stoğu minimumun altına düşen parçalar turuncu vurgulanır. |
| **Ürünler** | Ürün (ad, kod, seri no öneki) ve ürün ağacı düzenlenir. Ağaçta 1 ürün için hangi parçadan kaç adet gerektiği tutulur. Parça, adıyla veya koduyla eklenebilir. Üretim yapılmış ürünün seri no öneki değiştirilemez. |
| **Stok** | Mal girişi: parça, tedarikçi, lot no, adet, sipariş tarihi. Yeni bir tedarikçi adı yazılırsa otomatik tanımlanır. Liste lot bazında veya parça bazında görüntülenir; toplam ve kullanılabilir adet ayrı gösterilir. Üretimde ilk kullanılacak lot **sıradaki** etiketi taşır, geri çağrılan lotlar turuncu gösterilir. |
| **Üretim** | Ürün ve adet seçilir. Sistem kaydetmeden önce hangi lottan kaç parça düşüleceğini, eksikleri, atlanacak geri çağrılmış lotları ve verilecek seri numaralarını gösterir. Stok yetmiyorsa **Üretimi kaydet** pasiftir. |
| **Satış** | Müşteri ve tarih seçilir, seri numaraları eklenir. Barkod okuyucu için tek seferde birden fazla numara girilebilir. Geri çağrılan lot içeren ürün listede uyarıyla gösterilir; böyle bir ürün varsa satış ancak onay kutusu işaretlenince kaydedilir. Ekranda satış geçmişi de bulunur. |
| **İzlenebilirlik** | İki sekme vardır. **Lot → Ürün → Müşteri** sekmesinde lotun kullanıldığı seri numaraları (satıldı / depoda), etkilenen müşteriler ve iletişim bilgileri ile **Bu lotu geri çağır** düğmesi bulunur. **Seri No → Parçalar** sekmesinde ürünün içindeki parçalar; lot, tedarikçi ve adetleriyle listelenir. |

## İş kuralları

- **FIFO:** Üretimde parçalar sipariş tarihi en eski lottan düşülür; gerekirse bir parça
  birden fazla lottan karşılanır.
- **Geri çağrılan lotlar** üretimde kullanılmaz.
- **Yetersiz stokta** üretim kaydedilmez; eksik parçalar ve adetleri gösterilir.
- **Seri numaraları:** N adet üretim N ayrı kayıt ve ardışık seri no oluşturur
  (`SN-IP-0001`, `SN-IP-0002`, …).
- **Tek işlem:** Üretim ve stok düşümü tek veritabanı işleminde yapılır; bir hata olursa
  hiçbir şey kaydedilmez.
- **Satışta geri çağrılan lot:** Böyle bir lot içeren seri no satışa eklenirse uyarı
  gösterilir.
- **Silme yok:** Üretim ve satış kayıtları silinemez. Üretim kayıtları yönetim panelinde
  salt okunurdur; yalnızca Üretim ekranından oluşturulur.
- **Ürün ağacı değişirse** eski üretimler bozulmaz: her üretimin hangi lottan kaç parça
  kullandığı ayrıca saklanır.

## Testler

```bash
.venv/Scripts/python manage.py test
```

Testler iş mantığını (`takip/services.py`), formları ve tüm ekranları kapsar.
`UcanUcaSenaryoTest`, aşağıdaki senaryoyu boş bir veritabanında yalnızca ekranlardaki
formları göndererek baştan sona çalıştırır:

1. 3 parça tanımla, İndikatör Paneli'ni bunlarla tanımla.
2. Diyottan farklı tarihli 2 lot gir.
3. 5 panel üret: diyot en eski lottan düşülür.
4. 2 paneli bir müşteriye sat.
5. Diyot lotunu geri çağır: müşteri ve depodaki 3 panel listelenir.
6. Yeni üretim yap: geri çağrılan lotta parça kalmış olsa da atlanır.

### Ayrı bir veritabanıyla deneme

`DJANGO_DB` ortam değişkeni farklı bir SQLite dosyası gösterirse asıl veritabanına
dokunulmaz (PowerShell):

```powershell
$env:DJANGO_DB = "deneme.sqlite3"
.venv/Scripts/python manage.py migrate
.venv/Scripts/python manage.py runserver
```

## Teknik

- Python 3 + Django 5.2, SQLite (ileride PostgreSQL'e geçilebilir).
- Django şablonları, tek CSS dosyası, çok az JavaScript. Kayıt işlemleri JavaScript
  olmadan da çalışır; JavaScript yalnızca kolaylık sağlar (adet −/+ düğmeleri,
  onay pencereleri, çift tıklama koruması).
- İş mantığı `takip/services.py` içindedir; görünümler (`views.py`) yalnızca bu
  fonksiyonları çağırır.
- Tasarımın kaynağı `docs/mockup/` içindeki onaylı V2 ekranlarıdır.
- Arayüz ve model adları Türkçedir.

```
config/                      ayarlar, ana URL'ler
takip/
  models.py                  Parca, Tedarikci, StokLotu, Urun, UrunAgaci, Uretim,
                             UretimTuketim, Musteri, Satis, SatisKalemi
  services.py                FIFO üretim, izlenebilirlik, satış, stok özeti
  forms.py, views.py, urls.py, admin.py
  tests.py
  templates/takip/           base.html + ekran şablonları
  static/takip/style.css
  management/commands/ornek_veri.py
docs/mockup/                 onaylı tasarım (V2*.dc.html)
CLAUDE.md                    şema ve kurallar (geliştirme notları)
```
