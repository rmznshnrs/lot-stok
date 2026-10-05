from datetime import date, timedelta
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse, reverse_lazy
from django.utils import timezone

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
            [{'id': self.musteri.id, 'ad': 'Atlas', 'iletisim': '',
              'seri_nolar': ['SN-IP-0001', 'SN-IP-0002'],
              'satislar': [{'seri_no': 'SN-IP-0001', 'tarih': timezone.localdate()},
                           {'seri_no': 'SN-IP-0002', 'tarih': timezone.localdate()}]}],
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


class ParcaStoklariTest(TemelVeri):
    def test_toplam_kullanilabilir_lot_sayisi_ve_min(self):
        self.diyot.min_stok = 20
        self.diyot.save()
        Parca.objects.create(kod='YENI', ad='Yeni parça')
        p = {x.kod: x for x in services.parca_stoklari()}
        self.assertEqual(p['DIY'].toplam_stok, 63)        # 10 + 3 + 50 (geri çağrılan)
        self.assertEqual(p['DIY'].kullanilabilir_stok, 13)
        self.assertEqual(p['DIY'].lot_sayisi, 3)
        self.assertTrue(p['DIY'].min_altinda)
        self.assertFalse(p['LED'].min_altinda)
        self.assertEqual((p['YENI'].toplam_stok, p['YENI'].lot_sayisi), (0, 0))

    def test_bitmis_lot_sayilmaz(self):
        services.uretim_yap(self.urun, 3)  # L-D-ESKI biter
        self.assertEqual(services.parca_stoklari().get(kod='DIY').lot_sayisi, 2)


class MenuTest(TestCase):
    def test_tum_menu_sayfalari_acilir(self):
        for ad in ('ana_sayfa', 'izlenebilirlik', 'uretim', 'stok', 'satis', 'parcalar'):
            with self.subTest(ad=ad):
                yanit = self.client.get(reverse(f'takip:{ad}'))
                self.assertEqual(yanit.status_code, 200)
                self.assertContains(yanit, 'class="aktif"')
                self.assertContains(yanit, 'takip/style.css')
                self.assertNotContains(yanit, 'hazırlanıyor')


class ParcalarEkraniTest(TemelVeri):
    url = reverse_lazy('takip:parcalar')

    def test_liste_gercek_degerleri_gosterir(self):
        self.diyot.min_stok = 20
        self.diyot.save()
        yanit = self.client.get(self.url)
        self.assertContains(yanit, 'DIY')
        self.assertContains(yanit, 'İndikatör Paneli')
        self.assertContains(yanit, '>13</a> · min altında')
        self.assertContains(yanit, 'uyari-satir')
        self.assertNotContains(yanit, '[')  # yer tutucu kalmamalı

    def test_kullanilmayan_parca(self):
        Parca.objects.create(kod='BOSTA', ad='Boşta')
        self.assertContains(self.client.get(self.url), 'Henüz hiçbir ürün ağacında yok')

    def test_arama(self):
        yanit = self.client.get(self.url, {'q': 'diyot'})
        self.assertEqual([p.kod for p in yanit.context['parcalar']], ['DIY'])

    def test_parca_kaydet(self):
        yanit = self.client.post(self.url, {'kod': ' STM32F103 ', 'ad': 'Mikrodenetleyici',
                                            'birim': 'adet', 'min_stok': 5})
        parca = Parca.objects.get(kod='STM32F103')
        self.assertRedirects(yanit, f'{self.url}?yeni={parca.pk}')
        self.assertContains(self.client.get(yanit.url), 'rozet--mavi')

    def test_ayni_kod_tekrar_kaydedilemez(self):
        yanit = self.client.post(self.url, {'kod': 'diy', 'ad': 'X', 'birim': 'adet',
                                            'min_stok': 0})
        self.assertEqual(yanit.status_code, 200)
        self.assertContains(yanit, 'Bu kodla bir parça zaten var.')
        self.assertEqual(Parca.objects.filter(kod__iexact='diy').count(), 1)


class UrunlerEkraniTest(TemelVeri):
    def setUp(self):
        super().setUp()
        self.url = reverse('takip:urun', args=[self.urun.pk])

    def post_verisi(self, **ek):
        satirlar = list(self.urun.agac.order_by('parca__kod'))
        veri = {
            'ad': self.urun.ad, 'kod': self.urun.kod, 'seri_oneki': self.urun.seri_oneki,
            'agac-TOTAL_FORMS': len(satirlar), 'agac-INITIAL_FORMS': len(satirlar),
            'agac-MIN_NUM_FORMS': 0, 'agac-MAX_NUM_FORMS': 1000,
            'yeni_parca': '', 'yeni_adet': '',
        }
        for i, s in enumerate(satirlar):
            veri.update({f'agac-{i}-id': s.pk, f'agac-{i}-parca': s.parca_id,
                         f'agac-{i}-adet': s.adet, f'agac-{i}-urun': self.urun.pk})
        veri.update(ek)
        return veri

    def agac(self):
        return dict(self.urun.agac.values_list('parca__kod', 'adet'))

    def test_liste_ilk_urune_yonlendirir(self):
        self.assertRedirects(self.client.get(reverse('takip:urunler')), self.url)

    def test_urun_ve_agac_gosterilir(self):
        yanit = self.client.get(self.url)
        self.assertContains(yanit, 'liste-oge--secili')
        self.assertContains(yanit, 'value="SN-IP-"')
        self.assertContains(yanit, 'Diyot')
        self.assertContains(yanit, '<option value="LED">')
        self.assertNotContains(yanit, '[')

    def test_adet_degistir(self):
        # Sıra: DIY (0), LED (1)
        yanit = self.client.post(self.url, self.post_verisi(**{'agac-1-adet': 6}))
        self.assertRedirects(yanit, self.url)
        self.assertEqual(self.agac(), {'DIY': 1, 'LED': 6})

    def test_satir_kaldir(self):
        self.client.post(self.url, self.post_verisi(**{'agac-0-DELETE': 'on'}))
        self.assertEqual(self.agac(), {'LED': 4})

    def test_parca_ekle(self):
        Parca.objects.create(kod='PCB', ad='Kart')
        yanit = self.client.post(self.url, self.post_verisi(
            yeni_parca='pcb', yeni_adet=1, islem='ekle'))
        self.assertRedirects(yanit, self.url)
        self.assertEqual(self.agac(), {'DIY': 1, 'LED': 4, 'PCB': 1})

    def test_agacta_olan_parca_tekrar_eklenemez(self):
        yanit = self.client.post(self.url, self.post_verisi(yeni_parca='LED', yeni_adet=2))
        self.assertContains(yanit, 'zaten ağaçta')
        self.assertEqual(self.agac(), {'DIY': 1, 'LED': 4})

    def test_kaldirilan_parca_ayni_kayitta_yeniden_eklenebilir(self):
        self.client.post(self.url, self.post_verisi(
            **{'agac-1-DELETE': 'on', 'yeni_parca': 'LED', 'yeni_adet': 2}))
        self.assertEqual(self.agac(), {'DIY': 1, 'LED': 2})

    def test_hatali_girislerde_hicbir_sey_kaydedilmez(self):
        for ek in ({'agac-1-adet': 0}, {'yeni_parca': 'YOK', 'yeni_adet': 1},
                   {'yeni_parca': 'LED'}, {'ad': ''}):
            with self.subTest(ek=ek):
                veri = self.post_verisi(**{'agac-0-DELETE': 'on', **ek})
                self.assertEqual(self.client.post(self.url, veri).status_code, 200)
                self.assertEqual(self.agac(), {'DIY': 1, 'LED': 4})

    def test_yeni_urun(self):
        url = reverse('takip:urun_yeni')
        self.assertEqual(self.client.get(url).status_code, 200)
        yanit = self.client.post(url, {
            'ad': 'Röle Kartı', 'kod': 'RK', 'seri_oneki': 'SN-RK-',
            'agac-TOTAL_FORMS': 0, 'agac-INITIAL_FORMS': 0,
            'agac-MIN_NUM_FORMS': 0, 'agac-MAX_NUM_FORMS': 1000,
            'yeni_parca': 'DIY', 'yeni_adet': 2,
        })
        urun = Urun.objects.get(kod='RK')
        self.assertRedirects(yanit, reverse('takip:urun', args=[urun.pk]))
        self.assertEqual(list(urun.agac.values_list('parca__kod', 'adet')), [('DIY', 2)])

    def test_uretim_varsa_seri_oneki_degismez(self):
        services.uretim_yap(self.urun, 1)
        self.client.post(self.url, self.post_verisi(seri_oneki='YENI-', ad='Panel v2'))
        self.urun.refresh_from_db()
        self.assertEqual((self.urun.ad, self.urun.seri_oneki), ('Panel v2', 'SN-IP-'))


class MalGirisiTest(TemelVeri):
    def test_lot_olusur_kalan_giris_adedine_esit(self):
        lot, yeni = services.mal_girisi(self.diyot, 'Tedarikçi A', ' L-YENI ', 40,
                                        date(2026, 9, 1), 'SP-9')
        self.assertFalse(yeni)
        self.assertEqual((lot.lot_no, lot.giris_adet, lot.kalan_adet, lot.tedarikci),
                         ('L-YENI', 40, 40, self.ted))
        self.assertLess(abs((timezone.now() - lot.giris_zamani).total_seconds()), 60)

    def test_yeni_tedarikci_olusur(self):
        lot, yeni = services.mal_girisi(self.diyot, '  Yeni   Firma ', 'L-X', 1, date(2026, 9, 1))
        self.assertTrue(yeni)
        self.assertEqual(lot.tedarikci.ad, 'Yeni Firma')
        _, yeni = services.mal_girisi(self.diyot, 'yeni firma', 'L-Y', 1, date(2026, 9, 1))
        self.assertFalse(yeni)

    def test_hatalar(self):
        for args in (('L-LED', 1), ('l-led', 1), ('L-Z', 0), ('', 1)):
            with self.subTest(args=args), self.assertRaises(ValueError):
                services.mal_girisi(self.led, 'Tedarikçi A', args[0], args[1], date(2026, 9, 1))
        self.assertEqual(StokLotu.objects.count(), 4)


class StokDurumuTest(TemelVeri):
    def gruplar(self, **kw):
        return {g['parca'].kod: g for g in services.stok_durumu(**kw)}

    def test_fifo_sirasi_ve_siradaki_lot(self):
        g = self.gruplar()['DIY']
        self.assertEqual([l.lot_no for l in g['lotlar']], ['L-D-GERI', 'L-D-ESKI', 'L-D-YENI'])
        self.assertEqual(g['siradaki'], self.diyot_eski)  # geri çağrılan sıradaki olmaz
        self.assertEqual([l.sirada for l in g['lotlar']], [False, True, False])
        self.assertEqual((g['parca'].toplam_stok, g['parca'].kullanilabilir_stok), (63, 13))

    def test_biten_lotlar_gizlenir(self):
        services.uretim_yap(self.urun, 3)  # L-D-ESKI biter
        self.assertEqual([l.lot_no for l in self.gruplar()['DIY']['lotlar']],
                         ['L-D-GERI', 'L-D-YENI'])
        self.assertEqual(self.gruplar()['DIY']['siradaki'], self.diyot_yeni)
        self.assertEqual(len(self.gruplar(bitenler=True)['DIY']['lotlar']), 3)

    def test_arama(self):
        self.assertEqual(list(self.gruplar(q='diyot')), ['DIY'])
        self.assertEqual(len(self.gruplar(q='diyot')['DIY']['lotlar']), 3)
        # Yalnız lot eşleşirse yalnız o lot gösterilir.
        g = self.gruplar(q='eski')
        self.assertEqual(list(g), ['DIY'])
        self.assertEqual([l.lot_no for l in g['DIY']['lotlar']], ['L-D-ESKI'])
        self.assertEqual(self.gruplar(q='yok-boyle-bir-sey'), {})

    def test_parca_filtresi(self):
        self.assertEqual(list(self.gruplar(parca_kod='led')), ['LED'])


class StokEkraniTest(TemelVeri):
    url = reverse_lazy('takip:stok')

    def test_lot_bazinda(self):
        yanit = self.client.get(self.url)
        self.assertContains(yanit, 'L-D-ESKI</a> <span class="lot-etiket">· sıradaki</span>')
        self.assertContains(yanit, '· geri çağrıldı')
        self.assertContains(yanit, 'Toplam: 63')
        self.assertContains(yanit, 'kullanılabilir: 13')
        self.assertContains(yanit, 'sekme sekme--aktif" aria-current="page">Lot bazında')
        self.assertNotContains(yanit, '[')

    def test_parca_bazinda(self):
        yanit = self.client.get(self.url, {'gorunum': 'parca'})
        self.assertContains(yanit, 'sekme sekme--aktif" aria-current="page">Parça bazında')
        self.assertContains(yanit, 'L-D-ESKI <span class="lot-etiket">· 3 kaldı</span>')

    def test_parca_linki_formu_doldurur(self):
        yanit = self.client.get(self.url, {'parca': 'DIY'})
        self.assertEqual(yanit.context['form'].initial['parca'], self.diyot)
        self.assertEqual(len(yanit.context['gruplar']), 1)

    def test_mal_girisi_kaydet(self):
        yanit = self.client.post(self.url, {
            'parca': self.led.pk, 'tedarikci': 'Yeni Firma', 'lot_no': 'L-LED-2',
            'adet': 25, 'siparis_tarihi': '2026-09-15', 'siparis_no': 'SP-77',
        }, follow=True)
        lot = StokLotu.objects.get(lot_no='L-LED-2')
        self.assertEqual((lot.kalan_adet, lot.siparis_tarihi, lot.tedarikci.ad),
                         (25, date(2026, 9, 15), 'Yeni Firma'))
        self.assertContains(yanit, 'L-LED-2 stoğa eklendi')
        self.assertContains(yanit, 'Yeni tedarikçi tanımlandı')

    def test_mal_girisi_hatalari(self):
        yanit = self.client.post(self.url, {
            'parca': self.led.pk, 'tedarikci': 'X', 'lot_no': 'l-led', 'adet': 0,
            'siparis_tarihi': '2026-09-15',
        })
        self.assertEqual(yanit.status_code, 200)
        self.assertContains(yanit, 'Bu lot numarası zaten kayıtlı.')
        self.assertEqual(StokLotu.objects.count(), 4)


class UretimOnizlemeEkTest(TemelVeri):
    def test_seri_nolar_ve_atlanan_lotlar(self):
        o = services.uretim_onizle(self.urun, 3)
        self.assertEqual(o.seri_nolar, ['SN-IP-0001', 'SN-IP-0002', 'SN-IP-0003'])
        diyot = next(p for p in o.parcalar if p.parca == self.diyot)
        self.assertEqual(diyot.atlanan_lotlar, [self.diyot_geri])
        services.uretim_yap(self.urun, 1)
        self.assertEqual(services.sonraki_seri_nolar(self.urun, 1), ['SN-IP-0002'])


class UretimEkraniTest(TemelVeri):
    url = reverse_lazy('takip:uretim')

    def test_onizleme_yeterli(self):
        yanit = self.client.get(self.url, {'urun': self.urun.pk, 'adet': 5})
        self.assertContains(yanit, 'L-D-ESKI × 3 · L-D-YENI × 2')
        self.assertContains(yanit, 'SN-IP-0001 … SN-IP-0005')
        self.assertContains(yanit, 'L-D-GERI</span>) otomatik olarak atlanır')
        self.assertNotContains(yanit, 'Eksik parça var')
        self.assertContains(yanit, '<button type="submit" class="btn">Üretimi kaydet')
        html = yanit.content.decode().split('<script>')[0]
        self.assertNotIn('[', html)  # yer tutucu kalmamalı

    def test_onizleme_eksik_buton_pasif(self):
        yanit = self.client.get(self.url, {'urun': self.urun.pk, 'adet': 14})
        self.assertContains(yanit, '1 adet eksik')
        self.assertContains(yanit, 'Eksik parça var')
        self.assertContains(yanit, 'class="btn" disabled')

    def test_varsayilanlar_ve_gecersiz_adet(self):
        yanit = self.client.get(self.url)
        self.assertEqual((yanit.context['urun'], yanit.context['adet']), (self.urun, 1))
        self.assertEqual(self.client.get(self.url, {'adet': 'abc'}).context['adet'], 1)
        self.assertEqual(self.client.get(self.url, {'adet': '5000'}).context['adet'], 999)

    def test_bos_agac(self):
        bos = Urun.objects.create(kod='BOS', ad='Boş', seri_oneki='SN-B-')
        yanit = self.client.get(self.url, {'urun': bos.pk})
        self.assertContains(yanit, 'ürün ağacı boş')

    def test_kaydet_ve_seri_numaralarini_goster(self):
        yanit = self.client.post(self.url, {'urun': self.urun.pk, 'adet': 2})
        self.assertRedirects(yanit, f'{self.url}?urun={self.urun.pk}&adet=2',
                             fetch_redirect_response=False)
        self.assertEqual(Uretim.objects.count(), 2)
        sayfa = self.client.get(yanit.url)
        self.assertContains(sayfa, 'Üretim kaydedildi: 2 adet İndikatör Paneli')
        self.assertContains(sayfa, '>SN-IP-0001</a>')
        self.assertContains(sayfa, '>SN-IP-0002</a>')
        self.assertContains(sayfa, 'SN-IP-0003 … SN-IP-0004')  # sıradaki önizleme
        # Sonuç kutusu yalnız bir kez gösterilir.
        self.assertNotContains(self.client.get(yanit.url), 'Üretim kaydedildi')

    def test_yetersiz_stokta_kaydetmez(self):
        yanit = self.client.post(self.url, {'urun': self.urun.pk, 'adet': 14}, follow=True)
        self.assertContains(yanit, 'Stok yetersiz, üretim kaydedilmedi: DIY (1 eksik)')
        self.assertEqual(Uretim.objects.count(), 0)

    def test_gecersiz_post(self):
        self.client.post(self.url, {'urun': self.urun.pk, 'adet': 0})
        self.assertEqual(Uretim.objects.count(), 0)


class NumaraAraTest(TemelVeri):
    def setUp(self):
        super().setUp()
        services.uretim_yap(self.urun, 2)

    def test_birebir_eslesme(self):
        self.assertEqual(services.numara_ara(' l-d-eski '),
                         {'tur': 'lot', 'deger': 'L-D-ESKI', 'adaylar': []})
        self.assertEqual(services.numara_ara('sn-ip-0002')['tur'], 'seri')
        self.assertEqual(services.numara_ara('sn-ip-0002')['deger'], 'SN-IP-0002')
        self.assertEqual(services.numara_ara('led'), {'tur': 'parca', 'deger': 'LED',
                                                      'adaylar': []})

    def test_kismi_eslesme(self):
        self.assertEqual(services.numara_ara('ESKI')['deger'], 'L-D-ESKI')  # tek aday
        sonuc = services.numara_ara('SN-IP')
        self.assertIsNone(sonuc['tur'])
        self.assertEqual(sonuc['adaylar'], [('seri', 'SN-IP-0001'), ('seri', 'SN-IP-0002')])
        self.assertEqual(services.numara_ara('L-D')['adaylar'],
                         [('lot', 'L-D-ESKI'), ('lot', 'L-D-GERI'), ('lot', 'L-D-YENI')])

    def test_bulunamadi_ve_bos(self):
        self.assertEqual(services.numara_ara('XYZ'), {'tur': None, 'deger': None, 'adaylar': []})
        self.assertIsNone(services.numara_ara('  ')['tur'])


class SonHareketlerTest(TemelVeri):
    def test_giris_uretim_satis_yeniden_eskiye(self):
        services.uretim_yap(self.urun, 3)
        services.satis_yap(self.musteri, ['SN-IP-0001', 'SN-IP-0002'])
        h = services.son_hareketler(limit=20)
        self.assertEqual([x['tur'] for x in h[:2]], ['satis', 'uretim'])
        self.assertEqual(h[0]['numara'], 'SN-IP-0001 … SN-IP-0002')
        self.assertIn('Atlas', h[0]['aciklama'])
        # 3 üretim tek satırda
        self.assertEqual(h[1]['aciklama'], 'İndikatör Paneli · 3 adet')
        self.assertEqual(h[1]['numara'], 'SN-IP-0001 … SN-IP-0003')
        girisler = [x for x in h if x['tur'] == 'giris']
        self.assertEqual(len(girisler), 4)

    def test_giris_siparis_tarihine_degil_giris_zamanina_gore_siralanir(self):
        # En eski siparişli lot en son girilmiş olsun.
        StokLotu.objects.filter(pk=self.diyot_geri.pk).update(
            giris_zamani=timezone.now() + timedelta(hours=1))
        StokLotu.objects.filter(pk=self.led_lot.pk).update(
            giris_zamani=timezone.now() - timedelta(days=30))
        girisler = [x['numara'] for x in services.son_hareketler(limit=20)
                    if x['tur'] == 'giris']
        self.assertEqual(girisler[0], 'L-D-GERI')
        self.assertEqual(girisler[-1], 'L-LED')

    def test_limit(self):
        self.assertEqual(len(services.son_hareketler(limit=2)), 2)


class LotGeriCagirTest(TemelVeri):
    def test_geri_cagir_ve_kaldir(self):
        lot = services.lot_geri_cagir('l-d-eski')
        self.assertTrue(lot.geri_cagrildi)
        self.diyot_eski.refresh_from_db()
        self.assertTrue(self.diyot_eski.geri_cagrildi)
        services.lot_geri_cagir('L-D-ESKI', geri_cagir=False)
        self.diyot_eski.refresh_from_db()
        self.assertFalse(self.diyot_eski.geri_cagrildi)

    def test_bilinmeyen_lot(self):
        with self.assertRaises(StokLotu.DoesNotExist):
            services.lot_geri_cagir('YOK')


class SatisYardimcilariTest(TemelVeri):
    def test_musteri_getir_veya_olustur(self):
        self.assertEqual(services.musteri_getir_veya_olustur(' atlas '), (self.musteri, False))
        m, yeni = services.musteri_getir_veya_olustur('Yeni  Müşteri')
        self.assertTrue(yeni)
        self.assertEqual(m.ad, 'Yeni Müşteri')
        with self.assertRaises(ValueError):
            services.musteri_getir_veya_olustur('  ')

    def test_satilabilir_uretim(self):
        services.uretim_yap(self.urun, 2)
        self.assertEqual(services.satilabilir_uretim('sn-ip-0001').seri_no, 'SN-IP-0001')
        services.satis_yap(self.musteri, ['SN-IP-0001'])
        with self.assertRaisesMessage(SatisHatasi, 'daha önce satılmış: Atlas'):
            services.satilabilir_uretim('SN-IP-0001')
        with self.assertRaisesMessage(SatisHatasi, 'bulunamadı'):
            services.satilabilir_uretim('SN-YOK')

    def test_satis_gecmisi(self):
        services.uretim_yap(self.urun, 3)
        services.satis_yap(self.musteri, ['SN-IP-0002', 'SN-IP-0001'], tarih=date(2026, 5, 1))
        diger = Musteri.objects.create(ad='Beta')
        services.satis_yap(diger, ['SN-IP-0003'], tarih=date(2026, 6, 1))
        g = services.satis_gecmisi()
        self.assertEqual([(x['musteri'].ad, x['seri_nolar']) for x in g],
                         [('Beta', ['SN-IP-0003']), ('Atlas', ['SN-IP-0001', 'SN-IP-0002'])])
        self.assertEqual([x['musteri'].ad for x in services.satis_gecmisi('0001')], ['Atlas'])
        self.assertEqual([x['musteri'].ad for x in services.satis_gecmisi('bet')], ['Beta'])


class AnaSayfaEkraniTest(TemelVeri):
    url = reverse_lazy('takip:ana_sayfa')

    def test_sayfa(self):
        services.uretim_yap(self.urun, 1)
        yanit = self.client.get(self.url)
        self.assertContains(yanit, 'Bir lot veya seri numarasının izini sür')
        self.assertContains(yanit, 'Mal girişi yap')
        self.assertContains(yanit, 'Son hareketler')
        self.assertContains(yanit, 'SN-IP-0001')
        self.assertNotContains(yanit, '[')

    def test_otomatik_algilama_yonlendirir(self):
        services.uretim_yap(self.urun, 1)
        iz = reverse('takip:izlenebilirlik')
        self.assertRedirects(self.client.get(self.url, {'q': 'l-d-eski'}), f'{iz}?lot=L-D-ESKI')
        self.assertRedirects(self.client.get(self.url, {'q': 'sn-ip-0001'}),
                             f'{iz}?sekme=seri&seri=SN-IP-0001')
        self.assertRedirects(self.client.get(self.url, {'q': 'DIY'}),
                             f"{reverse('takip:stok')}?parca=DIY")

    def test_adaylar_ve_bulunamadi(self):
        yanit = self.client.get(self.url, {'q': 'L-D'})
        self.assertContains(yanit, 'birebir eşleşmedi')
        self.assertContains(yanit, 'L-D-GERI')
        self.assertContains(self.client.get(self.url, {'q': 'XYZ'}), 'bulunamadı')


class IzlenebilirlikEkraniTest(TemelVeri):
    url = reverse_lazy('takip:izlenebilirlik')

    def setUp(self):
        super().setUp()
        services.uretim_yap(self.urun, 4)  # 0001-0003 L-D-ESKI, 0004 L-D-YENI
        self.musteri.iletisim = 'atlas@ornek.example'
        self.musteri.save()
        services.satis_yap(self.musteri, ['SN-IP-0001'], tarih=date(2026, 5, 12))

    def test_lot_akisi(self):
        yanit = self.client.get(self.url, {'lot': 'l-d-eski'})
        self.assertContains(yanit, '3 üründe kullanıldı')
        self.assertContains(yanit, '1 müşteriye ulaştı')
        self.assertContains(yanit, '2 ürün depoda')
        self.assertContains(yanit, 'Bu lotu geri çağır')
        self.assertContains(yanit, 'rozet rozet--uyari">Satıldı')
        self.assertContains(yanit, 'rozet rozet--basari">Depoda')
        self.assertContains(yanit, 'atlas@ornek.example')
        self.assertContains(yanit, '12.05.2026')
        self.assertContains(yanit, 'bu lottan parça içeriyor')
        self.assertNotContains(yanit, 'SN-IP-0004')
        self.assertNotIn('[', yanit.content.decode().split('<script>')[0])

    def test_seri_parcalar(self):
        StokLotu.objects.filter(pk=self.diyot_eski.pk).update(geri_cagrildi=True)
        yanit = self.client.get(self.url, {'sekme': 'seri', 'seri': 'sn-ip-0001'})
        self.assertContains(yanit, 'SN-IP-0001')
        self.assertContains(yanit, 'Atlas · 12.05.2026')
        self.assertContains(yanit, 'L-D-ESKI</a> <span class="lot-etiket lot-etiket--uyari">')
        self.assertContains(yanit, 'Bu üründe geri çağrılan 1 lot var.')
        self.assertContains(yanit, 'L-LED')

    def test_satilmamis_seri(self):
        yanit = self.client.get(self.url, {'seri': 'SN-IP-0004'})
        self.assertEqual(yanit.context['sekme'], 'seri')
        self.assertContains(yanit, 'Henüz satılmadı')
        self.assertNotContains(yanit, 'geri çağrılan')

    def test_bulunamadi_ve_bos(self):
        self.assertContains(self.client.get(self.url, {'lot': 'YOK'}), 'bulunamadı')
        self.assertContains(self.client.get(self.url, {'sekme': 'seri', 'seri': 'YOK'}),
                            'bulunamadı')
        self.assertContains(self.client.get(self.url), 'Bir lot numarası girin')

    def test_geri_cagir_butonu(self):
        url = reverse('takip:lot_geri_cagir')
        yanit = self.client.post(url, {'lot_no': 'L-D-ESKI'}, follow=True)
        self.assertRedirects(yanit, f'{self.url}?lot=L-D-ESKI')
        self.assertTrue(StokLotu.objects.get(pk=self.diyot_eski.pk).geri_cagrildi)
        self.assertContains(yanit, 'L-D-ESKI geri çağrıldı')
        self.assertContains(yanit, 'Geri çağırmayı kaldır')
        self.assertContains(yanit, 'sevkiyattan önce ayrılmalı')
        self.client.post(url, {'lot_no': 'L-D-ESKI', 'islem': 'kaldir'})
        self.assertFalse(StokLotu.objects.get(pk=self.diyot_eski.pk).geri_cagrildi)

    def test_geri_cagir_get_ile_calismaz(self):
        self.assertEqual(self.client.get(reverse('takip:lot_geri_cagir')).status_code, 405)


class SatisEkraniTest(TemelVeri):
    url = reverse_lazy('takip:satis')

    def setUp(self):
        super().setUp()
        services.uretim_yap(self.urun, 4)  # 0001-0003 L-D-ESKI, 0004 L-D-YENI

    def post(self, **veri):
        return self.client.post(self.url, {'musteri': 'Atlas', 'tarih': '2026-06-01', **veri})

    def test_ekle_kaydetmez_listeyi_gunceller(self):
        yanit = self.post(yeni_seri='sn-ip-0001, SN-IP-0002 SN-YOK', islem='ekle')
        self.assertEqual(Satis.objects.count(), 0)
        self.assertEqual([k['uretim'].seri_no for k in yanit.context['sepet']],
                         ['SN-IP-0001', 'SN-IP-0002'])
        self.assertContains(yanit, 'SN-YOK seri numarası bulunamadı.')
        self.assertContains(yanit, '2 ürün')

    def test_kaldir_ve_tekrar_ekleme(self):
        yanit = self.post(seri=['SN-IP-0001', 'SN-IP-0002'], islem='kaldir:SN-IP-0001')
        self.assertEqual([k['uretim'].seri_no for k in yanit.context['sepet']], ['SN-IP-0002'])
        yanit = self.post(seri=['SN-IP-0002'], yeni_seri='sn-ip-0002', islem='ekle')
        self.assertContains(yanit, 'zaten listede')

    def test_kaydet(self):
        yanit = self.post(seri=['SN-IP-0001'], yeni_seri='SN-IP-0004', islem='kaydet')
        self.assertRedirects(yanit, self.url, fetch_redirect_response=False)
        satis = Satis.objects.get()
        self.assertEqual((satis.musteri, satis.tarih), (self.musteri, date(2026, 6, 1)))
        self.assertEqual(sorted(satis.kalemler.values_list('uretim__seri_no', flat=True)),
                         ['SN-IP-0001', 'SN-IP-0004'])
        sayfa = self.client.get(self.url)
        self.assertContains(sayfa, 'Satış kaydedildi: Atlas, 2 ürün')
        self.assertContains(sayfa, 'SN-IP-0001</a>, <a')  # geçmişte tek satır
        self.assertNotIn('[', sayfa.content.decode())

    def test_yeni_musteri_olusur(self):
        self.client.post(self.url, {'musteri': 'Gama Ltd', 'tarih': '2026-06-01',
                                    'seri': ['SN-IP-0004'], 'islem': 'kaydet'})
        self.assertEqual(Satis.objects.get().musteri.ad, 'Gama Ltd')

    def test_geri_cagrilan_lot_uyarisi_ve_onay(self):
        StokLotu.objects.filter(pk=self.diyot_eski.pk).update(geri_cagrildi=True)
        yanit = self.post(yeni_seri='SN-IP-0001 SN-IP-0004', islem='ekle')
        self.assertContains(yanit, 'Geri çağrılan lot içeriyor (L-D-ESKI)')
        self.assertContains(yanit, 'sepet-oge sepet-oge--uyari')
        self.assertContains(yanit, 'yine de sat')
        # Onaysız kaydedilmez
        yanit = self.post(seri=['SN-IP-0001', 'SN-IP-0004'], islem='kaydet')
        self.assertEqual(yanit.status_code, 200)
        self.assertEqual(Satis.objects.count(), 0)
        # Onayla kaydedilir, uyarı mesajı gösterilir
        yanit = self.client.post(self.url, {'musteri': 'Atlas', 'tarih': '2026-06-01',
                                            'seri': ['SN-IP-0001', 'SN-IP-0004'],
                                            'onay': '1', 'islem': 'kaydet'}, follow=True)
        self.assertEqual(Satis.objects.count(), 1)
        self.assertContains(yanit, 'SN-IP-0001 geri çağrılan lot içeriyor: L-D-ESKI.')

    def test_hatalarda_kaydetmez(self):
        services.satis_yap(self.musteri, ['SN-IP-0003'])
        for veri in ({'seri': [], 'islem': 'kaydet'},
                     {'seri': ['SN-IP-0003'], 'islem': 'kaydet'},
                     {'seri': ['SN-IP-0001'], 'islem': 'kaydet', 'musteri': ''},
                     {'seri': ['SN-IP-0001'], 'islem': 'kaydet', 'tarih': 'abc'}):
            with self.subTest(veri=veri):
                self.assertEqual(self.post(**veri).status_code, 200)
        self.assertEqual(Satis.objects.count(), 1)

    def test_gecmis_arama(self):
        services.satis_yap(self.musteri, ['SN-IP-0003'])
        yanit = self.client.get(self.url, {'q': 'atl'})
        self.assertEqual(len(yanit.context['gecmis']), 1)
        self.assertEqual(len(self.client.get(self.url, {'q': 'zzz'}).context['gecmis']), 0)


class UcanUcaSenaryoTest(TestCase):
    """ADIMLAR.md Adım 8: boş veritabanında, yalnızca ekranlar (form gönderimleri) ile."""

    def ok(self, yanit, *metinler):
        self.assertEqual(yanit.status_code, 200)
        for metin in metinler:
            self.assertContains(yanit, metin)
        return yanit

    def test_senaryo(self):
        c = self.client

        # 1. Üç parça tanımla, İndikatör Paneli'ni bunlarla tanımla.
        for kod, ad in (('1N4001', 'Diyot'), ('PCB-IP', 'Elektronik kart (PCB)'),
                        ('KASA-IP', 'Kasa')):
            yanit = c.post(reverse('takip:parcalar'),
                           {'kod': kod, 'ad': ad, 'birim': 'adet', 'min_stok': 2}, follow=True)
            self.ok(yanit, f'{kod} – {ad} kaydedildi.', 'Henüz hiçbir ürün ağacında yok')
        yanit = c.post(reverse('takip:urun_yeni'), {
            'ad': 'İndikatör Paneli', 'kod': 'IP', 'seri_oneki': 'SN-IP-',
            'agac-TOTAL_FORMS': 0, 'agac-INITIAL_FORMS': 0,
            'agac-MIN_NUM_FORMS': 0, 'agac-MAX_NUM_FORMS': 1000,
            'yeni_parca': '1N4001', 'yeni_adet': 2, 'islem': 'ekle',
        }, follow=True)
        self.ok(yanit, '1N4001 ürün ağacına eklendi.')
        urun = Urun.objects.get(kod='IP')
        urun_url = reverse('takip:urun', args=[urun.pk])
        for kod in ('PCB-IP', 'KASA-IP'):
            satirlar = list(urun.agac.order_by('parca__kod'))
            veri = {'ad': urun.ad, 'kod': urun.kod, 'seri_oneki': urun.seri_oneki,
                    'agac-TOTAL_FORMS': len(satirlar), 'agac-INITIAL_FORMS': len(satirlar),
                    'agac-MIN_NUM_FORMS': 0, 'agac-MAX_NUM_FORMS': 1000,
                    'yeni_parca': kod, 'yeni_adet': 1, 'islem': 'ekle'}
            for i, s in enumerate(satirlar):
                veri.update({f'agac-{i}-id': s.pk, f'agac-{i}-parca': s.parca_id,
                             f'agac-{i}-adet': s.adet})
            self.ok(c.post(urun_url, veri, follow=True), f'{kod} ürün ağacına eklendi.')
        self.assertEqual(dict(urun.agac.values_list('parca__kod', 'adet')),
                         {'1N4001': 2, 'PCB-IP': 1, 'KASA-IP': 1})
        self.ok(c.get(reverse('takip:parcalar')), '>İndikatör Paneli</a>')

        # 2. Diyottan farklı tarihli 2 lot gir (+ diğer parçalar için birer lot).
        # Eski lotta üretimden sonra parça kalsın ki 6. adımda atlanması anlamlı olsun.
        lotlar = (('1N4001', 'L-D-EYLUL', '2026-09-01', 15),
                  ('1N4001', 'L-D-EKIM', '2026-10-01', 30),
                  ('PCB-IP', 'L-PCB-1', '2026-09-10', 20),
                  ('KASA-IP', 'L-KASA-1', '2026-09-12', 20))
        for kod, lot_no, tarih, adet in lotlar:
            yanit = c.post(reverse('takip:stok'), {
                'parca': Parca.objects.get(kod=kod).pk, 'tedarikci': 'Elektro A.Ş.',
                'lot_no': lot_no, 'adet': adet, 'siparis_tarihi': tarih,
            }, follow=True)
            self.ok(yanit, f'{lot_no} stoğa eklendi')
        yanit = self.ok(c.get(reverse('takip:stok')), 'Toplam: 45')
        self.assertContains(yanit, 'L-D-EYLUL</a> <span class="lot-etiket">· sıradaki</span>')

        # 3. 5 panel üret → en eski lottan düşmeli.
        uretim_url = reverse('takip:uretim')
        self.ok(c.get(uretim_url, {'urun': urun.pk, 'adet': 5}),
                'L-D-EYLUL × 10', 'SN-IP-0001 … SN-IP-0005')
        yanit = c.post(uretim_url, {'urun': urun.pk, 'adet': 5}, follow=True)
        self.ok(yanit, 'Üretim kaydedildi: 5 adet İndikatör Paneli', '>SN-IP-0005</a>')
        self.assertEqual(StokLotu.objects.get(lot_no='L-D-EYLUL').kalan_adet, 5)
        self.assertEqual(StokLotu.objects.get(lot_no='L-D-EKIM').kalan_adet, 30)
        self.ok(c.get(reverse('takip:izlenebilirlik'), {'seri': 'SN-IP-0003'}),
                '>L-D-EYLUL</a>')

        # 4. 2 paneli bir müşteriye sat.
        satis_url = reverse('takip:satis')
        yanit = self.ok(c.post(satis_url, {'musteri': 'Atlas Otomasyon', 'tarih': '2026-10-03',
                                           'yeni_seri': 'SN-IP-0001 SN-IP-0002',
                                           'islem': 'ekle'}), '2 ürün')
        yanit = c.post(satis_url, {'musteri': 'Atlas Otomasyon', 'tarih': '2026-10-03',
                                   'seri': ['SN-IP-0001', 'SN-IP-0002'], 'islem': 'kaydet'},
                       follow=True)
        self.ok(yanit, 'Satış kaydedildi: Atlas Otomasyon, 2 ürün',
                'Yeni müşteri tanımlandı: Atlas Otomasyon.')

        # 5. Diyot lotunu geri çağır → müşteri ve depodaki paneller listelenmeli.
        yanit = c.post(reverse('takip:lot_geri_cagir'), {'lot_no': 'L-D-EYLUL'}, follow=True)
        self.ok(yanit, 'L-D-EYLUL geri çağrıldı',
                '5 üründe kullanıldı', '1 müşteriye ulaştı', '3 ürün depoda',
                'Atlas Otomasyon', 'sevkiyattan önce ayrılmalı')
        for seri in ('SN-IP-0001', 'SN-IP-0002'):
            self.assertContains(yanit, f'{seri}</span>\n          <span class="not">'
                                       'İndikatör Paneli · 2 adet kullanıldı')
        self.assertEqual(yanit.content.decode().count('rozet rozet--uyari">Satıldı'), 2)
        self.assertEqual(yanit.content.decode().count('rozet rozet--basari">Depoda'), 3)
        # Ana sayfadan aramak da aynı sonuca götürür; depodaki panel satışa eklenirse uyarı verir.
        self.assertRedirects(c.get(reverse('takip:ana_sayfa'), {'q': 'l-d-eylul'}),
                             f"{reverse('takip:izlenebilirlik')}?lot=L-D-EYLUL")
        self.ok(c.post(satis_url, {'musteri': 'Beta', 'tarih': '2026-10-04',
                                   'yeni_seri': 'SN-IP-0003', 'islem': 'ekle'}),
                'Geri çağrılan lot içeriyor (L-D-EYLUL)')

        # 6. Yeni üretim → geri çağrılan lot atlanmalı.
        # Eski lotta 5 diyot kaldığı halde kullanılmaz; atlandığı da yazılır.
        self.ok(c.get(uretim_url, {'urun': urun.pk, 'adet': 2}),
                'L-D-EKIM × 4', 'SN-IP-0006 … SN-IP-0007',
                'L-D-EYLUL</span>) otomatik olarak atlanır')
        c.post(uretim_url, {'urun': urun.pk, 'adet': 2})
        yeni = Uretim.objects.get(seri_no='SN-IP-0006')
        self.assertEqual(
            list(yeni.tuketimler.filter(stok_lotu__parca__kod='1N4001')
                 .values_list('stok_lotu__lot_no', 'adet')),
            [('L-D-EKIM', 2)],
        )
        self.assertEqual(StokLotu.objects.get(lot_no='L-D-EKIM').kalan_adet, 26)
        self.assertEqual(StokLotu.objects.get(lot_no='L-D-EYLUL').kalan_adet, 5)
        self.ok(c.get(reverse('takip:izlenebilirlik'), {'seri': 'SN-IP-0006'}), 'L-D-EKIM')
        self.assertNotContains(c.get(reverse('takip:izlenebilirlik'), {'seri': 'SN-IP-0006'}),
                               'geri çağrılan')


class SenaryodaBulunanHatalarTest(TemelVeri):
    """Adım 8 tarayıcı denemesinde bulunan hatalar."""

    def yeni_satir(self, deger):
        from .forms import YeniAgacSatiriForm
        form = YeniAgacSatiriForm({'yeni_parca': deger, 'yeni_adet': 1})
        return form.is_valid(), form.cleaned_data.get('yeni_parca'), form.errors

    def test_urun_agacina_parca_adiyla_eklenebilir(self):
        # Kutu "Parça adı veya kodu ile ekle…" diyor; önceden yalnız kod çalışıyordu.
        Parca.objects.create(kod='PCB-IP', ad='Elektronik kart (PCB)')
        self.assertEqual(self.yeni_satir('diyot')[1], self.diyot)        # ad, birebir
        self.assertEqual(self.yeni_satir('led')[1], self.led)            # kod, birebir
        self.assertEqual(self.yeni_satir('pcb')[1].kod, 'PCB-IP')        # tek kısmi eşleşme
        self.assertEqual(self.yeni_satir('DIY – Diyot')[1], self.diyot)  # öneri listesi biçimi
        Parca.objects.create(kod='PCB-RK', ad='Röle kartı PCB')
        gecerli, _, hatalar = self.yeni_satir('pcb')
        self.assertFalse(gecerli)
        self.assertIn('birden fazla parçayla eşleşti: PCB-IP, PCB-RK', str(hatalar))
        self.assertIn('bulunamadı', str(self.yeni_satir('yok-boyle')[2]))

    def test_nokta_ile_biten_adda_cift_nokta_olmaz(self):
        yanit = self.client.post(reverse('takip:stok'), {
            'parca': self.led.pk, 'tedarikci': 'Elektro A.Ş.', 'lot_no': 'L-YENI',
            'adet': 5, 'siparis_tarihi': '2026-09-01',
        }, follow=True)
        self.assertContains(yanit, 'Yeni tedarikçi tanımlandı: Elektro A.Ş.<')
        self.assertNotContains(yanit, 'A.Ş..')
