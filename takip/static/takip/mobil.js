/* Lot Takip mobil (PWA): kamera ile barkod / karekod okuma, satış listesi.
 *
 * Okuma: tarayıcının BarcodeDetector'ı varsa (Android Chrome) o, yoksa ZXing
 * (iOS Safari vb.). Kamera yalnızca HTTPS veya localhost üzerinde açılır.
 * JavaScript yoksa sayfalar elle girişle çalışmaya devam eder.
 */
(function () {
  'use strict';

  var FORMATLAR = ['qr_code', 'code_128', 'code_39', 'code_93', 'ean_13', 'ean_8',
                   'upc_a', 'upc_e', 'itf', 'codabar', 'data_matrix'];

  // --- Okuyucu (BarcodeDetector veya ZXing) ---------------------------------

  var okuyucuSozu = null;

  function okuyucuHazirla() {
    if (okuyucuSozu) return okuyucuSozu;
    okuyucuSozu = (async function () {
      if ('BarcodeDetector' in window) {
        try {
          var destek = await window.BarcodeDetector.getSupportedFormats();
          var secili = FORMATLAR.filter(function (f) { return destek.indexOf(f) !== -1; });
          if (secili.length) {
            var dedektor = new window.BarcodeDetector({ formats: secili });
            return async function (video) {
              var sonuc = await dedektor.detect(video);
              return sonuc.length ? sonuc[0].rawValue : null;
            };
          }
        } catch (e) { /* ZXing'e düş */ }
      }
      if (!window.ZXing) throw new Error('Barkod okuyucu yüklenemedi.');
      var Z = window.ZXing;
      var ipuclari = new Map();
      ipuclari.set(Z.DecodeHintType.TRY_HARDER, true);
      var okuyucu = new Z.MultiFormatReader();
      okuyucu.setHints(ipuclari);
      var tuval = document.createElement('canvas');
      var baglam = tuval.getContext('2d', { willReadFrequently: true });
      return async function (video) {
        var g = video.videoWidth, y = video.videoHeight;
        if (!g || !y) return null;
        // Hız için görüntüyü küçült (uzun kenar en çok 800 piksel)
        var oran = Math.min(1, 800 / Math.max(g, y));
        tuval.width = Math.round(g * oran);
        tuval.height = Math.round(y * oran);
        baglam.drawImage(video, 0, 0, tuval.width, tuval.height);
        try {
          var kaynak = new Z.HTMLCanvasElementLuminanceSource(tuval);
          var bitmap = new Z.BinaryBitmap(new Z.HybridBinarizer(kaynak));
          return okuyucu.decodeWithState(bitmap).getText();
        } catch (e) {
          return null;  // bu karede barkod yok
        }
      };
    })();
    return okuyucuSozu;
  }

  // --- Kamera ---------------------------------------------------------------

  function Tarayici(kutu, sonucGeldi) {
    this.kutu = kutu;
    this.video = kutu.querySelector('video');
    this.durum = kutu.querySelector('[data-durum]');
    this.sonucGeldi = sonucGeldi;
    this.akis = null;
    this.calisiyor = false;
    this.sonDeger = null;
    this.sonZaman = 0;
  }

  Tarayici.prototype.yaz = function (metin) {
    if (this.durum) this.durum.textContent = metin;
  };

  Tarayici.prototype.baslat = async function () {
    if (this.calisiyor) return;
    if (!window.isSecureContext || !navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      this.yaz('Kamera için güvenli bağlantı (HTTPS) gerekiyor. Numarayı elle girin.');
      return;
    }
    this.yaz('Kamera açılıyor…');
    try {
      var oku = await okuyucuHazirla();
      this.akis = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: { ideal: 'environment' }, width: { ideal: 1280 } },
        audio: false
      });
      this.video.srcObject = this.akis;
      await this.video.play();
    } catch (e) {
      this.durdur();
      this.yaz(e && e.name === 'NotAllowedError'
        ? 'Kamera izni verilmedi. Numarayı elle girin.'
        : 'Kamera açılamadı. Numarayı elle girin.');
      return;
    }
    this.calisiyor = true;
    this.kutu.classList.add('m-kamera--acik');
    this.yaz('Barkodu / etiketi kameraya tutun');
    var self = this;
    var dongu = async function () {
      if (!self.calisiyor) return;
      var deger = null;
      try { deger = await oku(self.video); } catch (e) { deger = null; }
      deger = deger && deger.trim();
      var simdi = Date.now();
      // Aynı etiket kamerada dururken tekrar tekrar okunmasın
      if (deger && (deger !== self.sonDeger || simdi - self.sonZaman > 2500)) {
        self.sonDeger = deger;
        self.sonZaman = simdi;
        if (navigator.vibrate) navigator.vibrate(60);
        self.sonucGeldi(deger, self);
      }
      if (self.calisiyor) setTimeout(dongu, 120);
    };
    dongu();
  };

  Tarayici.prototype.durdur = function () {
    this.calisiyor = false;
    this.kutu.classList.remove('m-kamera--acik');
    if (this.akis) {
      this.akis.getTracks().forEach(function (t) { t.stop(); });
      this.akis = null;
    }
    if (this.video) this.video.srcObject = null;
  };

  // --- Tam ekran tarama penceresi (Mal girişi, Satış) ------------------------

  function pencereAc(baslik, sonucGeldi) {
    var pencere = document.getElementById('tarama-penceresi');
    pencere.querySelector('[data-baslik]').textContent = baslik;
    pencere.hidden = false;
    document.body.classList.add('m-kilitli');
    var tarayici = new Tarayici(pencere.querySelector('.m-kamera'), sonucGeldi);
    var kapat = function () {
      tarayici.durdur();
      pencere.hidden = true;
      document.body.classList.remove('m-kilitli');
      pencere.querySelector('[data-kapat]').removeEventListener('click', kapat);
    };
    pencere.querySelector('[data-kapat]').addEventListener('click', kapat);
    tarayici.kapat = kapat;
    tarayici.baslat();
    return tarayici;
  }

  // --- "Tara" düğmeleri: okunan numarayı hedef kutuya yaz (istenirse ara) ----

  document.querySelectorAll('[data-tara-hedef]').forEach(function (dugme) {
    dugme.hidden = false;
    dugme.addEventListener('click', function () {
      var hedef = document.getElementById(dugme.dataset.taraHedef);
      pencereAc(dugme.dataset.taraBaslik || 'Barkod tara', function (deger, t) {
        hedef.value = deger;
        t.kapat();
        if (dugme.hasAttribute('data-tara-gonder')) hedef.form.submit();
        else hedef.focus();
      });
    });
  });

  // --- Değişince formu gönder (üretim: ürün / adet önizlemesi) ----------------

  document.querySelectorAll('[data-otomatik-gonder]').forEach(function (alan) {
    alan.addEventListener('change', function () { alan.form.submit(); });
  });

  // --- Büyük −/+ düğmeleri ---------------------------------------------------

  document.querySelectorAll('[data-adim]').forEach(function (dugme) {
    dugme.addEventListener('click', function () {
      var girdi = dugme.parentElement.querySelector('input[type="number"]');
      var en_az = parseInt(girdi.min || '1', 10), en_cok = parseInt(girdi.max || '999', 10);
      var deger = (parseInt(girdi.value, 10) || en_az) + parseInt(dugme.dataset.adim, 10);
      girdi.value = Math.min(en_cok, Math.max(en_az, deger));
      girdi.dispatchEvent(new Event('change'));
    });
  });

  // --- Onay isteyen düğmeler (lotu geri çağır) --------------------------------

  document.querySelectorAll('[data-onay]').forEach(function (dugme) {
    dugme.form.addEventListener('submit', function (e) {
      if (e.submitter === dugme && !window.confirm(dugme.dataset.onay)) e.preventDefault();
    });
  });

  // --- Kayıt formları: çift dokunuşta iki kez kaydedilmesin -------------------
  // Düğme, form verisi alındıktan sonra kilitlenir (submit sırasında kilitlenirse
  // düğmenin name/value'su gönderilmez). Satış formunun kendi koruması var.

  document.querySelectorAll('form[method="post"]:not(#m-satis-formu)').forEach(function (form) {
    var gonderildi = false;
    form.addEventListener('submit', function (e) {
      if (e.defaultPrevented) return;
      if (gonderildi) { e.preventDefault(); return; }
      gonderildi = true;
      var dugme = e.submitter;
      if (dugme) setTimeout(function () { dugme.disabled = true; }, 0);
    });
  });

  // --- Satış: seri numaralarını okutarak listeye ekle ------------------------

  var satisFormu = document.getElementById('m-satis-formu');
  if (satisFormu) {
    var liste = document.getElementById('m-sepet');
    var sablon = document.getElementById('m-sepet-sablon');
    var bosMetin = document.getElementById('m-sepet-bos');
    var sayac = document.getElementById('m-sepet-sayi');
    var onayAlani = document.getElementById('m-onay');
    var bildirim = document.getElementById('m-satis-bildirim');
    var yeniSeri = satisFormu.querySelector('[name="yeni_seri"]');

    var listedekiler = function () {
      return Array.prototype.map.call(liste.querySelectorAll('input[name="seri"]'),
        function (i) { return i.value.toLowerCase(); });
    };
    var guncelle = function () {
      var n = liste.querySelectorAll('.m-sepet-oge').length;
      sayac.textContent = n + ' ürün';
      bosMetin.hidden = n > 0;
      var uyari = liste.querySelector('.m-sepet-oge--uyari') !== null;
      onayAlani.hidden = !uyari;
      if (!uyari) onayAlani.querySelector('input').checked = false;
    };
    var bildir = function (metin, hata) {
      bildirim.textContent = metin;
      bildirim.className = 'm-bildirim' + (hata ? ' m-bildirim--hata' : '');
      bildirim.hidden = !metin;
    };
    var ekle = async function (seri) {
      seri = (seri || '').trim();
      if (!seri) return false;
      if (listedekiler().indexOf(seri.toLowerCase()) !== -1) {
        bildir(seri + ' zaten listede.', true);
        return false;
      }
      var yanit, veri;
      try {
        yanit = await fetch(satisFormu.dataset.kontrolUrl + '?no=' + encodeURIComponent(seri),
                            { headers: { 'Accept': 'application/json' } });
        veri = await yanit.json();
      } catch (e) {
        bildir('Bağlantı yok; seri no kontrol edilemedi.', true);
        return false;
      }
      if (!veri.ok) { bildir(veri.hata, true); return false; }
      if (listedekiler().indexOf(veri.seri_no.toLowerCase()) !== -1) {
        bildir(veri.seri_no + ' zaten listede.', true);
        return false;
      }
      var oge = sablon.content.firstElementChild.cloneNode(true);
      oge.querySelector('input[name="seri"]').value = veri.seri_no;
      oge.querySelector('[data-urun]').textContent = veri.urun;
      oge.querySelector('[data-seri]').textContent = veri.seri_no;
      var kaldir = oge.querySelector('button');
      kaldir.value = 'kaldir:' + veri.seri_no;
      kaldir.setAttribute('aria-label', veri.seri_no + ' listeden kaldır');
      if (veri.geri_lotlar.length) {
        oge.classList.add('m-sepet-oge--uyari');
        oge.querySelector('[data-uyari]').textContent =
          'Geri çağrılan lot içeriyor (' + veri.geri_lotlar.join(', ') + ') — satışa uygun değil';
        oge.querySelector('[data-uyari]').hidden = false;
        bildir(veri.seri_no + ' geri çağrılan lot içeriyor!', true);
        if (navigator.vibrate) navigator.vibrate([80, 60, 80]);
      } else {
        bildir(veri.seri_no + ' eklendi: ' + veri.urun, false);
      }
      liste.appendChild(oge);
      guncelle();
      return true;
    };

    // × düğmeleri sunucuya gitmeden kaldırır
    liste.addEventListener('click', function (e) {
      var dugme = e.target.closest('button[value^="kaldir:"]');
      if (!dugme) return;
      e.preventDefault();
      dugme.closest('.m-sepet-oge').remove();
      bildir('', false);
      guncelle();
    });
    // Elle "Ekle" de sayfa yenilemeden çalışır
    satisFormu.querySelector('button[value="ekle"]').addEventListener('click', async function (e) {
      e.preventDefault();
      var parcalar = yeniSeri.value.split(/[\s,;]+/).filter(Boolean);
      // Birden fazla numarada her birinin sonucu görünsün (son mesaj öncekini silmesin)
      var mesajlar = [], hataVar = false;
      for (var i = 0; i < parcalar.length; i++) {
        var tamam = await ekle(parcalar[i]);
        hataVar = hataVar || !tamam || bildirim.classList.contains('m-bildirim--hata');
        mesajlar.push(bildirim.textContent);
      }
      if (mesajlar.length > 1) bildir(mesajlar.join(' '), hataVar);
      yeniSeri.value = '';
    });
    yeniSeri.addEventListener('keydown', function (e) {
      if (e.key === 'Enter') {
        e.preventDefault();
        satisFormu.querySelector('button[value="ekle"]').click();
      }
    });
    var taraDugmesi = document.getElementById('m-seri-tara');
    taraDugmesi.hidden = false;
    taraDugmesi.addEventListener('click', function () {
      var pencereSonuc = document.querySelector('#tarama-penceresi [data-son]');
      pencereSonuc.textContent = '';
      pencereAc('Seri numaralarını okutun', async function (deger) {
        var tamam = await ekle(deger);
        pencereSonuc.textContent = (tamam ? '✓ ' : '✕ ') + bildirim.textContent;
        pencereSonuc.className = 'm-pencere-son' + (tamam ? '' : ' m-pencere-son--hata');
      });
    });
    // Çift dokunuşta iki kez kaydedilmesin. Düğme form verisi alındıktan sonra
    // kilitlenir; submit sırasında kilitlenirse "islem=kaydet" gönderilmez.
    var gonderildi = false;
    satisFormu.addEventListener('submit', function (e) {
      var gonderen = e.submitter;
      if (!gonderen || gonderen.value !== 'kaydet') return;
      if (gonderildi) { e.preventDefault(); return; }
      gonderildi = true;
      setTimeout(function () { gonderen.disabled = true; }, 0);
    });
    guncelle();
  }

  // --- Service worker (ana ekrana ekleme / çevrimdışı sayfa) ------------------

  if ('serviceWorker' in navigator && window.isSecureContext) {
    var swUrl = document.documentElement.dataset.swUrl;
    if (swUrl) navigator.serviceWorker.register(swUrl, { scope: document.documentElement.dataset.swScope });
  }
})();
