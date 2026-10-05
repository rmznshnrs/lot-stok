from datetime import date, datetime

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from takip.models import (
    Musteri,
    Parca,
    Satis,
    SatisKalemi,
    StokLotu,
    Tedarikci,
    Uretim,
    UretimTuketim,
    Urun,
    UrunAgaci,
)
from takip.services import satis_yap, uretim_yap

PARCALAR = [
    # kod, ad, birim, min_stok
    ('R-10K', 'Direnç 10 kΩ 0805', 'adet', 200),
    ('LED-KR', 'LED kırmızı 3 mm', 'adet', 50),
    ('D-1N4007', 'Diyot 1N4007', 'adet', 30),
    ('PCB-IP', 'İndikatör paneli PCB', 'adet', 10),
    ('PCB-RK', 'Röle kartı PCB', 'adet', 10),
    ('KON-2P', 'Klemens konnektör 2 pin', 'adet', 40),
    ('ROLE-12', 'Röle 12 V', 'adet', 20),
]

TEDARIKCILER = [
    ('Elektro Komponent A.Ş.', 'satis@elektrokomponent.example · 0212 555 10 10'),
    ('Mikro Devre Ltd.', 'info@mikrodevre.example · 0216 555 20 20'),
    ('Anadolu PCB', 'siparis@anadolupcb.example · 0232 555 30 30'),
]

LOTLAR = [
    # lot_no, parça, tedarikçi, sipariş tarihi, sipariş no, giriş adedi, geri çağrıldı
    ('L-R-2501', 'R-10K', 'Elektro Komponent A.Ş.', date(2026, 1, 8), 'SP-1001', 1000, False),
    ('L-LED-2412', 'LED-KR', 'Mikro Devre Ltd.', date(2025, 12, 15), 'SP-0987', 20, False),
    ('L-LED-2602', 'LED-KR', 'Mikro Devre Ltd.', date(2026, 2, 20), 'SP-1023', 200, False),
    ('L-D-2601', 'D-1N4007', 'Elektro Komponent A.Ş.', date(2026, 1, 10), 'SP-1002', 30, False),
    ('L-D-2603', 'D-1N4007', 'Mikro Devre Ltd.', date(2026, 3, 5), 'SP-1040', 50, False),
    ('L-PCB-IP-01', 'PCB-IP', 'Anadolu PCB', date(2026, 1, 20), 'SP-1010', 25, False),
    ('L-PCB-RK-01', 'PCB-RK', 'Anadolu PCB', date(2026, 1, 20), 'SP-1011', 15, False),
    # En eski konnektör lotu geri çağrılmış: FIFO bu lotu atlamalı.
    ('L-KON-2511', 'KON-2P', 'Elektro Komponent A.Ş.', date(2025, 11, 3), 'SP-0950', 50, True),
    ('L-KON-2602', 'KON-2P', 'Elektro Komponent A.Ş.', date(2026, 2, 1), 'SP-1015', 100, False),
    ('L-ROLE-2602', 'ROLE-12', 'Mikro Devre Ltd.', date(2026, 2, 10), 'SP-1020', 30, False),
]

URUNLER = [
    # kod, ad, seri öneki, {parça: adet}
    ('IP', 'İndikatör Paneli', 'SN-IP-',
     {'LED-KR': 4, 'R-10K': 4, 'D-1N4007': 1, 'PCB-IP': 1, 'KON-2P': 1}),
    ('RK', 'Röle Kartı', 'SN-RK-',
     {'ROLE-12': 2, 'D-1N4007': 2, 'R-10K': 2, 'PCB-RK': 1, 'KON-2P': 2}),
]

URETIMLER = [
    # ürün kodu, adet, tarih
    ('IP', 4, datetime(2026, 4, 2, 10, 0)),
    ('RK', 3, datetime(2026, 4, 9, 14, 30)),
    ('IP', 2, datetime(2026, 5, 6, 9, 15)),  # LED eski lot biter, yeni lota geçer
]

MUSTERILER = [
    ('Atlas Otomasyon', 'satinalma@atlasotomasyon.example · 0224 555 40 40'),
    ('Beta Enerji', 'tedarik@betaenerji.example · 0312 555 50 50'),
]

SATISLAR = [
    # müşteri, tarih, seri numaraları
    ('Atlas Otomasyon', date(2026, 5, 12), ['SN-IP-0001', 'SN-IP-0002']),
    ('Beta Enerji', date(2026, 5, 20), ['SN-RK-0001']),
]


class Command(BaseCommand):
    help = 'Test için örnek parça, lot, ürün, üretim ve satış verisi yükler.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--sifirla',
            action='store_true',
            help='Mevcut tüm takip verisini silip örnek veriyi yeniden yükler.',
        )

    @transaction.atomic
    def handle(self, *args, **options):
        if options['sifirla']:
            self._sifirla()
        elif Parca.objects.exists():
            raise CommandError(
                'Veritabanında zaten veri var. Silip yeniden yüklemek için: '
                'python manage.py ornek_veri --sifirla'
            )

        parcalar = {
            kod: Parca.objects.create(kod=kod, ad=ad, birim=birim, min_stok=min_stok)
            for kod, ad, birim, min_stok in PARCALAR
        }
        tedarikciler = {
            ad: Tedarikci.objects.create(ad=ad, iletisim=iletisim)
            for ad, iletisim in TEDARIKCILER
        }
        for lot_no, parca, tedarikci, tarih, siparis_no, adet, geri in LOTLAR:
            StokLotu.objects.create(
                lot_no=lot_no, parca=parcalar[parca], tedarikci=tedarikciler[tedarikci],
                siparis_tarihi=tarih, siparis_no=siparis_no, giris_adet=adet,
                geri_cagrildi=geri,
            )

        urunler = {}
        for kod, ad, onek, agac in URUNLER:
            urun = Urun.objects.create(kod=kod, ad=ad, seri_oneki=onek)
            for parca, adet in agac.items():
                UrunAgaci.objects.create(urun=urun, parca=parcalar[parca], adet=adet)
            urunler[kod] = urun

        for urun_kodu, adet, tarih in URETIMLER:
            uretim_yap(urunler[urun_kodu], adet, tarih=timezone.make_aware(tarih))

        musteriler = {
            ad: Musteri.objects.create(ad=ad, iletisim=iletisim)
            for ad, iletisim in MUSTERILER
        }
        for musteri_ad, tarih, seriler in SATISLAR:
            satis_yap(musteriler[musteri_ad], seriler, tarih=tarih)

        self.stdout.write(self.style.SUCCESS(
            f'Örnek veri yüklendi: {Parca.objects.count()} parça, '
            f'{StokLotu.objects.count()} lot, {Urun.objects.count()} ürün, '
            f'{Uretim.objects.count()} üretim, {Satis.objects.count()} satış.'
        ))

    def _sifirla(self):
        # PROTECT ilişkileri nedeniyle bağımlı kayıtlardan başlayarak sil.
        for model in (SatisKalemi, Satis, Musteri, UretimTuketim, Uretim,
                      UrunAgaci, Urun, StokLotu, Tedarikci, Parca):
            model.objects.all().delete()
