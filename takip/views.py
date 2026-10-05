from django.contrib import messages
from django.db import transaction
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import urlencode

from .forms import (
    MalGirisiForm,
    ParcaForm,
    UretimForm,
    UrunAgaciFormSet,
    UrunForm,
    YeniAgacSatiriForm,
)
from .models import Parca, Tedarikci, Urun, UrunAgaci
from .services import (
    YetersizStok,
    mal_girisi,
    parca_stoklari,
    stok_durumu,
    uretim_onizle,
    uretim_yap,
)


def _hazirlaniyor(request, aktif, baslik):
    return render(request, 'takip/hazirlaniyor.html', {'aktif': aktif, 'baslik': baslik})


def ana_sayfa(request):
    return _hazirlaniyor(request, 'ana_sayfa', 'Ana Sayfa')


def izlenebilirlik(request):
    return _hazirlaniyor(request, 'izlenebilirlik', 'İzlenebilirlik')


def satis(request):
    return _hazirlaniyor(request, 'satis', 'Satış')


# --- Stok --------------------------------------------------------------------

def stok(request):
    gorunum = 'parca' if request.GET.get('gorunum') == 'parca' else 'lot'
    q = request.GET.get('q', '').strip()
    parca_kod = request.GET.get('parca', '').strip()
    bitenler = request.GET.get('bitenler') == '1'

    if request.method == 'POST':
        form = MalGirisiForm(request.POST)
        if form.is_valid():
            v = form.cleaned_data
            try:
                lot, yeni_tedarikci = mal_girisi(
                    v['parca'], v['tedarikci'], v['lot_no'], v['adet'],
                    v['siparis_tarihi'], v['siparis_no'],
                )
            except ValueError as hata:
                form.add_error(None, str(hata))
            else:
                messages.success(
                    request, f'{lot.lot_no} stoğa eklendi: {lot.giris_adet} {lot.parca.birim} '
                             f'{lot.parca.kod} – {lot.parca.ad}.'
                )
                if yeni_tedarikci:
                    messages.info(request, f'Yeni tedarikçi tanımlandı: {lot.tedarikci.ad}.')
                return redirect(f"{reverse('takip:stok')}?{urlencode({'q': lot.lot_no})}")
    else:
        secili = Parca.objects.filter(kod__iexact=parca_kod).first() if parca_kod else None
        form = MalGirisiForm(initial={'parca': secili} if secili else None)

    def sekme_url(g):
        parametreler = {'gorunum': g}
        if q:
            parametreler['q'] = q
        if parca_kod:
            parametreler['parca'] = parca_kod
        if bitenler:
            parametreler['bitenler'] = '1'
        return f"{reverse('takip:stok')}?{urlencode(parametreler)}"

    return render(request, 'takip/stok.html', {
        'aktif': 'stok',
        'form': form,
        'gorunum': gorunum,
        'gruplar': stok_durumu(q, parca_kod, bitenler),
        'q': q,
        'parca_kod': parca_kod,
        'bitenler': bitenler,
        'lot_url': sekme_url('lot'),
        'parca_url': sekme_url('parca'),
        'tedarikciler': Tedarikci.objects.order_by('ad'),
    })


# --- Üretim ------------------------------------------------------------------

def _uretim_url(urun, adet):
    return f"{reverse('takip:uretim')}?{urlencode({'urun': urun.pk, 'adet': adet})}"


def uretim(request):
    if request.method == 'POST':
        form = UretimForm(request.POST)
        if not form.is_valid():
            messages.error(request, 'Ürün ve 1–999 arası bir adet seçin.')
            return redirect('takip:uretim')
        urun, adet = form.cleaned_data['urun'], form.cleaned_data['adet']
        try:
            uretimler = uretim_yap(urun, adet)
        except YetersizStok as hata:
            eksik = ', '.join(f'{e.parca.kod} ({e.eksik} eksik)' for e in hata.eksikler)
            messages.error(request, f'Stok yetersiz, üretim kaydedilmedi: {eksik}.')
        except ValueError as hata:
            messages.error(request, str(hata))
        else:
            request.session['son_uretim'] = {
                'urun': urun.ad, 'seri_nolar': [u.seri_no for u in uretimler],
            }
        return redirect(_uretim_url(urun, adet))

    q = request.GET.get('q', '').strip()
    urunler = Urun.objects.annotate(parca_cesidi=Count('agac')).order_by('kod')
    if q:
        urunler = urunler.filter(Q(kod__icontains=q) | Q(ad__icontains=q))
    urun_id = request.GET.get('urun', '')
    urun = Urun.objects.filter(pk=urun_id).first() if urun_id.isdigit() else None
    if urun is None:
        urun = urunler.first() or Urun.objects.order_by('kod').first()
    adet_str = request.GET.get('adet', '')
    adet = min(max(int(adet_str), 1), 999) if adet_str.isdigit() else 1

    onizleme, hata = None, None
    if urun:
        try:
            onizleme = uretim_onizle(urun, adet)
        except ValueError as e:
            hata = str(e)
    atlanan = []
    if onizleme:
        atlanan = sorted({l.lot_no for p in onizleme.parcalar for l in p.atlanan_lotlar})

    return render(request, 'takip/uretim.html', {
        'aktif': 'uretim',
        'urunler': urunler,
        'urun': urun,
        'adet': adet,
        'q': q,
        'form': UretimForm(initial={'urun': urun, 'adet': adet}),
        'onizleme': onizleme,
        'hata': hata,
        'atlanan_lotlar': atlanan,
        'son_uretim': request.session.pop('son_uretim', None),
    })


# --- Parçalar ----------------------------------------------------------------

def parcalar(request):
    form = ParcaForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        parca = form.save()
        messages.success(request, f'{parca.kod} – {parca.ad} kaydedildi.')
        return redirect(f"{reverse('takip:parcalar')}?yeni={parca.pk}")

    q = request.GET.get('q', '').strip()
    qs = Parca.objects.prefetch_related('urunler')
    if q:
        qs = qs.filter(Q(kod__icontains=q) | Q(ad__icontains=q))
    yeni = request.GET.get('yeni', '')
    return render(request, 'takip/parcalar.html', {
        'aktif': 'parcalar',
        'form': form,
        'parcalar': parca_stoklari(qs).order_by('kod'),
        'q': q,
        'yeni_pk': int(yeni) if yeni.isdigit() else None,
    })


# --- Ürünler -----------------------------------------------------------------

def urunler(request):
    ilk = Urun.objects.order_by('kod').first()
    if ilk is None:
        return redirect('takip:urun_yeni')
    return redirect('takip:urun', pk=ilk.pk)


def urun_duzenle(request, pk=None):
    urun = get_object_or_404(Urun, pk=pk) if pk else None
    veri = request.POST if request.method == 'POST' else None
    form = UrunForm(veri, instance=urun)
    formset = UrunAgaciFormSet(
        veri, instance=urun or Urun(), prefix='agac',
        queryset=UrunAgaci.objects.select_related('parca').order_by('parca__kod'),
    )
    yeni_satir = YeniAgacSatiriForm(veri)

    if veri is not None:
        gecerli = all([form.is_valid(), formset.is_valid(), yeni_satir.is_valid()])
        yeni_parca = yeni_satir.cleaned_data.get('yeni_parca') if gecerli else None
        if yeni_parca:
            kalanlar = {
                f.cleaned_data['parca'].pk for f in formset.forms
                if f.cleaned_data and not f.cleaned_data.get('DELETE')
            }
            if yeni_parca.pk in kalanlar:
                yeni_satir.add_error(
                    'yeni_parca', f'{yeni_parca.kod} zaten ağaçta; adedini tablodan değiştirin.'
                )
                gecerli = False
        if gecerli:
            with transaction.atomic():
                urun = form.save()
                formset.instance = urun
                formset.save()
                if yeni_parca:
                    UrunAgaci.objects.create(
                        urun=urun, parca=yeni_parca, adet=yeni_satir.cleaned_data['yeni_adet']
                    )
            if yeni_parca:
                messages.success(request, f'{yeni_parca.kod} ürün ağacına eklendi.')
            else:
                messages.success(request, f'{urun.ad} kaydedildi.')
            return redirect('takip:urun', pk=urun.pk)

    stoklar = {p.pk: p for p in parca_stoklari().order_by('kod')}
    satirlar = [(f, stoklar.get(f.instance.parca_id)) for f in formset.forms]

    q = request.GET.get('q', '').strip()
    liste = Urun.objects.order_by('kod')
    if q:
        liste = liste.filter(Q(kod__icontains=q) | Q(ad__icontains=q))

    return render(request, 'takip/urunler.html', {
        'aktif': 'urunler',
        'urun': urun,
        'urunler': liste,
        'q': q,
        'form': form,
        'formset': formset,
        'satirlar': satirlar,
        'yeni_satir': yeni_satir,
        'parcalar': stoklar.values(),
    })
