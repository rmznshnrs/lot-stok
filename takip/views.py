from django.contrib import messages
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from .forms import ParcaForm, UrunAgaciFormSet, UrunForm, YeniAgacSatiriForm
from .models import Parca, Urun, UrunAgaci
from .services import parca_stoklari


def _hazirlaniyor(request, aktif, baslik):
    return render(request, 'takip/hazirlaniyor.html', {'aktif': aktif, 'baslik': baslik})


def ana_sayfa(request):
    return _hazirlaniyor(request, 'ana_sayfa', 'Ana Sayfa')


def izlenebilirlik(request):
    return _hazirlaniyor(request, 'izlenebilirlik', 'İzlenebilirlik')


def uretim(request):
    return _hazirlaniyor(request, 'uretim', 'Üretim')


def stok(request):
    return _hazirlaniyor(request, 'stok', 'Stok')


def satis(request):
    return _hazirlaniyor(request, 'satis', 'Satış')


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
