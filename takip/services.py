"""İş mantığı: FIFO üretim, izlenebilirlik ve satış.

Görünümler (views.py) ve komutlar yalnızca bu fonksiyonları çağırır.
"""
import re
from dataclasses import dataclass, field

from django.db import transaction
from django.db.models import BooleanField, Count, ExpressionWrapper, F, Q, Sum
from django.db.models.functions import Coalesce
from django.utils import timezone

from .models import Parca, Satis, SatisKalemi, StokLotu, Uretim, UretimTuketim


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

    @property
    def eksik(self):
        return max(0, self.gereken - self.kullanilabilir)


@dataclass
class UretimOnizleme:
    urun: object
    adet: int
    parcalar: list           # ParcaIhtiyaci listesi

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
        parcalar.append(ihtiyac)
    return UretimOnizleme(urun=urun, adet=adet, parcalar=parcalar)


def uretim_onizle(urun, adet):
    """Kaydetmeden: her parça için hangi lottan kaç adet düşüleceğini ve eksikleri döndürür."""
    return _onizle(urun, adet)


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
    sira = _sonraki_sira(urun)
    # Her parça için FIFO sırasıyla lot listesi; ürünler tek tek bu listeden tüketir.
    lot_kuyruklari = {p.parca.pk: [d.lot for d in p.lotlar] for p in onizleme.parcalar}

    uretimler = []
    for i in range(adet):
        uretim = Uretim.objects.create(
            urun=urun, seri_no=f'{urun.seri_oneki}{sira + i:04d}', tarih=tarih
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
    lot = StokLotu.objects.select_related('parca', 'tedarikci').filter(lot_no=lot_no).first()
    if lot is None:
        return None

    satirlar = (
        lot.tuketimler.values(
            'uretim__id', 'uretim__seri_no', 'uretim__tarih', 'uretim__urun__kod',
            'uretim__urun__ad', 'uretim__satis_kalemi__satis__id',
            'uretim__satis_kalemi__satis__tarih',
            'uretim__satis_kalemi__satis__musteri__id',
            'uretim__satis_kalemi__satis__musteri__ad',
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
            m = musteriler.setdefault(
                satis['musteri_id'], {'id': satis['musteri_id'], 'ad': satis['musteri'],
                                      'seri_nolar': []}
            )
            m['seri_nolar'].append(s['uretim__seri_no'])
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
    uretim = Uretim.objects.select_related('urun').filter(seri_no=seri_no).first()
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


# --- Satış -------------------------------------------------------------------

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
