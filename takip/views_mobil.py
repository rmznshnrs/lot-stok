"""Mobil uygulama (PWA): /m/ altında masaüstünün tüm işlevleri, telefona uygun.

Alt menü: Ana Sayfa, Stok, Üretim, Satış, Daha fazla (Ürünler, Parçalar,
İzlenebilirlik). İş mantığı services.py'dedir; form işleme masaüstüyle ortaktır
(views.*_isle / *_baglami). Bu modül yalnızca mobil şablonları ve adresleri seçer.
"""
import hashlib
import os

from django.contrib.staticfiles import finders
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.templatetags.static import static
from django.urls import reverse
from django.utils.http import urlencode
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

from .forms import MobilMalGirisiForm
from .models import Parca, Tedarikci, Urun
from .services import (
    SatisHatasi,
    lot_izle,
    numara_ara,
    satilabilir_uretim,
    satis_gecmisi,
    satis_uyarilari,
    seri_izle,
    son_hareketler,
    stok_durumu,
)
from .views import (
    mal_girisi_formu_isle,
    parca_formu_isle,
    parca_listesi_baglami,
    satis_formu_isle,
    uretim_baglami,
    uretim_kaydet,
    urun_formu_isle,
    urun_listesi,
)

# Service worker'ın önbelleğe aldığı dosyalar (çevrimdışı açılış için)
ONBELLEK_DOSYALARI = [
    'takip/style.css',
    'takip/mobil.js',
    'takip/vendor/zxing.min.js',
    'takip/ikon/ikon-192.png',
    'takip/ikon/ikon-512.png',
    'takip/ikon/apple-touch-icon.png',
]


def onbellek_surumu():
    """Önbelleğe alınan dosyaların değişiklik zamanı ve boyutundan kısa özet.

    Dosyalar değişince service worker'ın içeriği de değişir; tarayıcı yeni sürümü
    kurar ve eski önbelleği siler. Her istekte hesaplanır (sunucu yeniden
    başlatılmadan değişen CSS/JS de algılansın diye); yalnızca dosya bilgisi okunur.
    """
    ozet = hashlib.sha256()
    for yol in ONBELLEK_DOSYALARI:
        dosya = finders.find(yol)
        if dosya:
            bilgi = os.stat(dosya)
            ozet.update(f'{yol}:{bilgi.st_mtime_ns}:{bilgi.st_size};'.encode())
    return ozet.hexdigest()[:12]


def _adres(ad, **parametreler):
    url = reverse(f'takip:{ad}')
    parametreler = {k: v for k, v in parametreler.items() if v not in (None, '')}
    return f'{url}?{urlencode(parametreler)}' if parametreler else url


# --- 1. Ana Sayfa --------------------------------------------------------------

def ana(request):
    """Arama (kamera + elle; numara türü otomatik), 3 hızlı işlem, son hareketler."""
    q = request.GET.get('q', '').strip()
    sonuc = None
    if q:
        sonuc = numara_ara(q)
        if sonuc['tur'] == 'lot':
            return redirect(_adres('m_iz', lot=sonuc['deger']))
        if sonuc['tur'] == 'seri':
            return redirect(_adres('m_iz', sekme='seri', seri=sonuc['deger']))
        if sonuc['tur'] == 'parca':
            return redirect(_adres('m_stok', parca=sonuc['deger']))
    return render(request, 'takip/mobil/ana.html', {
        'aktif': 'ana', 'q': q, 'sonuc': sonuc, 'hareketler': son_hareketler(limit=10),
    })


# --- 2. Stok -------------------------------------------------------------------

def stok(request):
    """Parça bazında stok kartları; parçaya dokununca lotları (lot → izlenebilirlik)."""
    q = request.GET.get('q', '').strip()
    parca_kod = request.GET.get('parca', '').strip()
    bitenler = request.GET.get('bitenler') == '1'
    gruplar = stok_durumu(q, parca_kod, bitenler)
    return render(request, 'takip/mobil/stok.html', {
        'aktif': 'stok', 'alt_sekme': 'liste', 'q': q, 'parca_kod': parca_kod,
        'bitenler': bitenler, 'gruplar': gruplar,
        'secili': gruplar[0] if parca_kod and gruplar else None,
    })


def giris(request):
    """Mal girişi: parça kod/adla, lot no kamerayla okutulabilir."""
    form, lot = mal_girisi_formu_isle(
        request, MobilMalGirisiForm,
        initial={'parca': request.GET.get('parca', '')} if request.GET.get('parca') else None,
    )
    if lot:
        return redirect(_adres('m_stok', parca=lot.parca.kod))
    return render(request, 'takip/mobil/giris.html', {
        'aktif': 'stok', 'alt_sekme': 'giris', 'form': form,
        'parcalar': Parca.objects.order_by('kod'),
        'tedarikciler': Tedarikci.objects.order_by('ad'),
    })


# --- 3. Üretim -----------------------------------------------------------------

def uretim(request):
    if request.method == 'POST':
        urun, adet = uretim_kaydet(request)
        if urun is None:
            return redirect('takip:m_uretim')
        return redirect(_adres('m_uretim', urun=urun.pk, adet=adet))
    return render(request, 'takip/mobil/uretim.html', {
        **uretim_baglami(request), 'aktif': 'uretim',
    })


# --- 4. Satış ------------------------------------------------------------------

def satis(request):
    baglam = satis_formu_isle(request)
    if baglam['kaydedildi']:
        return redirect('takip:m_satis')
    return render(request, 'takip/mobil/satis.html', {
        **baglam, 'aktif': 'satis', 'alt_sekme': 'yeni',
    })


def satis_gecmis(request):
    q = request.GET.get('q', '').strip()
    return render(request, 'takip/mobil/satis_gecmis.html', {
        'aktif': 'satis', 'alt_sekme': 'gecmis', 'q': q, 'gecmis': satis_gecmisi(q),
    })


@require_GET
def seri_kontrol(request):
    """Satış listesine eklenecek seri no'yu doğrular (JSON; okutma sırasında çağrılır)."""
    try:
        uretim_kaydi = satilabilir_uretim(request.GET.get('no', ''))
    except SatisHatasi as hata:
        return JsonResponse({'ok': False, 'hata': str(hata)})
    uyarilar = satis_uyarilari([uretim_kaydi.seri_no])
    return JsonResponse({
        'ok': True,
        'seri_no': uretim_kaydi.seri_no,
        'urun': uretim_kaydi.urun.ad,
        'geri_lotlar': uyarilar[0]['lotlar'] if uyarilar else [],
    })


# --- 5. Daha fazla -------------------------------------------------------------

def daha(request):
    return render(request, 'takip/mobil/daha.html', {'aktif': 'daha'})


def izlenebilirlik(request):
    """Lot → ürün → müşteri ve seri no → parçalar; lotu geri çağırma."""
    lot_no = request.GET.get('lot', '').strip()
    seri_no = request.GET.get('seri', '').strip()
    sekme = request.GET.get('sekme')
    if sekme not in ('lot', 'seri'):
        sekme = 'seri' if seri_no and not lot_no else 'lot'
    baglam = {'aktif': 'daha', 'sekme': sekme, 'lot_no': lot_no, 'seri_no': seri_no}
    if sekme == 'lot' and lot_no:
        iz = baglam['iz'] = lot_izle(lot_no)
        if iz:
            baglam['lot_no'] = iz['lot'].lot_no
            baglam['satilan'] = len(iz['uretimler']) - len(iz['stoktaki_uretimler'])
    elif sekme == 'seri' and seri_no:
        iz = baglam['iz'] = seri_izle(seri_no)
        if iz:
            baglam['seri_no'] = iz['uretim'].seri_no
    baglam['lot_sekme_url'] = _adres('m_iz', sekme='lot', lot=baglam['lot_no'])
    baglam['seri_sekme_url'] = _adres('m_iz', sekme='seri', seri=baglam['seri_no'])
    return render(request, 'takip/mobil/izlenebilirlik.html', baglam)


def urunler(request):
    q = request.GET.get('q', '').strip()
    return render(request, 'takip/mobil/urunler.html', {
        'aktif': 'daha', 'q': q, 'urunler': urun_listesi(q),
    })


def urun(request, pk=None):
    urun_kaydi = get_object_or_404(Urun, pk=pk) if pk else None
    baglam = urun_formu_isle(request, urun_kaydi)
    if baglam['kaydedilen']:
        return redirect('takip:m_urun', pk=baglam['kaydedilen'].pk)
    return render(request, 'takip/mobil/urun.html', {**baglam, 'aktif': 'daha'})


def parcalar(request):
    return render(request, 'takip/mobil/parcalar.html', {
        **parca_listesi_baglami(request), 'aktif': 'daha',
    })


def parca_yeni(request):
    form, parca = parca_formu_isle(request)
    if parca:
        return redirect(_adres('m_parcalar', yeni=parca.pk))
    return render(request, 'takip/mobil/parca_yeni.html', {'aktif': 'daha', 'form': form})


# --- PWA -----------------------------------------------------------------------

def manifest(request):
    kok = reverse('takip:m_ana')
    veri = {
        'name': 'Lot Takip',
        'short_name': 'Lot Takip',
        'description': 'Stok, üretim, satış ve lot / seri no izlenebilirliği',
        'lang': 'tr',
        'id': kok,
        'start_url': kok,
        'scope': kok,
        'display': 'standalone',
        'orientation': 'portrait',
        'background_color': '#F6F5F1',
        'theme_color': '#1F4E8C',
        'icons': [
            {'src': static('takip/ikon/ikon-192.png'), 'sizes': '192x192', 'type': 'image/png'},
            {'src': static('takip/ikon/ikon-512.png'), 'sizes': '512x512', 'type': 'image/png'},
            {'src': static('takip/ikon/ikon-maskable-512.png'), 'sizes': '512x512',
             'type': 'image/png', 'purpose': 'maskable'},
        ],
        'shortcuts': [
            {'name': 'Mal girişi', 'url': reverse('takip:m_giris')},
            {'name': 'Üretim', 'url': reverse('takip:m_uretim')},
            {'name': 'Satış kaydı', 'url': reverse('takip:m_satis')},
        ],
    }
    yanit = JsonResponse(veri, json_dumps_params={'ensure_ascii': False})
    yanit['Content-Type'] = 'application/manifest+json'
    return yanit


@never_cache
def service_worker(request):
    icerik = render(request, 'takip/mobil/sw.js', {
        'surum': onbellek_surumu(),
        'dosyalar': [static(y) for y in ONBELLEK_DOSYALARI],
        'cevrimdisi_url': reverse('takip:m_cevrimdisi'),
    }).content
    return HttpResponse(icerik, content_type='application/javascript; charset=utf-8')


def cevrimdisi(request):
    return render(request, 'takip/mobil/cevrimdisi.html', {'aktif': ''})
