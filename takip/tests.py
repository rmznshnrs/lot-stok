from datetime import date
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.test import TestCase

from . import services
from .models import (
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
from .services import SatisHatasi, YetersizStok


class TemelVeri(TestCase):
    """İndikatör Paneli: 4 LED + 1 diyot. LED tek lot, diyot üç lot (biri geri çağrılmış)."""

    def setUp(self):
        self.ted = Tedarikci.objects.create(ad='Tedarikçi A')
        self.led = Parca.objects.create(kod='LED', ad='LED')
        self.diyot = Parca.objects.create(kod='DIY', ad='Diyot')
        self.urun = Urun.objects.create(kod='IP', ad='İndikatör Paneli', seri_oneki='SN-IP-')
        UrunAgaci.objects.create(urun=self.urun, parca=self.led, adet=4)
        UrunAgaci.objects.create(urun=self.urun, parca=self.diyot, adet=1)

        self.led_lot = self.lot('L-LED', self.led, date(2026, 1, 1), 100)
        # Sıra kasıtlı karışık: FIFO id'ye değil sipariş tarihine bakmalı.
        self.diyot_yeni = self.lot('L-D-YENI', self.diyot, date(2026, 3, 1), 10)
        self.diyot_eski = self.lot('L-D-ESKI', self.diyot, date(2026, 1, 1), 3)
        self.diyot_geri = self.lot('L-D-GERI', self.diyot, date(2025, 6, 1), 50, geri=True)

        self.musteri = Musteri.objects.create(ad='Atlas')

    def lot(self, lot_no, parca, tarih, adet, geri=False):
        return StokLotu.objects.create(
            lot_no=lot_no, parca=parca, tedarikci=self.ted,
            siparis_tarihi=tarih, giris_adet=adet, geri_cagrildi=geri,
        )

    def kalan(self, lot):
        lot.refresh_from_db()
        return lot.kalan_adet


class UretimOnizleTest(TemelVeri):
    def test_fifo_dagilimi_ve_geri_cagrilan_lot_atlanir(self):
        o = services.uretim_onizle(self.urun, 5)
        self.assertTrue(o.yeterli)
        diyot = next(p for p in o.parcalar if p.parca == self.diyot)
        self.assertEqual(diyot.gereken, 5)
        self.assertEqual(diyot.kullanilabilir, 13)  # geri çağrılan 50 sayılmaz
        self.assertEqual(
            [(d.lot.lot_no, d.adet) for d in diyot.lotlar],
            [('L-D-ESKI', 3), ('L-D-YENI', 2)],
        )
        led = next(p for p in o.parcalar if p.parca == self.led)
        self.assertEqual([(d.lot.lot_no, d.adet) for d in led.lotlar], [('L-LED', 20)])

    def test_eksikler(self):
        o = services.uretim_onizle(self.urun, 30)  # 120 LED, 30 diyot gerekir
        self.assertFalse(o.yeterli)
        eksikler = {p.parca.kod: p.eksik for p in o.eksikler}
        self.assertEqual(eksikler, {'LED': 20, 'DIY': 17})

    def test_hicbir_sey_kaydetmez(self):
        services.uretim_onizle(self.urun, 5)
        self.assertEqual(Uretim.objects.count(), 0)
        self.assertEqual(self.kalan(self.diyot_eski), 3)

    def test_gecersiz_adet_ve_bos_agac(self):
        with self.assertRaises(ValueError):
            services.uretim_onizle(self.urun, 0)
        bos = Urun.objects.create(kod='BOS', ad='Boş', seri_oneki='SN-B-')
        with self.assertRaises(ValueError):
            services.uretim_onizle(bos, 1)


class UretimYapTest(TemelVeri):
    def test_n_kayit_ve_ardisik_seri_no(self):
        uretimler = services.uretim_yap(self.urun, 3)
        self.assertEqual(
            [u.seri_no for u in uretimler], ['SN-IP-0001', 'SN-IP-0002', 'SN-IP-0003']
        )
        uretimler = services.uretim_yap(self.urun, 2)
        self.assertEqual([u.seri_no for u in uretimler], ['SN-IP-0004', 'SN-IP-0005'])

    def test_seri_no_en_buyuk_siradan_devam_eder(self):
        Uretim.objects.create(urun=self.urun, seri_no='SN-IP-0041')
        self.assertEqual(services.uretim_yap(self.urun, 1)[0].seri_no, 'SN-IP-0042')

    def test_fifo_dusum_birden_fazla_lottan(self):
        uretimler = services.uretim_yap(self.urun, 5)
        self.assertEqual(self.kalan(self.diyot_eski), 0)
        self.assertEqual(self.kalan(self.diyot_yeni), 8)
        self.assertEqual(self.kalan(self.led_lot), 80)
        # İlk 3 ürün eski lottan, sonraki 2 yeni lottan diyot alır.
        lotlar = [
            u.tuketimler.get(stok_lotu__parca=self.diyot).stok_lotu.lot_no for u in uretimler
        ]
        self.assertEqual(lotlar, ['L-D-ESKI'] * 3 + ['L-D-YENI'] * 2)

    def test_bir_urun_icin_lot_bolunur(self):
        # LED: 6'lık eski lot + yeni lot; 2. ürünün 4 LED'i iki lottan gelmeli.
        self.led_lot.kalan_adet = 0
        self.led_lot.save()
        eski = self.lot('L-LED-ESKI', self.led, date(2025, 1, 1), 6)
        yeni = self.lot('L-LED-YENI', self.led, date(2025, 2, 1), 50)
        u1, u2 = services.uretim_yap(self.urun, 2)
        self.assertEqual(
            sorted((t.stok_lotu.lot_no, t.adet) for t in u2.tuketimler.filter(
                stok_lotu__parca=self.led)),
            [('L-LED-ESKI', 2), ('L-LED-YENI', 2)],
        )
        self.assertEqual(self.kalan(eski), 0)
        self.assertEqual(self.kalan(yeni), 48)

    def test_geri_cagrilan_lot_kullanilmaz(self):
        services.uretim_yap(self.urun, 13)
        self.assertEqual(self.kalan(self.diyot_geri), 50)
        self.assertFalse(UretimTuketim.objects.filter(stok_lotu=self.diyot_geri).exists())

    def test_yetersiz_stokta_hicbir_sey_kaydedilmez(self):
        with self.assertRaises(YetersizStok) as ctx:
            services.uretim_yap(self.urun, 14)  # 13 kullanılabilir diyot var
        self.assertEqual([(e.parca.kod, e.eksik) for e in ctx.exception.eksikler], [('DIY', 1)])
        self.assertEqual(Uretim.objects.count(), 0)
        self.assertEqual(UretimTuketim.objects.count(), 0)
        self.assertEqual(self.kalan(self.diyot_eski), 3)
        self.assertEqual(self.kalan(self.led_lot), 100)

    def test_hata_olursa_transaction_geri_alinir(self):
        gercek = UretimTuketim.objects.create
        cagri = {'n': 0}

        def patlayan(*args, **kwargs):
            cagri['n'] += 1
            if cagri['n'] == 4:  # 2. ürünün ortasında
                raise RuntimeError('beklenmeyen hata')
            return gercek(*args, **kwargs)

        with mock.patch.object(UretimTuketim.objects, 'create', side_effect=patlayan):
            with self.assertRaises(RuntimeError):
                services.uretim_yap(self.urun, 3)
        self.assertEqual(Uretim.objects.count(), 0)
        self.assertEqual(UretimTuketim.objects.count(), 0)
        self.assertEqual(self.kalan(self.diyot_eski), 3)

    def test_eski_uretim_urun_agaci_degisse_de_dogru_kalir(self):
        (u,) = services.uretim_yap(self.urun, 1)
        UrunAgaci.objects.filter(urun=self.urun, parca=self.led).update(adet=10)
        self.assertEqual(u.tuketimler.get(stok_lotu__parca=self.led).adet, 4)


class LotIzleTest(TemelVeri):
    def test_uretimler_ve_musteriler(self):
        uretimler = services.uretim_yap(self.urun, 5)  # 3 ESKI, 2 YENI
        services.satis_yap(self.musteri, ['SN-IP-0001', 'SN-IP-0002'])
        sonuc = services.lot_izle('L-D-ESKI')
        self.assertEqual(sonuc['lot'], self.diyot_eski)
        self.assertEqual(sonuc['kullanilan_adet'], 3)
        self.assertEqual(
            [u['seri_no'] for u in sonuc['uretimler']], ['SN-IP-0001', 'SN-IP-0002', 'SN-IP-0003']
        )
        self.assertEqual(sonuc['uretimler'][0]['adet'], 1)
        self.assertEqual(sonuc['uretimler'][0]['satis']['musteri'], 'Atlas')
        self.assertEqual(
            sonuc['musteriler'],
            [{'id': self.musteri.id, 'ad': 'Atlas', 'seri_nolar': ['SN-IP-0001', 'SN-IP-0002']}],
        )
        self.assertEqual([u['seri_no'] for u in sonuc['stoktaki_uretimler']], ['SN-IP-0003'])
        self.assertEqual(uretimler[3].seri_no, 'SN-IP-0004')
        self.assertNotIn('SN-IP-0004', [u['seri_no'] for u in sonuc['uretimler']])

    def test_kullanilmamis_lot(self):
        sonuc = services.lot_izle('L-D-GERI')
        self.assertEqual(sonuc['uretimler'], [])
        self.assertEqual(sonuc['musteriler'], [])

    def test_bilinmeyen_lot(self):
        self.assertIsNone(services.lot_izle('YOK'))


class SeriIzleTest(TemelVeri):
    def test_parcalar_lotlar_ve_satis(self):
        services.uretim_yap(self.urun, 1)
        satis = services.satis_yap(self.musteri, ['SN-IP-0001']).satis
        sonuc = services.seri_izle('SN-IP-0001')
        self.assertEqual(sonuc['urun'], self.urun)
        self.assertEqual(
            [(p['parca'].kod, p['lot'].lot_no, p['adet']) for p in sonuc['parcalar']],
            [('DIY', 'L-D-ESKI', 1), ('LED', 'L-LED', 4)],
        )
        self.assertEqual(sonuc['parcalar'][0]['tedarikci'], self.ted)
        self.assertEqual(sonuc['satis'], satis)
        self.assertEqual(sonuc['musteri'], self.musteri)
        self.assertEqual(sonuc['geri_cagrilan_lotlar'], [])

    def test_satilmamis_ve_geri_cagrilan(self):
        services.uretim_yap(self.urun, 1)
        StokLotu.objects.filter(pk=self.diyot_eski.pk).update(geri_cagrildi=True)
        sonuc = services.seri_izle('SN-IP-0001')
        self.assertIsNone(sonuc['satis'])
        self.assertIsNone(sonuc['musteri'])
        self.assertEqual(sonuc['geri_cagrilan_lotlar'], ['L-D-ESKI'])

    def test_bilinmeyen_seri(self):
        self.assertIsNone(services.seri_izle('SN-YOK-0001'))


class SatisYapTest(TemelVeri):
    def setUp(self):
        super().setUp()
        services.uretim_yap(self.urun, 5)  # 0001-0003 ESKI diyot, 0004-0005 YENI

    def test_satis_kaydedilir(self):
        sonuc = services.satis_yap(self.musteri, ['SN-IP-0001', ' SN-IP-0004 '],
                                   tarih=date(2026, 5, 1))
        self.assertEqual(sonuc.satis.musteri, self.musteri)
        self.assertEqual(sonuc.satis.tarih, date(2026, 5, 1))
        self.assertEqual(
            sorted(sonuc.satis.kalemler.values_list('uretim__seri_no', flat=True)),
            ['SN-IP-0001', 'SN-IP-0004'],
        )
        self.assertEqual(sonuc.uyarilar, [])

    def test_geri_cagrilan_lot_uyarisi_satisi_engellemez(self):
        StokLotu.objects.filter(pk=self.diyot_eski.pk).update(geri_cagrildi=True)
        sonuc = services.satis_yap(self.musteri, ['SN-IP-0001', 'SN-IP-0004'])
        self.assertEqual(sonuc.uyarilar, [{'seri_no': 'SN-IP-0001', 'lotlar': ['L-D-ESKI']}])
        self.assertEqual(sonuc.satis.kalemler.count(), 2)
        # Kaydetmeden kontrol de aynı uyarıyı verir.
        self.assertEqual(services.satis_uyarilari(['SN-IP-0002']),
                         [{'seri_no': 'SN-IP-0002', 'lotlar': ['L-D-ESKI']}])

    def test_daha_once_satilmis_seri_no(self):
        services.satis_yap(self.musteri, ['SN-IP-0001'])
        with self.assertRaisesMessage(SatisHatasi, 'SN-IP-0001'):
            services.satis_yap(self.musteri, ['SN-IP-0002', 'SN-IP-0001'])
        self.assertEqual(Satis.objects.count(), 1)
        self.assertFalse(SatisKalemi.objects.filter(uretim__seri_no='SN-IP-0002').exists())

    def test_bilinmeyen_tekrarlanan_ve_bos_liste(self):
        for liste in (['SN-IP-9999'], ['SN-IP-0001', 'SN-IP-0001'], [], ['  ']):
            with self.subTest(liste=liste), self.assertRaises(SatisHatasi):
                services.satis_yap(self.musteri, liste)
        self.assertEqual(Satis.objects.count(), 0)


class OrnekVeriTest(TestCase):
    def test_komut_uretim_yap_ile_yukler(self):
        call_command('ornek_veri', stdout=StringIO())
        self.assertEqual(Uretim.objects.count(), 9)
        # Geri çağrılan konnektör lotu hiç kullanılmamalı.
        self.assertEqual(StokLotu.objects.get(lot_no='L-KON-2511').kalan_adet, 50)
        # Eski LED lotu SN-IP-0005'te biter, SN-IP-0006 yeni lottan alır.
        self.assertEqual(StokLotu.objects.get(lot_no='L-LED-2412').kalan_adet, 0)
        self.assertEqual(
            list(UretimTuketim.objects.filter(
                uretim__seri_no='SN-IP-0006', stok_lotu__parca__kod='LED-KR'
            ).values_list('stok_lotu__lot_no', flat=True)),
            ['L-LED-2602'],
        )
        self.assertEqual(SatisKalemi.objects.count(), 3)
        call_command('ornek_veri', '--sifirla', stdout=StringIO())
        self.assertEqual(Uretim.objects.count(), 9)
