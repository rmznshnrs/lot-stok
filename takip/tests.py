from datetime import date
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse, reverse_lazy

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
