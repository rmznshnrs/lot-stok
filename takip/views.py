from django.shortcuts import redirect


def ana_sayfa(request):
    # Arayüz Adım 7'de yapılacak; şimdilik yönetim paneline yönlendir.
    return redirect('admin:index')
