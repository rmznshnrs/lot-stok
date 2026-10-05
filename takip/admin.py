from django.contrib import admin
from django.db.models import Count, Sum

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

admin.site.site_header = 'Lot Takip Yönetimi'
admin.site.site_title = 'Lot Takip'
admin.site.index_title = 'Yönetim paneli'


class SilinemezMixin:
    """Üretim ve satış kayıtları silinmez (CLAUDE.md kuralı)."""

    def has_delete_permission(self, request, obj=None):
        return False


class SaltOkunurMixin(SilinemezMixin):
    """Üretim kayıtları yalnızca services.uretim_yap ile (FIFO + transaction) oluşur."""

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(Parca)
class ParcaAdmin(admin.ModelAdmin):
    list_display = ['kod', 'ad', 'birim', 'min_stok', 'toplam_stok']
    search_fields = ['kod', 'ad']

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(_toplam=Sum('lotlar__kalan_adet'))

    @admin.display(description='Toplam stok', ordering='_toplam')
    def toplam_stok(self, obj):
        return obj._toplam or 0


@admin.register(Tedarikci)
class TedarikciAdmin(admin.ModelAdmin):
    list_display = ['ad', 'iletisim']
    search_fields = ['ad']


@admin.register(StokLotu)
class StokLotuAdmin(admin.ModelAdmin):
    list_display = [
        'lot_no', 'parca', 'tedarikci', 'siparis_tarihi', 'siparis_no',
        'giris_adet', 'kalan_adet', 'geri_cagrildi',
    ]
    list_filter = ['geri_cagrildi', 'parca', 'tedarikci']
    search_fields = ['lot_no', 'siparis_no', 'parca__kod', 'parca__ad']
    autocomplete_fields = ['parca', 'tedarikci']
    date_hierarchy = 'siparis_tarihi'
    actions = ['geri_cagir', 'geri_cagirmayi_kaldir']

    def get_readonly_fields(self, request, obj=None):
        # Lot kullanılmaya başladıktan sonra adetler elle değiştirilmesin.
        if obj and obj.tuketimler.exists():
            return ['parca', 'giris_adet', 'kalan_adet']
        return []

    @admin.action(description='Seçili lotları geri çağır')
    def geri_cagir(self, request, queryset):
        n = queryset.update(geri_cagrildi=True)
        self.message_user(request, f'{n} lot geri çağrıldı.')

    @admin.action(description='Seçili lotların geri çağırmasını kaldır')
    def geri_cagirmayi_kaldir(self, request, queryset):
        n = queryset.update(geri_cagrildi=False)
        self.message_user(request, f'{n} lotun geri çağırması kaldırıldı.')


class UrunAgaciInline(admin.TabularInline):
    model = UrunAgaci
    extra = 1
    autocomplete_fields = ['parca']


@admin.register(Urun)
class UrunAdmin(admin.ModelAdmin):
    list_display = ['kod', 'ad', 'seri_oneki', 'parca_cesidi', 'uretim_sayisi']
    search_fields = ['kod', 'ad']
    inlines = [UrunAgaciInline]

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(
            _parca=Count('agac', distinct=True),
            _uretim=Count('uretimler', distinct=True),
        )

    @admin.display(description='Parça çeşidi', ordering='_parca')
    def parca_cesidi(self, obj):
        return obj._parca

    @admin.display(description='Üretilen', ordering='_uretim')
    def uretim_sayisi(self, obj):
        return obj._uretim


class UretimTuketimInline(SaltOkunurMixin, admin.TabularInline):
    model = UretimTuketim
    extra = 0
    fields = ['stok_lotu', 'parca', 'adet', 'geri_cagrildi']
    readonly_fields = fields

    @admin.display(description='Parça')
    def parca(self, obj):
        return obj.stok_lotu.parca

    @admin.display(description='Geri çağrıldı', boolean=True)
    def geri_cagrildi(self, obj):
        return obj.stok_lotu.geri_cagrildi


@admin.register(Uretim)
class UretimAdmin(SaltOkunurMixin, admin.ModelAdmin):
    list_display = ['seri_no', 'urun', 'tarih', 'satildi']
    list_filter = ['urun']
    search_fields = ['seri_no', 'tuketimler__stok_lotu__lot_no']
    date_hierarchy = 'tarih'
    inlines = [UretimTuketimInline]

    @admin.display(description='Satıldı', boolean=True)
    def satildi(self, obj):
        return hasattr(obj, 'satis_kalemi')


@admin.register(UretimTuketim)
class UretimTuketimAdmin(SaltOkunurMixin, admin.ModelAdmin):
    list_display = ['uretim', 'stok_lotu', 'adet']
    search_fields = ['uretim__seri_no', 'stok_lotu__lot_no']
    list_select_related = ['uretim', 'stok_lotu']


@admin.register(Musteri)
class MusteriAdmin(admin.ModelAdmin):
    list_display = ['ad', 'iletisim']
    search_fields = ['ad']


class SatisKalemiInline(SilinemezMixin, admin.TabularInline):
    model = SatisKalemi
    extra = 1
    autocomplete_fields = ['uretim']


@admin.register(Satis)
class SatisAdmin(SilinemezMixin, admin.ModelAdmin):
    list_display = ['id', 'musteri', 'tarih', 'kalem_sayisi']
    list_filter = ['musteri']
    search_fields = ['musteri__ad', 'kalemler__uretim__seri_no']
    date_hierarchy = 'tarih'
    autocomplete_fields = ['musteri']
    inlines = [SatisKalemiInline]

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(_kalem=Count('kalemler'))

    @admin.display(description='Ürün adedi', ordering='_kalem')
    def kalem_sayisi(self, obj):
        return obj._kalem


@admin.register(SatisKalemi)
class SatisKalemiAdmin(SilinemezMixin, admin.ModelAdmin):
    list_display = ['satis', 'uretim']
    search_fields = ['uretim__seri_no', 'satis__musteri__ad']
    list_select_related = ['satis__musteri', 'uretim']
