"""Whisper motoru: model önbelleği, canlı ilerleme kancaları ve son işlemler.

Saf `openai-whisper` kullanılır. İki kanca sayesinde arayüz işlem sürerken boş
beklemez:

* `whisper.transcribe.tqdm` geçici olarak kendi ilerleme sınıfımızla değiştirilir
  — böylece `verbose` ne olursa olsun kare bazlı gerçek yüzdeyi alırız.
* `verbose=True` ile whisper her pencerede ürettiği segmentleri stdout'a basar;
  yalnızca çalışan iş parçacığının yazdıklarını yakalayan bir vekil ile bunları
  canlı akış olarak arayüze veririz.

İkisi de `finally` içinde eski hâline döndürülür; kanca kurulamazsa işlem yine
de çalışır, sadece ilerleme kabalaşır.
"""

from __future__ import annotations

import io
import os
import re
import sys
import threading
from types import SimpleNamespace
from typing import Any, Callable

from .config import Ayarlar, varsayilan_model_klasoru

# Modüller ağır olduğu için ilk ihtiyaçta yüklenir.
whisper: Any = None
torch: Any = None
np: Any = None

# `whisper.transcribe` paket ad alanında *fonksiyonu* gösteriyor; ilerleme
# kancası modülün kendisine kurulmalı, bu yüzden modülü ayrıca tutuyoruz.
transcribe_modulu: Any = None

# Bir pencerenin oylamaya katılması için dil olasılığının en az bu olması
# gerekir; altındakiler konuşma içermeyen (müzik/gürültü) pencereler sayılır.
PENCERE_GUVEN_TABANI = 0.5

# Dil tespiti pencereleri: uzun filmde bile bu sayının üstüne çıkmayız,
# her pencere bir model çağrısı demek.
PENCERE_UST_SINIR = 40

# whisper'ın verbose çıktısı: "[00:12.340 --> 00:15.100]  metin"
SEGMENT_DESENI = re.compile(
    r"^\[\s*(?P<bas>[\d:.]+)\s*-->\s*(?P<son>[\d:.]+)\s*\]\s?(?P<metin>.*)$"
)


class AkisiKes(Exception):
    """`model.transcribe` çağrısını yarıda kesen durumların ortak atası.

    İlerleme kancasından atılabilen tek istisna türü budur; kanca bunun
    dışındaki hataları yutar ki işlem sürsün.
    """


class KullaniciIptali(AkisiKes):
    """İptal düğmesine basıldığında akışı yukarı taşımak için."""


class IstemEkosu(AkisiKes):
    """Model, başlangıç istemini transkript sanıp tekrarlamaya başladı.

    Whisper `initial_prompt`'u metnin devamı gibi işliyor; ses zor ya da
    konuşma seyrekse model istemin kendisini üretmeye başlayabiliyor ve
    `condition_on_previous_text` açıkken bu döngü hiç kırılmıyor. Erken
    yakalayıp istemsiz yeniden denemek, saatlerce süren bir işin çöp çıkmasını
    engelliyor.
    """


def _sadelestir(metin: str) -> str:
    """Karşılaştırma için metni sadeleştirir (küçük harf, harf/rakam dışı atılır)."""
    return "".join(ch for ch in (metin or "").casefold() if ch.isalnum())


def istem_ekosu_mu(metin: str, istem: str | None, en_az: int = 12) -> bool:
    """Segment metni başlangıç isteminin (bir parçasının) tekrarı mı?

    İki ayrı sızıntı biçimi var:

    * **Tam eko** — segment doğrudan istemin kendisi. Karakter düzeyinde
      içerme denetimiyle yakalanır.
    * **Kısmi eko** — istemin başı gerçek metne karışıyor, örneğin
      «Le volgarità è un bel po'»: ilk iki kelime istemden, gerisi uydurma.
      Bunu içerme denetimi görmüyor; ardışık kelime örtüşmesine bakılır.
      Yanlış alarmı önlemek için örtüşen kelimelerden en az birinin ayırt
      edici (uzun) olması aranır — «sono», «per» gibi sıradan kelimeler tek
      başına yetmez.
    """
    if not istem:
        return False

    sade_metin = _sadelestir(metin)
    sade_istem = _sadelestir(istem)
    if sade_metin and len(sade_metin) >= en_az and (
            sade_metin in sade_istem or sade_istem in sade_metin):
        return True

    return _ortak_dizi_var_mi(metin, istem)


def _ortak_dizi_var_mi(metin: str, istem: str, en_az_kelime: int = 2,
                       ayirt_edici_uzunluk: int = 6) -> bool:
    """İki metinde ardışık ortak kelime dizisi var mı?"""
    def kelimeler(s: str) -> list[str]:
        return [_sadelestir(k) for k in (s or "").split() if _sadelestir(k)]

    m, i = kelimeler(metin), kelimeler(istem)
    if len(m) < en_az_kelime or len(i) < en_az_kelime:
        return False

    for bas in range(len(i) - en_az_kelime + 1):
        for uzunluk in range(en_az_kelime, len(i) - bas + 1):
            dizi = i[bas:bas + uzunluk]
            if not any(len(k) >= ayirt_edici_uzunluk for k in dizi):
                continue
            for j in range(len(m) - uzunluk + 1):
                if m[j:j + uzunluk] == dizi:
                    return True
    return False


# Whisper'ın eğitim verisinden gelen, konuşma olmayan yerlerde ürettiği bilinen
# artıklar. Altyazı sitelerinin jenerik satırları olduğu için model sessizlik ya
# da müzik gördüğünde bunları "en olası devam" sanıyor. Gerçek bir konuşmada
# geçme ihtimalleri yok denecek kadar az.
HALUSINASYON_KALIPLARI = (
    "transcription by castingwords",
    "subtitles by the amara.org community",
    "amara.org",
    "subtitles by",
    "altyazı m.k.",
    "altyazı: ",
    "abone ol",
    "kanala abone",
    "subscribe to my channel",
    "subscribe to the channel",
    # Aynı jenerik, whisper'ın eğitim verisindeki diğer dillerde. İspanyolca
    # olanı gerçek bir dosyada çıktı: konuşma yokken «¡Suscríbete al canal!».
    "suscríbete",
    "suscribete",
    "iscriviti al canale",
    "abonnez-vous",
    "abonniere",
    "подпишись",
    "подписывайтесь",
    "thanks for watching",
    "thank you for watching",
    "www.zeoranger.co.uk",
    "©",
)


def halusinasyon_mu(metin: str) -> bool:
    """Bilinen bir jenerik/altyazı artığı mı?"""
    sade = " ".join((metin or "").split()).casefold().strip(" .!?-—")
    if not sade:
        return False
    return any(k in sade for k in HALUSINASYON_KALIPLARI)


def halusinasyonlari_ele(segmentler: list[dict]) -> tuple[list[dict], int]:
    """Bilinen artık satırları ayıklar; (temiz, atılan_sayısı) döndürür."""
    temiz = [s for s in segmentler if not halusinasyon_mu(s.get("text") or "")]
    return temiz, len(segmentler) - len(temiz)


def bozuk_cikti_mi(segmentler: list[dict], en_az_segment: int = 8,
                   cesitlilik_esigi: float = 0.2,
                   pencere: int = 8,
                   pencere_esigi: float = 0.75) -> bool:
    """Çıktı bir tekrar döngüsüne düşmüş mü?

    Sağlıklı bir transkriptte neredeyse her blok farklıdır. Benzersiz metin
    oranı çok düşükse model kilitlenmiş demektir.

    İki ölçüm yapılır, çünkü çöküş çoğu zaman dosyanın tamamını kapsamıyor:

    * **Bütün dosya** — baştan sona tekrar döngüsü.
    * **Kayan pencere** — dosyanın bir bölümünde çöküş. Gerçek bir örnekte
      ilk 2 dakika kusursuzdu, kalan 5 dakika tek bir «Hayır hayır.»
      tekrarıydı; genel oran %86 benzersiz çıktığı için bütünsel denetim bunu
      hiç görmemişti.
    """
    metinler = [" ".join((s.get("text") or "").split()).casefold()
                for s in segmentler if (s.get("text") or "").strip()]
    if len(metinler) < en_az_segment:
        return False

    if len(set(metinler)) / len(metinler) < cesitlilik_esigi:
        return True

    if len(metinler) < pencere * 2:
        return False

    # Pencere içinde "baskın metin payı"na bakılır: tek bir metin pencerenin
    # büyük bölümünü kaplıyorsa model oraya kilitlenmiştir. Benzersiz *oran*
    # yerine bunu kullanmanın sebebi, sağlıklı dosyalarda da kısa tekrarların
    # olması («Emmek. Emmek.», «Teşekkür ederim!» ×3) — oran ölçütü bunları
    # yanlışlıkla çöküş sayıyordu.
    from collections import Counter
    for bas in range(0, len(metinler) - pencere + 1):
        dilim = metinler[bas:bas + pencere]
        baskin = Counter(dilim).most_common(1)[0][1]
        if baskin / len(dilim) >= pencere_esigi:
            return True
    return False


class BellekYetersiz(RuntimeError):
    """GPU belleği yetmediğinde; mesajı doğrudan kullanıcıya gösterilir."""


def _yariya_indir(model):
    """Ağırlıkları fp16'ya çevirir, LayerNorm katmanlarını fp32 bırakır.

    Düz `model.half()` çalışmıyor: whisper'ın LayerNorm'u
    `super().forward(x.float())` diyerek girdiyi fp32'ye çeviriyor, ağırlık da
    fp16 olunca torch «expected scalar type Float but found Half» atıyor.
    LayerNorm parametreleri toplamın binde birinden az yer kapladığı için
    fp32 bırakmanın bellek maliyeti yok; sayısal kararlılık için de tercih
    edilen yol zaten bu.
    """
    model = model.half()
    for katman in model.modules():
        if isinstance(katman, torch.nn.LayerNorm):
            katman.float()
    return model


def bellek_hatasi_mi(hata: BaseException) -> bool:
    """Bellek yetersizliğini hem tip hem metin üzerinden tanır.

    ROCm ve CUDA farklı sınıflar atabiliyor, sürüme göre de değişiyor; metne
    bakmak tek başına kırılgan, tipe bakmak tek başına eksik.
    """
    if torch is not None and isinstance(hata, getattr(torch, "OutOfMemoryError", ())):
        return True
    metin = str(hata).lower()
    return "out of memory" in metin or "hiperroroutofmemory" in metin


# --------------------------------------------------------------------------
# Kancalar
# --------------------------------------------------------------------------

def _ilerleme_cubugu(geri_cagirma: Callable[[float], None]):
    """`tqdm` yerine geçen, `disable` bayrağını yok sayan sınıf.

    whisper iki ayrı yerde tqdm kullanıyor ve ikisi farklı biçimde içe
    aktarılmış: `transcribe` modülünde `import tqdm` (yani `tqdm.tqdm`),
    paket kökünde `from tqdm import tqdm` (yani doğrudan sınıf). Bu yüzden
    burada sınıfı döndürüp çağıran taraf uygun sarmalamayı yapıyor.
    """

    class _Cubuk:
        def __init__(self, *args, total=None, **kwargs):
            self.total = float(total or 0)
            self.n = 0.0

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def update(self, n=1):
            self.n += float(n or 0)
            if self.total > 0:
                try:
                    geri_cagirma(max(0.0, min(1.0, self.n / self.total)))
                except AkisiKes:
                    raise          # iptal / istem ekosu buradan yukarı çıkmalı
                except Exception:
                    pass

        def close(self):
            pass

        def set_description(self, *_a, **_k):
            pass

        def write(self, *_a, **_k):
            pass

    return _Cubuk


class _StdoutVekili(io.TextIOBase):
    """Yalnızca belirli bir iş parçacığının yazdıklarını yakalar.

    Diğer iş parçacıkları (ve arayüz) etkilenmesin diye geri kalan her şey
    olduğu gibi asıl stdout'a aktarılır.
    """

    def __init__(self, asil, is_parcacigi_kimligi: int, satir_geldi: Callable[[str], None]):
        self._asil = asil
        self._kimlik = is_parcacigi_kimligi
        self._satir_geldi = satir_geldi
        self._tampon = ""

    def write(self, metin):
        if threading.get_ident() != self._kimlik:
            if self._asil is not None:
                try:
                    return self._asil.write(metin)
                except Exception:
                    pass
            return len(metin or "")

        self._tampon += metin or ""
        while "\n" in self._tampon:
            satir, self._tampon = self._tampon.split("\n", 1)
            satir = satir.strip()
            if satir:
                try:
                    self._satir_geldi(satir)
                except Exception:
                    pass
        return len(metin or "")

    def flush(self):
        if self._asil is not None:
            try:
                self._asil.flush()
            except Exception:
                pass

    def bosalt(self):
        """Kalan yarım satırı da teslim eder."""
        artik, self._tampon = self._tampon.strip(), ""
        if artik:
            try:
                self._satir_geldi(artik)
            except Exception:
                pass


# --------------------------------------------------------------------------
# Motor
# --------------------------------------------------------------------------

class WhisperMotoru:
    """Model önbelleği tutar; art arda gelen işler modeli yeniden yüklemez."""

    def __init__(self, log: Callable[[str], None] | None = None):
        self._log = log or (lambda mesaj: None)
        self._model = None
        self._anahtar: tuple | None = None

    # --- kurulum ---------------------------------------------------------

    def kutuphaneleri_yukle(self) -> None:
        global whisper, torch, np, transcribe_modulu
        if whisper is not None:
            return
        self._log("📚 Kütüphaneler yükleniyor (torch + whisper)… ilk açılışta ~10 sn sürer.")
        import importlib

        import numpy as _np
        import torch as _torch
        import whisper as _whisper
        np, torch, whisper = _np, _torch, _whisper

        # `whisper/__init__.py` içindeki `from .transcribe import transcribe`
        # paket üzerindeki `transcribe` adını fonksiyonla gölgeliyor; bu yüzden
        # `import whisper.transcribe as x` da fonksiyonu getirir. Modülün
        # kendisini almanın tek güvenilir yolu import sistemine sormak.
        transcribe_modulu = importlib.import_module("whisper.transcribe")

    def cihaz_sec(self, tercih: str) -> str:
        self.kutuphaneleri_yukle()
        if tercih in ("cuda", "cpu"):
            return tercih
        try:
            return "cuda" if torch.cuda.is_available() else "cpu"
        except Exception:
            return "cpu"

    def donanim_bilgisi(self) -> str:
        """Başlıktaki rozet için kısa donanım açıklaması."""
        try:
            self.kutuphaneleri_yukle()
        except Exception as e:
            return f"Donanım okunamadı ({e.__class__.__name__})"
        try:
            if not torch.cuda.is_available():
                return f"CPU · {os.cpu_count() or '?'} çekirdek"
            ad = torch.cuda.get_device_name(0)
            surum = getattr(getattr(torch, "version", None), "hip", None)
            etiket = f"ROCm {surum}" if surum else f"CUDA {getattr(torch.version, 'cuda', '')}".strip()
            return f"{ad} · {etiket}"
        except Exception:
            return "GPU"

    def modeli_hazirla(self, ayarlar: Ayarlar,
                       ilerleme: Callable[[float, str], None] | None = None) -> str:
        """Modeli (gerekiyorsa) yükler ve kullanılan cihazı döndürür.

        Model ilk kez kullanılıyorsa indirilmesi dakikalar sürebilir; indirme
        çubuğunu da yakalayıp yukarı bildiririz, yoksa arayüz sessizce donmuş
        gibi görünür.
        """
        self.kutuphaneleri_yukle()

        cihaz = self.cihaz_sec(ayarlar.cihaz)
        indirme_koku = ((ayarlar.model_klasoru or "").strip()
                        or varsayilan_model_klasoru() or None)
        # fp16 ağırlıklar bellekte yarı yer kaplıyor; anahtarın parçası olmalı
        # ki fp16 ayarı değişince model yeniden yüklensin.
        yari = bool(ayarlar.fp16) and cihaz == "cuda"
        anahtar = (ayarlar.model, cihaz, indirme_koku, yari)

        if ayarlar.threads and int(ayarlar.threads) > 0:
            try:
                torch.set_num_threads(int(ayarlar.threads))
            except Exception:
                pass

        if anahtar == self._anahtar and self._model is not None:
            self._log(f"⚡ «{ayarlar.model}» modeli zaten bellekte ({cihaz.upper()}).")
            return cihaz

        if self._model is not None:
            self._log("♻️ Ayar değişti, model yeniden yükleniyor…")
            self._model = None
            self._anahtar = None
            self._belleği_bosalt()

        self._log(f"🧠 Model yükleniyor: {ayarlar.model} → {cihaz.upper()}")

        son_bildirilen = [-10]

        def _indirme(oran: float) -> None:
            yuzde = int(oran * 100)
            if yuzde >= son_bildirilen[0] + 10:
                son_bildirilen[0] = yuzde - yuzde % 10
                self._log(f"   ⬇️ Model indiriliyor: %{son_bildirilen[0]}")
            if ilerleme:
                ilerleme(oran, f"Model indiriliyor: %{yuzde}")

        # Paket kökünde kullanım `tqdm(...)` biçiminde — sınıfın kendisi.
        eski_tqdm = getattr(whisper, "tqdm", None)
        if eski_tqdm is not None:
            whisper.tqdm = _ilerleme_cubugu(_indirme)
        try:
            self._model = self._modeli_yukle(ayarlar.model, cihaz, indirme_koku, yari)
        finally:
            if eski_tqdm is not None:
                whisper.tqdm = eski_tqdm

        self._anahtar = anahtar
        self._log(f"✅ Model belleğe yüklendi ({cihaz.upper()}, "
                  f"{self._model_boyutu()}).")
        return cihaz

    def _modeli_yukle(self, model_adi: str, cihaz: str, indirme_koku: str | None,
                      yari: bool):
        """Modeli yükler; GPU belleği yetmezse temizleyip bir kez daha dener.

        `yari`: fp16 istendiğinde ağırlıkları da yarı hassasiyete çevirir.
        Whisper ağırlıkları her zaman fp32 kuruyor (dosya fp16 olsa bile) ve
        girdiyi katman katman ağırlığa uydurmak yerine ağırlığı her ileri
        geçişte fp16'ya çeviriyor. Baştan çevirmek belleği yarıya indiriyor
        (large-v3: 5,75 GB → 2,87 GB) ve bu tekrarlayan dönüşümü de kaldırıyor.
        """
        def _yukle():
            model = whisper.load_model(model_adi, device="cpu",
                                       download_root=indirme_koku)
            if yari:
                model = _yariya_indir(model)
            return model.to(cihaz)

        try:
            return _yukle()
        except Exception as e:
            if not bellek_hatasi_mi(e):
                raise
            self._log("   ⚠️ GPU belleği yetmedi; önbellek temizlenip tekrar deneniyor…")
            self._model = None
            self._anahtar = None
            self._belleği_bosalt()
            try:
                return _yukle()
            except Exception as e2:
                if not bellek_hatasi_mi(e2):
                    raise
                raise BellekYetersiz(self._bellek_ogudu(model_adi, yari)) from e2

    def _model_boyutu(self) -> str:
        try:
            bayt = sum(p.numel() * p.element_size() for p in self._model.parameters())
            return f"{bayt / 1024 ** 3:.2f} GB"
        except Exception:
            return "boyut okunamadı"

    def _bellek_ogudu(self, model_adi: str, yari: bool) -> str:
        satirlar = [f"«{model_adi}» modeli için GPU belleği yetmedi."]
        try:
            toplam = torch.cuda.get_device_properties(0).total_memory / 1024 ** 3
            kullanilan = torch.cuda.memory_allocated() / 1024 ** 3
            satirlar.append(f"Kart: {toplam:.1f} GB, bu işlemin ayırdığı: {kullanilan:.1f} GB.")
        except Exception:
            pass
        satirlar.append("Deneyebilecekleriniz:")
        if not yari:
            satirlar.append("  • Gelişmiş sekmesinden fp16'yı açın (bellek yarıya iner).")
        satirlar.append("  • Daha küçük bir model seçin (turbo, medium, small).")
        satirlar.append("  • GPU kullanan diğer uygulamaları kapatın.")
        satirlar.append("  • Temel sekmesinden işlem birimini CPU yapın (yavaş ama çalışır).")
        return "\n".join(satirlar)

    def bosalt(self) -> bool:
        """Modeli bellekten atar; gerçekten yüklü bir model varsa True döner.

        Dönüş değeri, model hiç yüklenmemişken «bellek boşaltıldı» diye
        yanıltıcı bir satır yazmamak için.
        """
        vardi = self._model is not None
        self._model = None
        self._anahtar = None
        self._belleği_bosalt()
        return vardi

    def _belleği_bosalt(self) -> None:
        import gc
        gc.collect()
        try:
            if torch is not None and torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:
            pass

    # --- dil tespiti -----------------------------------------------------

    def dil_oyla(self, ses, pencere_sayisi: int, iptal: Callable[[], bool],
                 ilerleme: Callable[[float, str], None]):
        """Filmin geneline yayılmış pencerelerde dil tespiti yapıp oylar.

        Tek bir 30 saniyelik açılışa bakmak (whisper'ın varsayılanı) müzikle
        veya sessizlikle başlayan dosyalarda yanılıyor; bu yüzden örnekleriz.

        Döndürür: (dil_kodu | None, güven, oylar)
        """
        try:
            pencere_boyu = 16000 * 30
            toplam = len(ses) // pencere_boyu
            if toplam < 1:
                return None, 0.0, {}

            # Uzun filmlerde sabit sayıda örnek yetmiyor: konuşmanın seyrek
            # olduğu bir dosyada 8 pencerenin 7'si müziğe denk gelip tek bir
            # pencerenin oylamayı belirlemesine yol açıyor. Kabaca iki dakikada
            # bir örnek alıp tabanı ayardaki değerde tutuyoruz.
            sureye_gore = int(len(ses) / 16000 / 120)
            adet = max(1, min(max(int(pencere_sayisi), sureye_gore),
                              PENCERE_UST_SINIR, toplam))
            indeksler = sorted({i * toplam // adet for i in range(adet)})

            oylar: dict[str, float] = {}
            olasiliklar: dict[str, list[float]] = {}
            elenen = 0

            for sira, i in enumerate(indeksler, 1):
                if iptal():
                    raise KullaniciIptali()
                ilerleme(sira / len(indeksler),
                         f"Dil tespiti: {sira}/{len(indeksler)} pencere")

                dilim = ses[i * pencere_boyu:(i + 1) * pencere_boyu]
                if len(dilim) < 16000:
                    continue

                dilim = whisper.pad_or_trim(dilim)
                mel = whisper.log_mel_spectrogram(
                    dilim, n_mels=self._model.dims.n_mels).to(self._model.device)
                _, olasilik_tablosu = self._model.detect_language(mel)

                kazanan = max(olasilik_tablosu, key=olasilik_tablosu.get)
                deger = float(olasilik_tablosu[kazanan])

                # Konuşma içermeyen pencereler (müzik, gürültü, sessizlik) da
                # bir dil "kazananı" üretiyor ama düşük olasılıkla. Bunları
                # saymazsak, dosyanın çoğu konuşmasızken bile gerçek diyalog
                # pencereleri oylamayı belirler.
                if deger < PENCERE_GUVEN_TABANI:
                    elenen += 1
                    continue

                oylar[kazanan] = oylar.get(kazanan, 0.0) + deger
                olasiliklar.setdefault(kazanan, []).append(deger)

            if elenen:
                self._log(f"   {elenen}/{len(indeksler)} pencere konuşma içermediği "
                          f"için oylamaya katılmadı.")

            if not oylar:
                self._log("   Hiçbir pencere güvenilir dil vermedi; tespit whisper'a bırakılıyor.")
                return None, 0.0, {}

            kazanan = max(oylar, key=oylar.get)
            guven = sum(olasiliklar[kazanan]) / len(olasiliklar[kazanan])
            return kazanan, guven, oylar

        except KullaniciIptali:
            raise
        except Exception as e:
            self._log(f"⚠️ Oylamalı dil tespiti yapılamadı ({e}); whisper'ın kendi tespitine bırakılıyor.")
            return None, 0.0, {}

    # --- ana iş ----------------------------------------------------------

    def cevir_metne(self, ses, ayarlar: Ayarlar, dil: str | None, istem: str | None,
                    cihaz: str,
                    ilerleme: Callable[[float], None],
                    segment_geldi: Callable[[float, float, str], None],
                    bilgi_satiri: Callable[[str], None],
                    iptal: Callable[[], bool]) -> dict:
        """`model.transcribe` çağrısını canlı geri bildirimle sarar."""
        if self._model is None:
            raise RuntimeError("Model yüklenmeden çeviri istendi.")

        argumanlar = ayarlar.transcribe_argumanlari(dil, istem)
        argumanlar["fp16"] = bool(ayarlar.fp16) and cihaz == "cuda"
        argumanlar["verbose"] = True          # segment satırlarını stdout'a bassın

        def _satir(satir: str) -> None:
            eslesme = SEGMENT_DESENI.match(satir)
            if not eslesme:
                bilgi_satiri(satir)
                return
            metin = eslesme.group("metin").strip()
            if not metin:
                return
            segment_geldi(_saniyeye_cevir(eslesme.group("bas")),
                          _saniyeye_cevir(eslesme.group("son")),
                          metin)

        def _ilerleme(oran: float) -> None:
            if iptal():
                raise KullaniciIptali()
            ilerleme(oran)

        eski_stdout = sys.stdout
        vekil = _StdoutVekili(eski_stdout, threading.get_ident(), _satir)

        eski_tqdm = getattr(transcribe_modulu, "tqdm", None)
        if eski_tqdm is not None:
            # Bu modülde kullanım `tqdm.tqdm(...)` biçiminde.
            transcribe_modulu.tqdm = SimpleNamespace(tqdm=_ilerleme_cubugu(_ilerleme))

        sys.stdout = vekil
        try:
            return self._model.transcribe(audio=ses, **argumanlar)
        finally:
            sys.stdout = eski_stdout
            vekil.bosalt()
            if eski_tqdm is not None:
                transcribe_modulu.tqdm = eski_tqdm


def _saniyeye_cevir(damga: str) -> float:
    """'00:12.340' ya da '01:02:03.400' → saniye."""
    try:
        parcalar = [float(p) for p in damga.split(":")]
    except ValueError:
        return 0.0
    toplam = 0.0
    for p in parcalar:
        toplam = toplam * 60 + p
    return toplam


# --------------------------------------------------------------------------
# Segment sonrası işlemler
# --------------------------------------------------------------------------

def tekrar_filtrele(segmentler: list[dict], limit: int) -> tuple[list[dict], int, int]:
    """Arka arkaya aynı metni üreten halüsinasyon döngülerini kırpar.

    Döndürür: (temiz_segmentler, atılan_sayısı, en_uzun_tekrar)
    """
    temiz: list[dict] = []
    onceki: str | None = None
    ardisik = 0
    atilan = 0
    en_uzun = 0

    for segment in segmentler:
        anahtar = " ".join((segment.get("text") or "").split()).casefold()
        if anahtar and anahtar == onceki:
            ardisik += 1
        else:
            onceki = anahtar
            ardisik = 1
        en_uzun = max(en_uzun, ardisik)

        if ardisik <= max(1, int(limit)):
            temiz.append(segment)
        else:
            atilan += 1

    return temiz, atilan, en_uzun


def _sayi(deger) -> float | None:
    if deger is None:
        return None
    try:
        deger = float(deger)
    except (TypeError, ValueError):
        return None
    return None if deger != deger else deger      # NaN ele


def zamanlari_duzelt(segmentler: list[dict], min_sure: float = 0.05) -> list[dict]:
    """Çakışan/geriye giden/sıfır uzunluklu zamanları tek yönlü hâle getirir.

    Kelime zaman damgaları varsa onlar da aynı aralığa sıkıştırılır; yoksa
    altyazı yazıcıları segmentle çelişen kelime zamanları üretir.
    """
    onceki_son: float | None = None

    for segment in segmentler:
        bas = _sayi(segment.get("start")) or 0.0
        son = _sayi(segment.get("end"))

        if onceki_son is not None and bas < onceki_son:
            bas = onceki_son
        if son is None or son < bas + min_sure:
            son = bas + min_sure

        segment["start"], segment["end"] = bas, son
        onceki_son = son

        kelimeler = segment.get("words")
        if kelimeler:
            imlec = bas
            for kelime in kelimeler:
                k_bas = _sayi(kelime.get("start"))
                k_son = _sayi(kelime.get("end"))
                k_bas = bas if k_bas is None else min(max(k_bas, imlec), son)
                k_son = son if k_son is None else min(max(k_son, k_bas), son)
                kelime["start"], kelime["end"] = k_bas, k_son
                imlec = k_son

    return segmentler


def bos_segmentleri_ele(segmentler: list[dict]) -> list[dict]:
    return [s for s in segmentler if (s.get("text") or "").strip()]
