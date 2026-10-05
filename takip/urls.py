from django.urls import path

from . import views, views_mobil

app_name = 'takip'

urlpatterns = [
    path('', views.ana_sayfa, name='ana_sayfa'),
    path('izlenebilirlik/', views.izlenebilirlik, name='izlenebilirlik'),
    path('izlenebilirlik/geri-cagir/', views.lot_geri_cagir_view, name='lot_geri_cagir'),
    path('uretim/', views.uretim, name='uretim'),
    path('stok/', views.stok, name='stok'),
    path('satis/', views.satis, name='satis'),
    path('urunler/', views.urunler, name='urunler'),
    path('urunler/yeni/', views.urun_duzenle, name='urun_yeni'),
    path('urunler/<int:pk>/', views.urun_duzenle, name='urun'),
    path('parcalar/', views.parcalar, name='parcalar'),

    # Mobil (PWA)
    path('m/', views_mobil.sorgula, name='m_sorgula'),
    path('m/giris/', views_mobil.giris, name='m_giris'),
    path('m/satis/', views_mobil.satis, name='m_satis'),
    path('m/api/seri/', views_mobil.seri_kontrol, name='m_seri_kontrol'),
    path('m/cevrimdisi/', views_mobil.cevrimdisi, name='m_cevrimdisi'),
    path('m/manifest.json', views_mobil.manifest, name='m_manifest'),
    path('m/sw.js', views_mobil.service_worker, name='m_sw'),
]
