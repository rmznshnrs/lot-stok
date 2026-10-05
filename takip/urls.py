from django.urls import path

from . import views

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
]
