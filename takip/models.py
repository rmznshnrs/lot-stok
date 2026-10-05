from django.db import models
from django.utils import timezone


class Parca(models.Model):
    kod = models.CharField('Kod', max_length=50, unique=True)
    ad = models.CharField('Ad', max_length=200)
    birim = models.CharField('Birim', max_length=20, default='adet')
    min_stok = models.PositiveIntegerField('Minimum stok', default=0)

    class Meta:
        verbose_name = 'Parça'
        verbose_name_plural = 'Parçalar'
        ordering = ['kod']

    def __str__(self):
        return f'{self.kod} – {self.ad}'


class Tedarikci(models.Model):
    ad = models.CharField('Ad', max_length=200, unique=True)
    iletisim = models.TextField('İletişim', blank=True)

    class Meta:
        verbose_name = 'Tedarikçi'
        verbose_name_plural = 'Tedarikçiler'
        ordering = ['ad']

    def __str__(self):
        return self.ad


class StokLotu(models.Model):
    parca = models.ForeignKey(
        Parca, on_delete=models.PROTECT, related_name='lotlar', verbose_name='Parça'
    )
    tedarikci = models.ForeignKey(
        Tedarikci, on_delete=models.PROTECT, related_name='lotlar', verbose_name='Tedarikçi'
    )
    lot_no = models.CharField('Lot no', max_length=100, unique=True)
    siparis_tarihi = models.DateField('Sipariş tarihi')
    siparis_no = models.CharField('Sipariş no', max_length=100, blank=True)
    giris_adet = models.PositiveIntegerField('Giriş adedi')
    # Boş bırakılırsa ilk kayıtta giris_adet ile doldurulur.
    kalan_adet = models.PositiveIntegerField('Kalan adet', blank=True)
    geri_cagrildi = models.BooleanField('Geri çağrıldı', default=False)

    class Meta:
        verbose_name = 'Stok lotu'
        verbose_name_plural = 'Stok lotları'
        # FIFO sırası: en eski sipariş önce
        ordering = ['siparis_tarihi', 'id']
        constraints = [
            models.CheckConstraint(
                condition=models.Q(kalan_adet__lte=models.F('giris_adet')),
                name='kalan_adet_giris_adetten_buyuk_olamaz',
            ),
        ]

    def save(self, *args, **kwargs):
        if self.kalan_adet is None:
            self.kalan_adet = self.giris_adet
        super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.lot_no} ({self.parca.kod})'


class Urun(models.Model):
    kod = models.CharField('Kod', max_length=50, unique=True)
    ad = models.CharField('Ad', max_length=200)
    seri_oneki = models.CharField(
        'Seri no öneki', max_length=20, unique=True, help_text='Örn. SN-IP-'
    )
    parcalar = models.ManyToManyField(
        Parca, through='UrunAgaci', related_name='urunler', verbose_name='Parçalar'
    )

    class Meta:
        verbose_name = 'Ürün'
        verbose_name_plural = 'Ürünler'
        ordering = ['kod']

    def __str__(self):
        return f'{self.kod} – {self.ad}'


class UrunAgaci(models.Model):
    """1 adet ürün için gereken parça ve adedi."""

    urun = models.ForeignKey(
        Urun, on_delete=models.CASCADE, related_name='agac', verbose_name='Ürün'
    )
    parca = models.ForeignKey(
        Parca, on_delete=models.PROTECT, related_name='agac_satirlari', verbose_name='Parça'
    )
    adet = models.PositiveIntegerField('Adet')

    class Meta:
        verbose_name = 'Ürün ağacı satırı'
        verbose_name_plural = 'Ürün ağacı'
        ordering = ['urun', 'parca__kod']
        constraints = [
            models.UniqueConstraint(fields=['urun', 'parca'], name='urun_agaci_tekil'),
            models.CheckConstraint(condition=models.Q(adet__gt=0), name='urun_agaci_adet_pozitif'),
        ]

    def __str__(self):
        return f'{self.urun.kod}: {self.adet} × {self.parca.kod}'


class Uretim(models.Model):
    """Üretilen tek bir ürün; her kaydın tek seri numarası vardır."""

    urun = models.ForeignKey(
        Urun, on_delete=models.PROTECT, related_name='uretimler', verbose_name='Ürün'
    )
    seri_no = models.CharField('Seri no', max_length=50, unique=True)
    tarih = models.DateTimeField('Tarih', default=timezone.now)

    class Meta:
        verbose_name = 'Üretim'
        verbose_name_plural = 'Üretimler'
        ordering = ['-tarih', '-id']

    def __str__(self):
        return self.seri_no


class UretimTuketim(models.Model):
    """Bir üretimde hangi lottan kaç adet parça kullanıldı (izlenebilirliğin kalbi)."""

    uretim = models.ForeignKey(
        Uretim, on_delete=models.PROTECT, related_name='tuketimler', verbose_name='Üretim'
    )
    stok_lotu = models.ForeignKey(
        StokLotu, on_delete=models.PROTECT, related_name='tuketimler', verbose_name='Stok lotu'
    )
    adet = models.PositiveIntegerField('Adet')

    class Meta:
        verbose_name = 'Üretim tüketimi'
        verbose_name_plural = 'Üretim tüketimleri'
        constraints = [
            models.CheckConstraint(condition=models.Q(adet__gt=0), name='tuketim_adet_pozitif'),
        ]

    def __str__(self):
        return f'{self.uretim.seri_no}: {self.adet} × {self.stok_lotu.lot_no}'


class Musteri(models.Model):
    ad = models.CharField('Ad', max_length=200, unique=True)
    iletisim = models.TextField('İletişim', blank=True)

    class Meta:
        verbose_name = 'Müşteri'
        verbose_name_plural = 'Müşteriler'
        ordering = ['ad']

    def __str__(self):
        return self.ad


class Satis(models.Model):
    musteri = models.ForeignKey(
        Musteri, on_delete=models.PROTECT, related_name='satislar', verbose_name='Müşteri'
    )
    tarih = models.DateField('Tarih', default=timezone.localdate)

    class Meta:
        verbose_name = 'Satış'
        verbose_name_plural = 'Satışlar'
        ordering = ['-tarih', '-id']

    def __str__(self):
        return f'Satış #{self.pk} – {self.musteri} ({self.tarih:%d.%m.%Y})'


class SatisKalemi(models.Model):
    satis = models.ForeignKey(
        Satis, on_delete=models.PROTECT, related_name='kalemler', verbose_name='Satış'
    )
    # Bir seri no yalnızca bir kez satılabilir.
    uretim = models.OneToOneField(
        Uretim, on_delete=models.PROTECT, related_name='satis_kalemi', verbose_name='Seri no'
    )

    class Meta:
        verbose_name = 'Satış kalemi'
        verbose_name_plural = 'Satış kalemleri'

    def __str__(self):
        return f'{self.satis} → {self.uretim.seri_no}'
