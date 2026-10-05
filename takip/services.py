"""İş mantığı: FIFO üretim, izlenebilirlik ve satış.

Görünümler (views.py) ve komutlar yalnızca bu fonksiyonları çağırır.
"""
import re
from dataclasses import dataclass, field

from django.db import transaction
from django.db.models import BooleanField, Count, ExpressionWrapper, F, Q, Sum
from django.db.models.functions import Coalesce
from django.utils import timezone

from .models import (
    Musteri, Parca, Satis, SatisKalemi, StokLotu, Tedarikci, Uretim, UretimTuketim,
)


class YetersizStok(Exception):
    """Üretim için stok yetmediğinde fırlatılır; eksikler listesini taşır."""

    def __init__(self, eksikler):
        self.eksikler = eksikler
        detay = ', '.join(f'{e.parca.kod}: {e.eksik} eksik' for e in eksikler)
        super().__init__(f'Yetersiz stok – {detay}')


class SatisHatasi(Exception):
    """Satış kaydedilemediğinde (bilinmeyen / zaten satılmış seri no vb.) fırlatılır."""


# --- Stok özeti --------------------------------------------------------------

def parca_stoklari(queryset=None):
    """Parçalara stok alanlarını ekler.

    toplam_stok: tüm lotlardaki kalan; kullanilabilir_stok: geri çağrılmamış
    lotlardaki kalan; lot_sayisi: kalanı olan lot sayısı; min_altinda: kullanılabilir
    stok min_stok'un altında mı.
    """
    qs = Parca.objects.all() if queryset is None else queryset
    qs = qs.annotate(
        toplam_stok=Coalesce(Sum('lotlar__kalan_adet'), 0),
        kullanilabilir_stok=Coalesce(
            Sum('lotlar__kalan_adet', filter=Q(lotlar__geri_cagrildi=False)), 0
        ),
        lot_sayisi=Count('lotlar', filter=Q(lotlar__kalan_adet__gt=0), distinct=True),
    )
    return qs.annotate(
        min_altinda=ExpressionWrapper(
            Q(kullanilabilir_stok__lt=F('min_stok')), output_field=BooleanField()
        )
    )


def mal_girisi(parca, tedarikci_adi, lot_no, adet, siparis_tarihi, siparis_no=''):
    """Yeni stok lotu oluşturur; tedarikçi adı yoksa tedarikçiyi de oluşturur.

    (lot, tedarikci_yeni_mi) döndürür. Lot no zaten varsa ValueError.
    """
    lot_no = lot_no.strip()
    if adet < 1:
        raise ValueError('Adet en az 1 olmalı.')
    if StokLotu.objects.filter(lot_no__iexact=lot_no).exists():
        raise ValueError(f'{lot_no} lot numarası zaten kayıtlı.')
    tedarikci_adi = ' '.join(tedarikci_adi.split())
    if not lot_no or not tedarikci_adi:
        raise ValueError('Lot no ve tedarikçi boş olamaz.')
    tedarikci =Tedarikci.objects.filter(ad__iexact=tedarikci_adi).first()
    yeni = tedarikci is None
    with transaction.atomic():
        if yeni:
            tedarikci = Tedarikci.objects.create(ad=tedarikci_adi)
        lot = StokLotu.objects.create(
            parca=parca, tedarikci=tedarikci, lot_no=lot_no, giris_adet=adet,
            siparis_tarihi=siparis_tarihi, siparis_no=siparis_no.strip(),
        )
    return lot, yeni


def stok_durumu(q='', parca_kod='', bitenler=False):
    """Stok ekranı için parça bazında gruplanmış lot listesi.

    Her öğe: parca (parca_stoklari alanlarıyla), lotlar (FIFO sırasıyla; her lotta
    `sirada` = üretimde ilk kullanılacak lot mu) ve siradaki lot.
    q parça kodu/adı ya da lot no'da arar; parça eşleşirse tüm lotları, yalnız lot
    eşleşirse eşleşen lotları gösterir. bitenler=False ise kalanı 0 olan lotlar gizlenir.
    """
    parcalar = parca_stoklari().order_by('kod')
    if parca_kod:
        parcalar = parcalar.filter(kod__iexact=parca_kod)
    lot_qs = StokLotu.objects.select_related('tedarikci').order_by('siparis_tarihi', 'id')

    lot_eslesen = set()
    if q:
        lot_eslesen = set(
            lot_qs.filter(lot_no__icontains=q).values_list('parca_id', flat=True)
        )
        parca_eslesen = Q(kod__icontains=q) | Q(ad__icontains=q)
        parcalar = parcalar.filter(parca_eslesen | Q(pk__in=lot_eslesen))
        parca_eslesen_idler = set(
            Parca.objects.filter(parca_eslesen).values_list('pk', flat=True)
        )
    parcalar = list(parcalar)

    lotlar = {}
    for lot in lot_qs.filter(parca__in=parcalar):
        lotlar.setdefault(lot.parca_id, []).append(lot)

    sonuc = []
    for parca in parcalar:
        tum = lotlar.get(parca.pk, [])
        siradaki = next((l for l in tum if not l.geri_cagrildi and l.kalan_adet > 0), None)
        gosterilen = [l for l in tum if bitenler or l.kalan_adet > 0]
        if q and parca.pk not in parca_eslesen_idler:
            gosterilen = [l for l in gosterilen if q.lower() in l.lot_no.lower()]
        for lot in gosterilen:
            lot.sirada = lot is siradaki
        sonuc.append({'parca': parca, 'lotlar': gosterilen, 'siradaki': siradaki})
    return sonuc


# --- Üretim önizleme ---------------------------------------------------------

@dataclass
class LotDusumu:
    lot: StokLotu
    adet: int


@dataclass
class ParcaIhtiyaci:
    parca: object
    birim_adet: int          # 1 ürün için gereken
    gereken: int             # birim_adet × üretim adedi
    kullanilabilir: int      # geri çağrılmamış lotlardaki toplam kalan
    lotlar: list = field(default_factory=list)  # FIFO sırasıyla LotDusumu
    atlanan_lotlar: list = field(default_factory=list)  # kalanı olan geri çağrılan lotlar

    @property
    def eksik(self):
        return max(0, self.gereken - self.kullanilabilir)


@dataclass
class UretimOnizleme:
    urun: object
    adet: int
    parcalar: list           # ParcaIhtiyaci listesi
    seri_nolar: list = field(default_factory=list)  # verilecek seri numaraları

    @property
    def eksikler(self):
        return [p for p in self.parcalar if p.eksik]

    @property
    def yeterli(self):
        return not self.eksikler


def _kullanilabilir_lotlar(parca, kilitle=False):
    qs = StokLotu.objects.filter(parca=parca, geri_cagrildi=False, kalan_adet__gt=0)
    if kilitle:
        qs = qs.select_for_update()
    return list(qs.order_by('siparis_tarihi', 'id'))


def _onizle(urun, adet, kilitle=False):
    if adet < 1:
        raise ValueError('Üretim adedi en az 1 olmalı.')
    agac = list(urun.agac.select_related('parca').order_by('parca__kod'))
    if not agac:
        raise ValueError(f'{urun.kod} ürününün ürün ağacı boş.')

    parcalar = []
    for satir in agac:
        lotlar = _kullanilabilir_lotlar(satir.parca, kilitle)
        ihtiyac = ParcaIhtiyaci(
            parca=satir.parca,
            birim_adet=satir.adet,
            gereken=satir.adet * adet,
            kullanilabilir=sum(l.kalan_adet for l in lotlar),
        )
        kalan = ihtiyac.gereken
        for lot in lotlar:
            if kalan == 0:
                break
            dusulen = min(kalan, lot.kalan_adet)
            ihtiyac.lotlar.append(LotDusumu(lot, dusulen))
            kalan -= dusulen
        if not kilitle:
            ihtiyac.atlanan_lotlar = list(
                StokLotu.objects.filter(
                    parca=satir.parca, geri_cagrildi=True, kalan_adet__gt=0
                ).order_by('siparis_tarihi', 'id')
            )
        parcalar.append(ihtiyac)
    return UretimOnizleme(urun=urun, adet=adet, parcalar=parcalar)


def uretim_onizle(urun, adet):
    """Kaydetmeden: her parça için hangi lottan kaç adet düşüleceğini, eksikleri,
    atlanacak geri çağrılmış lotları ve verilecek seri numaralarını döndürür."""
    onizleme = _onizle(urun, adet)
    onizleme.seri_nolar = sonraki_seri_nolar(urun, adet)
    return onizleme


# --- Üretim ------------------------------------------------------------------

def _sonraki_sira(urun):
    desen = re.compile(rf'^{re.escape(urun.seri_oneki)}(\d+)$')
    sira = 0
    for seri in Uretim.objects.filter(seri_no__startswith=urun.seri_oneki).values_list(
        'seri_no', flat=True
    ):
        eslesme = desen.match(seri)
        if eslesme:
            sira = max(sira, int(eslesme.group(1)))
    return sira + 1


def sonraki_seri_nolar(urun, adet):
    """Şimdi üretilirse verilecek ardışık seri numaraları."""
    sira = _sonraki_sira(urun)
    return [f'{urun.seri_oneki}{sira + i:04d}' for i in range(adet)]


@transaction.atomic
def uretim_yap(urun, adet, tarih=None):
    """N adet ürün üretir: N Uretim kaydı, ardışık seri no, FIFO lot düşümü.

    Geri çağrılan lotlar kullanılmaz. Stok yetmezse hiçbir şey kaydedilmez ve
    YetersizStok fırlatılır. Oluşan Uretim kayıtlarını sırasıyla döndürür.
    """
    onizleme = _onizle(urun, adet, kilitle=True)
    if not onizleme.yeterli:
        raise YetersizStok(onizleme.eksikler)

    tarih = tarih or timezone.now()
    seri_nolar = sonraki_seri_nolar(urun, adet)
    # Her parça için FIFO sırasıyla lot listesi; ürünler tek tek bu listeden tüketir.
    lot_kuyruklari = {p.parca.pk: [d.lot for d in p.lotlar] for p in onizleme.parcalar}

    uretimler = []
    for i in range(adet):
        uretim = Uretim.objects.create(
            urun=urun, seri_no=seri_nolar[i], tarih=tarih
        )
        for ihtiyac in onizleme.parcalar:
            kuyruk = lot_kuyruklari[ihtiyac.parca.pk]
            gereken = ihtiyac.birim_adet
            while gereken:
                lot = kuyruk[0]
                dusulen = min(gereken, lot.kalan_adet)
                lot.kalan_adet -= dusulen
                gereken -= dusulen
                UretimTuketim.objects.create(uretim=uretim, stok_lotu=lot, adet=dusulen)
                if lot.kalan_adet == 0:
                    kuyruk.pop(0)
        uretimler.append(uretim)

    for ihtiyac in onizleme.parcalar:
        for dusum in ihtiyac.lotlar:
            dusum.lot.save(update_fields=['kalan_adet'])
    return uretimler


# --- İzlenebilirlik ----------------------------------------------------------

def lot_izle(lot_no):
    """Parçadan bütüne: lotun kullanıldığı seri numaraları ve satıldığı müşteriler.

    Lot bulunamazsa None döner.
    """
    lot = StokLotu.objects.select_related('parca', 'tedarikci').filter(
        lot_no__iexact=lot_no.strip()
    ).first()
    if lot is None:
        return None

    satirlar = (
        lot.tuketimler.values(
            'uretim__id', 'uretim__seri_no', 'uretim__tarih', 'uretim__urun__kod',
            'uretim__urun__ad', 'uretim__satis_kalemi__satis__id',
            'uretim__satis_kalemi__satis__tarih',
            'uretim__satis_kalemi__satis__musteri__id',
            'uretim__satis_kalemi__satis__musteri__ad',
            'uretim__satis_kalemi__satis__musteri__iletisim',
        )
        .annotate(adet=Sum('adet'))
        .order_by('uretim__seri_no')
    )
    uretimler = []
    musteriler = {}
    for s in satirlar:
        satis = None
        if s['uretim__satis_kalemi__satis__id']:
            satis = {
                'id': s['uretim__satis_kalemi__satis__id'],
                'tarih': s['uretim__satis_kalemi__satis__tarih'],
                'musteri_id': s['uretim__satis_kalemi__satis__musteri__id'],
                'musteri': s['uretim__satis_kalemi__satis__musteri__ad'],
            }
            m = musteriler.setdefault(satis['musteri_id'], {
                'id': satis['musteri_id'], 'ad': satis['musteri'],
                'iletisim': s['uretim__satis_kalemi__satis__musteri__iletisim'],
                'seri_nolar': [], 'satislar': [],
            })
            m['seri_nolar'].append(s['uretim__seri_no'])
            m['satislar'].append({'seri_no': s['uretim__seri_no'], 'tarih': satis['tarih']})
        uretimler.append({
            'id': s['uretim__id'],
            'seri_no': s['uretim__seri_no'],
            'tarih': s['uretim__tarih'],
            'urun_kod': s['uretim__urun__kod'],
            'urun_ad': s['uretim__urun__ad'],
            'adet': s['adet'],
            'satis': satis,
        })
    return {
        'lot': lot,
        'kullanilan_adet': lot.giris_adet - lot.kalan_adet,
        'uretimler': uretimler,
        'musteriler': sorted(musteriler.values(), key=lambda m: m['ad']),
        'stoktaki_uretimler': [u for u in uretimler if u['satis'] is None],
    }


def seri_izle(seri_no):
    """Bütünden parçaya: seri no'lu üründe hangi parçalar, hangi lotlardan, kaçar adet.

    Seri no bulunamazsa None döner.
    """
    uretim = Uretim.objects.select_related('urun').filter(
        seri_no__iexact=seri_no.strip()
    ).first()
    if uretim is None:
        return None

    tuketimler = uretim.tuketimler.select_related(
        'stok_lotu__parca', 'stok_lotu__tedarikci'
    ).order_by('stok_lotu__parca__kod', 'stok_lotu__siparis_tarihi', 'stok_lotu__id')
    parcalar = [
        {
            'parca': t.stok_lotu.parca,
            'lot': t.stok_lotu,
            'tedarikci': t.stok_lotu.tedarikci,
            'siparis_tarihi': t.stok_lotu.siparis_tarihi,
            'adet': t.adet,
            'geri_cagrildi': t.stok_lotu.geri_cagrildi,
        }
        for t in tuketimler
    ]
    kalem = SatisKalemi.objects.select_related('satis__musteri').filter(uretim=uretim).first()
    return {
        'uretim': uretim,
        'urun': uretim.urun,
        'parcalar': parcalar,
        'satis': kalem.satis if kalem else None,
        'musteri': kalem.satis.musteri if kalem else None,
        'geri_cagrilan_lotlar': sorted({p['lot'].lot_no for p in parcalar if p['geri_cagrildi']}),
    }


def lot_geri_cagir(lot_no, geri_cagir=True):
    """Lotu geri çağırır (veya geri çağırmayı kaldırır). Lot yoksa StokLotu.DoesNotExist."""
    lot = StokLotu.objects.get(lot_no__iexact=lot_no.strip())
    if lot.geri_cagrildi != geri_cagir:
        lot.geri_cagrildi = geri_cagir
        lot.save(update_fields=['geri_cagrildi'])
    return lot


def numara_ara(metin):
    """Girilen numaranın türünü algılar: lot no, seri no veya parça kodu.

    Önce birebir (büyük/küçük harf duyarsız) eşleşme aranır; yoksa numaranın bir
    parçası olarak geçtiği lot ve seri numaraları aday olarak döner. Sonuç:
    {'tur': 'lot' | 'seri' | 'parca' | None, 'deger': ..., 'adaylar': [(tur, deger)]}.
    Tek aday varsa doğrudan o seçilir.
    """
    metin = metin.strip()
    sonuc = {'tur': None, 'deger': None, 'adaylar': []}
    if not metin:
        return sonuc
    lot = StokLotu.objects.filter(lot_no__iexact=metin).values_list('lot_no', flat=True).first()
    if lot:
        return {**sonuc, 'tur': 'lot', 'deger': lot}
    seri = Uretim.objects.filter(seri_no__iexact=metin).values_list('seri_no', flat=True).first()
    if seri:
        return {**sonuc, 'tur': 'seri', 'deger': seri}
    kod = Parca.objects.filter(kod__iexact=metin).values_list('kod', flat=True).first()
    if kod:
        return {**sonuc, 'tur': 'parca', 'deger': kod}

    adaylar = [('lot', l) for l in StokLotu.objects.filter(lot_no__icontains=metin)
               .order_by('lot_no').values_list('lot_no', flat=True)[:10]]
    adaylar += [('seri', x) for x in Uretim.objects.filter(seri_no__icontains=metin)
                .order_by('seri_no').values_list('seri_no', flat=True)[:10]]
    if len(adaylar) == 1:
        return {**sonuc, 'tur': adaylar[0][0], 'deger': adaylar[0][1]}
    return {**sonuc, 'adaylar': adaylar}


def _aralik(seriler):
    return seriler[0] if len(seriler) == 1 else f'{seriler[0]} … {seriler[-1]}'


def son_hareketler(limit=8):
    """Ana sayfa için son mal girişi, üretim ve satışlar (yeniden eskiye).

    Aynı anda yapılan üretimler (tek uretim_yap çağrısı) tek satırda toplanır.
    Mal girişinin tarihi lotun giriş zamanıdır (sipariş tarihi değil).
    """
    hareketler = []
    lotlar = StokLotu.objects.select_related('parca', 'tedarikci').order_by(
        '-giris_zamani', '-id'
    )[:limit]
    for lot in lotlar:
        hareketler.append({
            'tur': 'giris', 'tarih': timezone.localtime(lot.giris_zamani).date(),
            'sira': lot.giris_zamani.timestamp(),
            'aciklama': f'{lot.parca.ad} · {lot.giris_adet} {lot.parca.birim} · {lot.tedarikci.ad}',
            'numara': lot.lot_no, 'numara_ilk': lot.lot_no, 'numara_turu': 'lot',
        })

    gruplar = {}
    for u in Uretim.objects.select_related('urun').order_by('-tarih', '-id')[:limit * 20]:
        g = gruplar.setdefault((u.urun_id, u.tarih), {
            'urun': u.urun, 'tarih': u.tarih, 'seri_nolar': [], 'sira': u.id,
        })
        g['seri_nolar'].append(u.seri_no)
    for g in list(gruplar.values())[:limit]:
        seriler = sorted(g['seri_nolar'])
        hareketler.append({
            'tur': 'uretim', 'tarih': timezone.localtime(g['tarih']).date(), 'sira': g['sira'],
            'aciklama': f"{g['urun'].ad} · {len(seriler)} adet",
            'numara': _aralik(seriler), 'numara_ilk': seriler[0], 'numara_turu': 'seri',
        })

    satislar = (Satis.objects.select_related('musteri')
                .prefetch_related('kalemler__uretim__urun').order_by('-tarih', '-id')[:limit])
    for satis in satislar:
        kalemler = sorted(satis.kalemler.all(), key=lambda k: k.uretim.seri_no)
        if not kalemler:
            continue
        urunler = ', '.join(sorted({k.uretim.urun.ad for k in kalemler}))
        seriler = [k.uretim.seri_no for k in kalemler]
        adet = f' · {len(seriler)} adet' if len(seriler) > 1 else ''
        hareketler.append({
            'tur': 'satis', 'tarih': satis.tarih, 'sira': satis.id,
            'aciklama': f'{urunler} · {satis.musteri.ad}{adet}',
            'numara': _aralik(seriler), 'numara_ilk': seriler[0], 'numara_turu': 'seri',
        })

    oncelik = {'satis': 2, 'uretim': 1, 'giris': 0}
    hareketler.sort(key=lambda h: (h['tarih'], oncelik[h['tur']], h['sira']), reverse=True)
    return hareketler[:limit]


# --- Satış -------------------------------------------------------------------

def musteri_getir_veya_olustur(ad):
    """Adı (büyük/küçük harf duyarsız) eşleşen müşteriyi döndürür, yoksa oluşturur.

    (musteri, yeni_mi) döndürür.
    """
    ad = ' '.join(ad.split())
    if not ad:
        raise ValueError('Müşteri adı boş olamaz.')
    musteri = Musteri.objects.filter(ad__iexact=ad).first()
    if musteri:
        return musteri, False
    return Musteri.objects.create(ad=ad), True


def satilabilir_uretim(seri_no):
    """Satış listesine eklenecek seri no'yu doğrular; Uretim döndürür.

    Bulunamazsa veya daha önce satılmışsa SatisHatasi fırlatır.
    """
    seri_no = seri_no.strip()
    uretim = Uretim.objects.select_related('urun').filter(seri_no__iexact=seri_no).first()
    if uretim is None:
        raise SatisHatasi(f'{seri_no} seri numarası bulunamadı.')
    kalem = SatisKalemi.objects.select_related('satis__musteri').filter(uretim=uretim).first()
    if kalem:
        raise SatisHatasi(
            f'{uretim.seri_no} daha önce satılmış: {kalem.satis.musteri.ad}, '
            f'{kalem.satis.tarih:%d.%m.%Y}.'
        )
    return uretim


def satis_gecmisi(q='', limit=50):
    """Satış geçmişi: her satış + ürün için bir satır (yeniden eskiye).

    q müşteri adında veya seri no'da arar.
    """
    satislar = Satis.objects.select_related('musteri').order_by('-tarih', '-id')
    if q:
        satislar = satislar.filter(
            Q(musteri__ad__icontains=q) | Q(kalemler__uretim__seri_no__icontains=q)
        ).distinct()
    satirlar = []
    for satis in satislar.prefetch_related('kalemler__uretim__urun')[:limit]:
        urunler = {}
        for kalem in satis.kalemler.all():
            urunler.setdefault(kalem.uretim.urun, []).append(kalem.uretim.seri_no)
        for urun, seriler in sorted(urunler.items(), key=lambda x: x[0].ad):
            satirlar.append({'satis': satis, 'musteri': satis.musteri, 'urun': urun,
                             'seri_nolar': sorted(seriler)})
    return satirlar


@dataclass
class SatisSonucu:
    satis: Satis
    uyarilar: list           # [{'seri_no': ..., 'lotlar': [lot_no, ...]}]


def satis_uyarilari(seri_listesi):
    """Geri çağrılan lot içeren seri numaraları ve ilgili lotlar (kaydetmeden kontrol)."""
    satirlar = (
        UretimTuketim.objects.filter(
            uretim__seri_no__in=seri_listesi, stok_lotu__geri_cagrildi=True
        )
        .values_list('uretim__seri_no', 'stok_lotu__lot_no')
        .distinct()
    )
    lotlar = {}
    for seri, lot_no in satirlar:
        lotlar.setdefault(seri, set()).add(lot_no)
    return [
        {'seri_no': seri, 'lotlar': sorted(lotlar[seri])}
        for seri in seri_listesi if seri in lotlar
    ]


@transaction.atomic
def satis_yap(musteri, seri_listesi, tarih=None):
    """Seri numaralarını müşteriye satar.

    Bilinmeyen, tekrarlanan veya daha önce satılmış seri no varsa hiçbir şey
    kaydedilmez ve SatisHatasi fırlatılır. Geri çağrılan lot içeren seri
    numaraları satışı engellemez; SatisSonucu.uyarilar içinde döner.
    """
    seri_listesi = [s.strip() for s in seri_listesi if s and s.strip()]
    if not seri_listesi:
        raise SatisHatasi('En az bir seri no girilmeli.')
    tekrarlar = sorted({s for s in seri_listesi if seri_listesi.count(s) > 1})
    if tekrarlar:
        raise SatisHatasi(f'Listede tekrarlanan seri no: {", ".join(tekrarlar)}')

    uretimler = {
        u.seri_no: u
        for u in Uretim.objects.select_for_update().filter(seri_no__in=seri_listesi)
    }
    bilinmeyen = [s for s in seri_listesi if s not in uretimler]
    if bilinmeyen:
        raise SatisHatasi(f'Bulunamayan seri no: {", ".join(bilinmeyen)}')
    satilmis = list(
        SatisKalemi.objects.filter(uretim__seri_no__in=seri_listesi)
        .order_by('uretim__seri_no')
        .values_list('uretim__seri_no', flat=True)
    )
    if satilmis:
        raise SatisHatasi(f'Daha önce satılmış seri no: {", ".join(satilmis)}')

    satis = Satis.objects.create(musteri=musteri, tarih=tarih or timezone.localdate())
    SatisKalemi.objects.bulk_create(
        SatisKalemi(satis=satis, uretim=uretimler[s]) for s in seri_listesi
    )
    return SatisSonucu(satis=satis, uyarilar=satis_uyarilari(seri_listesi))
