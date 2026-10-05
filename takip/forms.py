from django import forms
from django.forms import inlineformset_factory

from .models import Parca, Urun, UrunAgaci


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
        parca = Parca.objects.filter(kod__iexact=kod).first()
        if parca is None:
            raise forms.ValidationError(f'"{deger}" koduyla parça bulunamadı.')
        return parca

    def clean(self):
        temiz = super().clean()
        parca, adet = temiz.get('yeni_parca'), temiz.get('yeni_adet')
        if parca and not adet and 'yeni_adet' not in self.errors:
            self.add_error('yeni_adet', 'Adet girin.')
        if adet and not parca and 'yeni_parca' not in self.errors:
            self.add_error('yeni_parca', 'Parça seçin.')
        return temiz
