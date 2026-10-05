from django import forms
from django.db.models import Q
from django.forms import inlineformset_factory
from django.utils import timezone

from .models import Parca, StokLotu, Urun, UrunAgaci


class ParcaForm(forms.ModelForm):
    class Meta:
        model = Parca
        fields = ['kod', 'ad', 'birim', 'min_stok']
        labels = {'kod': 'Parça kodu', 'ad': 'Parça adı'}
        widgets = {
            'kod': forms.TextInput(attrs={'class': 'girdi girdi--mono',
                                          'placeholder': 'örn. STM32F103'}),
            'ad': forms.TextInput(attrs={'class': 'girdi', 'placeholder': 'örn. Mikrodenetleyici'}),
            'birim': forms.TextInput(attrs={'class': 'girdi'}),
            'min_stok': forms.NumberInput(attrs={'class': 'girdi', 'min': 0}),
        }

    def clean_kod(self):
        kod = self.cleaned_data['kod'].strip()
        if Parca.objects.filter(kod__iexact=kod).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError('Bu kodla bir parça zaten var.')
        return kod


class UrunForm(forms.ModelForm):
    class Meta:
        model = Urun
        fields = ['ad', 'kod', 'seri_oneki']
        labels = {'ad': 'Ürün adı', 'kod': 'Ürün kodu', 'seri_oneki': 'Seri no öneki'}
        help_texts = {'seri_oneki': ''}
        widgets = {
            'ad': forms.TextInput(attrs={'class': 'girdi girdi--vurgulu'}),
            'kod': forms.TextInput(attrs={'class': 'girdi girdi--mono'}),
            'seri_oneki': forms.TextInput(attrs={'class': 'girdi girdi--mono',
                                                 'placeholder': 'örn. SN-IP-'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Üretim yapılmışsa önek değişmez; yoksa seri no sırası bölünür.
        if self.instance.pk and self.instance.uretimler.exists():
            alan = self.fields['seri_oneki']
            alan.disabled = True
            alan.help_text = 'Bu üründen üretim yapıldığı için değiştirilemez.'

    def clean_kod(self):
        return self.cleaned_data['kod'].strip()

    def clean_seri_oneki(self):
        return self.cleaned_data['seri_oneki'].strip()


class UrunAgaciSatirForm(forms.ModelForm):
    class Meta:
        model = UrunAgaci
        fields = ['parca', 'adet']
        widgets = {
            'parca': forms.HiddenInput(),
            'adet': forms.NumberInput(attrs={'class': 'girdi girdi--adet', 'min': 1,
                                             'aria-label': 'Adet'}),
        }

    def clean_adet(self):
        adet = self.cleaned_data['adet']
        if adet < 1:
            raise forms.ValidationError('Adet en az 1 olmalı.')
        return adet


UrunAgaciFormSet = inlineformset_factory(
    Urun, UrunAgaci, form=UrunAgaciSatirForm, extra=0, can_delete=True
)


class YeniAgacSatiriForm(forms.Form):
    """Ürün ağacına eklenecek yeni satır (parça kodu + adet); boş bırakılabilir."""

    yeni_parca = forms.CharField(required=False)
    yeni_adet = forms.IntegerField(required=False, min_value=1)

    def clean_yeni_parca(self):
        deger = self.cleaned_data['yeni_parca'].strip()
        if not deger:
            return None
        kod = deger.split(' – ')[0].strip()  # datalist "KOD – Ad" biçimini de kabul et
        # Önce kod, sonra ad birebir; yoksa kod veya adda geçen tek parça.
        parca = (Parca.objects.filter(kod__iexact=kod).first()
                 or Parca.objects.filter(ad__iexact=deger).first())
        if parca:
            return parca
        adaylar = list(Parca.objects.filter(Q(kod__icontains=deger) | Q(ad__icontains=deger))
                       .order_by('kod')[:6])
        if len(adaylar) == 1:
            return adaylar[0]
        if adaylar:
            raise forms.ValidationError(
                f'"{deger}" birden fazla parçayla eşleşti: '
                f'{", ".join(p.kod for p in adaylar)}. Kodu yazın.'
            )
        raise forms.ValidationError(f'"{deger}" adlı veya kodlu parça bulunamadı.')

    def clean(self):
        temiz = super().clean()
        parca, adet = temiz.get('yeni_parca'), temiz.get('yeni_adet')
        if parca and not adet and 'yeni_adet' not in self.errors:
            self.add_error('yeni_adet', 'Adet girin.')
        if adet and not parca and 'yeni_parca' not in self.errors:
            self.add_error('yeni_parca', 'Parça seçin.')
        return temiz


class ParcaSecimAlani(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        return f'{obj.kod} – {obj.ad}'


class MalGirisiForm(forms.Form):
    parca = ParcaSecimAlani(
        Parca.objects.order_by('kod'), label='Parça', empty_label='Parça seçin',
        widget=forms.Select(attrs={'class': 'girdi'}),
    )
    tedarikci = forms.CharField(
        label='Tedarikçi (nereden)', max_length=200,
        widget=forms.TextInput(attrs={'class': 'girdi', 'list': 'tedarikci-listesi',
                                      'placeholder': 'Tedarikçi seçin veya yazın',
                                      'autocomplete': 'off'}),
    )
    lot_no = forms.CharField(
        label='Lot numarası', max_length=100,
        widget=forms.TextInput(attrs={'class': 'girdi girdi--mono', 'placeholder': 'L-…'}),
    )
    adet = forms.IntegerField(
        label='Adet', min_value=1,
        widget=forms.NumberInput(attrs={'class': 'girdi', 'min': 1}),
    )
    siparis_tarihi = forms.DateField(
        label='Sipariş tarihi', initial=timezone.localdate,
        widget=forms.DateInput(attrs={'class': 'girdi', 'type': 'date'}, format='%Y-%m-%d'),
    )
    siparis_no = forms.CharField(
        label='Sipariş / irsaliye no', max_length=100, required=False,
        widget=forms.TextInput(attrs={'class': 'girdi', 'placeholder': 'örn. SP-1050'}),
    )

    def clean_lot_no(self):
        lot_no = self.cleaned_data['lot_no'].strip()
        if StokLotu.objects.filter(lot_no__iexact=lot_no).exists():
            raise forms.ValidationError('Bu lot numarası zaten kayıtlı.')
        return lot_no


class UretimForm(forms.Form):
    urun = forms.ModelChoiceField(Urun.objects.all(), widget=forms.HiddenInput())
    adet = forms.IntegerField(
        min_value=1, max_value=999, initial=1,
        widget=forms.NumberInput(attrs={'class': 'girdi girdi--adet-buyuk', 'min': 1,
                                        'max': 999, 'aria-label': 'Adet'}),
    )


class SatisForm(forms.Form):
    musteri = forms.CharField(
        label='Müşteri', max_length=200,
        widget=forms.TextInput(attrs={'class': 'girdi', 'list': 'musteri-listesi',
                                      'placeholder': 'Müşteri seçin veya yazın',
                                      'autocomplete': 'off'}),
    )
    tarih = forms.DateField(
        label='Satış tarihi', initial=timezone.localdate,
        widget=forms.DateInput(attrs={'class': 'girdi', 'type': 'date'}, format='%Y-%m-%d'),
    )
