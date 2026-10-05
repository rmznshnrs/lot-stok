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

    # Mobil uygulama (PWA): alt menü Ana Sayfa, Stok, Üretim, Satış, Daha fazla
    path('m/', views_mobil.ana, name='m_ana'),
    path('m/stok/', views_mobil.stok, name='m_stok'),
    path('m/stok/giris/', views_mobil.giris, name='m_giris'),
    path('m/uretim/', views_mobil.uretim, name='m_uretim'),
    path('m/satis/', views_mobil.satis, name='m_satis'),
    path('m/satis/gecmis/', views_mobil.satis_gecmis, name='m_satis_gecmis'),
    path('m/daha/', views_mobil.daha, name='m_daha'),
    path('m/izlenebilirlik/', views_mobil.izlenebilirlik, name='m_iz'),
    path('m/urunler/', views_mobil.urunler, name='m_urunler'),
    path('m/urunler/yeni/', views_mobil.urun, name='m_urun_yeni'),
    path('m/urunler/<int:pk>/', views_mobil.urun, name='m_urun'),
    path('m/parcalar/', views_mobil.parcalar, name='m_parcalar'),
    path('m/parcalar/yeni/', views_mobil.parca_yeni, name='m_parca_yeni'),
    path('m/api/seri/', views_mobil.seri_kontrol, name='m_seri_kontrol'),
    path('m/cevrimdisi/', views_mobil.cevrimdisi, name='m_cevrimdisi'),
    path('m/manifest.json', views_mobil.manifest, name='m_manifest'),
    path('m/sw.js', views_mobil.service_worker, name='m_sw'),
]
