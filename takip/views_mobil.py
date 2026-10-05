"""Mobil (PWA) ekranlar: /m/ altında Sorgula, Mal girişi, Satış.

İş mantığı services.py'den gelir; satış formu masaüstüyle ortaktır
(views.satis_formu_isle).
"""
import hashlib
import os

from django.contrib import messages
from django.contrib.staticfiles import finders
from django.http import HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.templatetags.static import static
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

from .forms import MobilMalGirisiForm
from .models import Tedarikci
from .services import (
    SatisHatasi,
    lot_izle,
    mal_girisi,
    numara_ara,
    parca_stoklari,
    satilabilir_uretim,
    satis_uyarilari,
    seri_izle,
    stok_durumu,
)
from .views import _cumle, satis_formu_isle

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


# --- Sorgula -----------------------------------------------------------------

def sorgula(request):
    q = request.GET.get('q', '').strip()
    sonuc = None
    if q:
        bulunan = numara_ara(q)
        tur, deger = bulunan['tur'], bulunan['deger']
        if tur == 'lot':
            iz = lot_izle(deger)
            sonuc = {'tur': 'lot', 'iz': iz,
                     'satilan': len(iz['uretimler']) - len(iz['stoktaki_uretimler'])}
        elif tur == 'seri':
            sonuc = {'tur': 'seri', 'iz': seri_izle(deger)}
        elif tur == 'parca':
            grup = stok_durumu(parca_kod=deger, bitenler=False)[0]
            sonuc = {'tur': 'parca', 'grup': grup}
        else:
            sonuc = {'tur': None, 'adaylar': bulunan['adaylar']}
    return render(request, 'takip/mobil/sorgula.html', {
        'aktif': 'sorgula', 'q': q, 'sonuc': sonuc,
    })


# --- Mal girişi --------------------------------------------------------------

def giris(request):
    form = MobilMalGirisiForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        v = form.cleaned_data
        try:
            lot, yeni_tedarikci = mal_girisi(
                v['parca'], v['tedarikci'], v['lot_no'], v['adet'],
                v['siparis_tarihi'], v['siparis_no'],
            )
        except ValueError as hata:
            form.add_error(None, str(hata))
        else:
            messages.success(request, _cumle(
                f'{lot.lot_no} kaydedildi: {lot.giris_adet} {lot.parca.birim} {lot.parca.ad}'
            ))
            if yeni_tedarikci:
                messages.info(request, _cumle(f'Yeni tedarikçi tanımlandı: {lot.tedarikci.ad}'))
            return redirect('takip:m_giris')
    return render(request, 'takip/mobil/giris.html', {
        'aktif': 'giris',
        'form': form,
        'parcalar': parca_stoklari().order_by('kod'),
        'tedarikciler': Tedarikci.objects.order_by('ad'),
    })


# --- Satış -------------------------------------------------------------------

def satis(request):
    baglam = satis_formu_isle(request)
    if baglam['kaydedildi']:
        return redirect('takip:m_satis')
    return render(request, 'takip/mobil/satis.html', {**baglam, 'aktif': 'satis'})


@require_GET
def seri_kontrol(request):
    """Satış listesine eklenecek seri no'yu doğrular (JSON; okutma sırasında çağrılır)."""
    try:
        uretim = satilabilir_uretim(request.GET.get('no', ''))
    except SatisHatasi as hata:
        return JsonResponse({'ok': False, 'hata': str(hata)})
    uyarilar = satis_uyarilari([uretim.seri_no])
    return JsonResponse({
        'ok': True,
        'seri_no': uretim.seri_no,
        'urun': uretim.urun.ad,
        'geri_lotlar': uyarilar[0]['lotlar'] if uyarilar else [],
    })


# --- PWA ---------------------------------------------------------------------

def manifest(request):
    kok = reverse('takip:m_sorgula')
    veri = {
        'name': 'Lot Takip',
        'short_name': 'Lot Takip',
        'description': 'Lot / seri no sorgulama, mal girişi ve satış kaydı',
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
