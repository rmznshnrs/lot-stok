from django.urls import path

from . import views

app_name = 'takip'

urlpatterns = [
    path('', views.ana_sayfa, name='ana_sayfa'),
]
