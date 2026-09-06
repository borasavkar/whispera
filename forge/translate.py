"""Altyazıların Türkçeye çevrilmesi.

Kademeli bir yol izlenir; her kademe bir öncekinin bıraktığı boşlukları
doldurur:

1. **DeepL** — yalnızca kullanıcı bir API anahtarı girdiyse. En akıcı çeviriyi
   veren ve aylık kotası en geniş olan yol, bu yüzden ilk sırada.
2. **Toplu paket** — cümle bütünlüğü korunarak gruplanmış satırlar Google'ın
   `translate_a/single` uç noktasına tek istekte gönderilir. Anahtarsız
   yollar içinde en hızlısı ve bağlamı en iyi koruyanı budur.
3. **Yedek yol** — pakette kalan satırlar `deep_translator` üzerinden tek tek
   denenir. Yavaş ama farklı bir uç nokta kullandığı için limitlere takılan
   satırları kurtarır.
4. **MyMemory** — anahtarsız üçüncü kaynak; günlük karakter kotası dar olduğu
   için bir filmi tek başına bitiremez, kalan satırları toplar.
5. **Yerel model** — Helsinki-NLP Marian modelleri makinede çalıştırılır.
   `transformers` kurulu olmadığı ve `Whispera.spec` onu hariç tuttuğu
   için şu an devre dışı; kütüphane gelirse kendiliğinden canlanır.

Sansürsüz mod açıkken Google'ın yıldızladığı satırlar ayrıca tekrar istenir.
"""

from __future__ import annotations

import random
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, Iterator

import requests
from deep_translator import GoogleTranslator, MyMemoryTranslator

from .media import sure_metni
from .whisper_engine import KullaniciIptali

# --- Ayar sabitleri --------------------------------------------------------

MAX_KARAKTER = 1600          # bir istekte gönderilecek en fazla karakter
MAX_SATIR = 40               # bir istekte gönderilecek en fazla satır
ISCI_SAYISI = 3              # eşzamanlı istek sayısı
ZAMAN_ASIMI = 25             # saniye
ONARIM_TURU = 3              # birincil yolda tekrar deneme turu
LIMIT_ESIGI = 18             # ardışık bu kadar hatadan sonra yol kapanır
LIMIT_HIZLI = 9              # hiç başarı yokken bu kadar istekte pes et
YEDEK_ISCI = 3
YEDEK_TURU = 4
YEDEK_BEKLEME = 6.0

YEREL_MODEL_TR = "Helsinki-NLP/opus-mt-tc-big-en-tr"
YEREL_KOPRU_KALIBI = "Helsinki-NLP/opus-mt-{dil}-en"
YEREL_YIGIN = 16

# Cümle gruplama
CUMLE_MAX_BLOK = 8
CUMLE_MAX_KARAKTER = 400
CUMLE_MAX_BOSLUK = 2.0       # saniye

UC_NOKTALAR = (
    "https://translate.googleapis.com/translate_a/single",
    "https://translate.google.com/translate_a/single",
    "https://clients5.google.com/translate_a/single",
)

BASLIKLAR = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
    "Accept": "*/*",
    "Accept-Language": "tr,en;q=0.9",
}

# "s*ktir", "f**k", "***" gibi yıldızlanmış kalıplar
# Paket içinde satırları ayıran karakter (motorlar bunu koruyor).
AYIRAC = chr(10)

SANSUR_DESENI = re.compile(r"[^\W\d_]\*(?:\*+|[^\W\d_])|\*{2,}[^\W\d_]|\*{3,}", re.UNICODE)


class CeviriHatasi(Exception):
    pass


# --------------------------------------------------------------------------
# Uç nokta sarmalayıcıları
# --------------------------------------------------------------------------

class GoogleCevirici:
    """`translate_a/single` uç noktalarına dayanıklı erişim.

    İstek limiti (HTTP 429) yediğinde bütün iş parçacıklarını ortak bir "fren"
    ile yavaşlatır; limit ısrar ederse bu yolu tamamen kapatıp yukarıya haber
    verir, böylece boşuna beklenmez.
    """

    def __init__(self, iptal: Callable[[], bool] | None = None,
                 log: Callable[[str], None] | None = None):
        self._iptal = iptal or (lambda: False)
        self._log = log or (lambda mesaj: None)
        self._yerel = threading.local()
        self._kilit = threading.Lock()
        self._fren_bitisi = 0.0
        self.istek_sayisi = 0
        self.basarili_istek = 0
        self._ardisik_limit = 0
        self.limit_asildi = False

    @property
    def _oturum(self) -> requests.Session:
        oturum = getattr(self._yerel, "oturum", None)
        if oturum is None:
            oturum = requests.Session()
            oturum.headers.update(BASLIKLAR)
            self._yerel.oturum = oturum
        return oturum

    def bekle(self, saniye: float) -> None:
        """İptale duyarlı uyku."""
        bitis = time.monotonic() + saniye
        while True:
            kalan = bitis - time.monotonic()
            if kalan <= 0:
                return
            if self._iptal():
                raise KullaniciIptali()
            time.sleep(min(0.25, kalan))

    def _fren_bekle(self) -> None:
        while True:
            with self._kilit:
                kalan = self._fren_bitisi - time.monotonic()
            if kalan <= 0:
                return
            if self._iptal():
                raise KullaniciIptali()
            time.sleep(min(0.25, kalan))

    def _fren_uygula(self, saniye: float) -> None:
        with self._kilit:
            self._fren_bitisi = max(self._fren_bitisi, time.monotonic() + saniye)

    @staticmethod
    def _yaniti_coz(veri) -> str:
        parcalar: list[str] = []
        if isinstance(veri, dict):
            for cumle in veri.get("sentences") or []:
                if isinstance(cumle, dict) and isinstance(cumle.get("trans"), str):
                    parcalar.append(cumle["trans"])
        elif isinstance(veri, list) and veri and isinstance(veri[0], list):
            for cumle in veri[0]:
                if isinstance(cumle, list) and cumle and isinstance(cumle[0], str):
                    parcalar.append(cumle[0])
        return "".join(parcalar)

    def _tek_istek(self, uc_nokta: str, metin: str, kaynak: str, hedef: str) -> str:
        parametreler = {
            "client": "gtx", "dj": "1", "dt": "t",
            "ie": "UTF-8", "oe": "UTF-8",
            "sl": kaynak or "auto", "tl": hedef,
        }
        try:
            cevap = self._oturum.post(uc_nokta, params=parametreler, data={"q": metin},
                                      timeout=ZAMAN_ASIMI)
            # Bazı uç noktalar POST kabul etmiyor; o durumda GET'e düş.
            if cevap.status_code in (400, 404, 405, 411, 413, 414, 501):
                cevap = self._oturum.get(uc_nokta, params=dict(parametreler, q=metin),
                                         timeout=ZAMAN_ASIMI)
        except Exception as e:
            raise CeviriHatasi(f"{e.__class__.__name__}: {e}") from e

        with self._kilit:
            self.istek_sayisi += 1

        if cevap.status_code in (429, 503):
            self._fren_uygula(random.uniform(6.0, 11.0))
            with self._kilit:
                self._ardisik_limit += 1
                sayi = self._ardisik_limit
                hicbir_basari = self.basarili_istek == 0 and self.istek_sayisi >= LIMIT_HIZLI
                kapat = sayi >= LIMIT_ESIGI or hicbir_basari
                yeni_kapandi = not self.limit_asildi and kapat
                toplam = self.istek_sayisi
                if yeni_kapandi:
                    self.limit_asildi = True
            if yeni_kapandi:
                self._log(f"   🚧 Birincil çeviri uç noktası istek limiti veriyor "
                          f"({toplam} istek, {sayi} ardışık); bu yol kapatılıyor, "
                          f"yedek yola geçilecek.")
            raise CeviriHatasi(f"HTTP {cevap.status_code} (istek limiti)")

        if cevap.status_code != 200:
            raise CeviriHatasi(f"HTTP {cevap.status_code}")

        with self._kilit:
            self.basarili_istek += 1
            self._ardisik_limit = 0

        try:
            veri = cevap.json()
        except Exception as e:
            raise CeviriHatasi("yanıt JSON olarak çözülemedi") from e

        cevrilmis = self._yaniti_coz(veri)
        if not cevrilmis.strip():
            raise CeviriHatasi("boş çeviri döndü")
        return cevrilmis.replace("\r\n", "\n").replace("\r", "\n")

    def cevir(self, metin: str, kaynak: str, hedef: str = "tr", tur_sayisi: int = 3) -> str:
        if self.limit_asildi:
            raise CeviriHatasi("birincil uç nokta istek limitinde (devre kapalı)")
        son_hata: Exception | None = None
        for tur in range(tur_sayisi):
            for uc_nokta in UC_NOKTALAR:
                if self._iptal():
                    raise KullaniciIptali()
                self._fren_bekle()
                try:
                    return self._tek_istek(uc_nokta, metin, kaynak, hedef)
                except CeviriHatasi as e:
                    son_hata = e
            if tur < tur_sayisi - 1:
                self.bekle(min(2.0 * (2 ** tur), 15.0) + random.uniform(0, 1.0))
        raise son_hata or CeviriHatasi("çeviri uç noktalarının hiçbiri yanıt vermedi")

    def cevir_yedek(self, metin: str, kaynak: str, hedef: str = "tr") -> str | None:
        try:
            sonuc = GoogleTranslator(source=kaynak or "auto", target=hedef).translate(metin)
        except Exception:
            return None
        if sonuc is None:
            return None
        sonuc = str(sonuc).strip()
        return sonuc or None


class GeminiCevirici:
    """Bir dil modeliyle çeviri — argoyu ve küfrü yumuşatmadan aktarır.

    Google Translate argoyu sözlükten çeviriyor ve küfrü yumuşatıyor. Gerçek
    bir dosyada ölçüldü: `polla` → «horoz», `nabos` → «şalgam», `puta` →
    «fahişe», emir kipindeki `Chupa` → mastar «Emmek». Dil modeli hem bağlamı
    görüyor hem de kaydı (register) koruyor.

    Satır hizası numaralandırmayla korunur: model bir satırı atlarsa ya da
    birleştirirse sonuç sessizce kaymaz, o satır çevrilmemiş sayılır.
    """

    UC_NOKTA = ("https://generativelanguage.googleapis.com/v1beta/"
                "models/{model}:generateContent")
    MODEL_LISTESI = "https://generativelanguage.googleapis.com/v1beta/models"
    VARSAYILAN_MODEL = "gemini-3.6-flash"
    PAKET_SATIR = 40           # tek istekte gönderilecek satır sayısı
    ZAMAN_ASIMI = 90

    # Yeni modeller yoğunlukta 503 veriyor. Tek bir sarsıntıda paketi Google'a
    # düşürmek çeviri kaydını bozuyor; ölçüldü: 11 satırlık paket bir turda
    # 0/11, hemen ardından aynı istek 11/11 döndü.
    GECICI_KODLAR = (429, 500, 502, 503, 504)
    DENEME_SAYISI = 4

    YONERGE = (
        "Sen bir altyazı çevirmenisin. Sana numaralı satırlar verilecek; "
        "her satırı {hedef} diline çevir.\n"
        "Kurallar:\n"
        "1. Çıktıda her satır tam olarak «numara|çeviri» biçiminde olsun.\n"
        "2. Satır sayısı ve numaralar girdiyle birebir aynı olsun; satır "
        "birleştirme, bölme veya atlama yapma.\n"
        "3. Küfür, argo ve müstehcen ifadeleri YUMUŞATMA, sansürleme veya "
        "kibarlaştırma. Kaynaktaki sertlik derecesini aynen koru; argoyu "
        "sözlük anlamıyla değil, hedef dildeki karşılığıyla çevir.\n"
        "4. Emir kipini emir kipi olarak çevir, mastar kullanma.\n"
        "5. Açıklama, not, başlık ekleme; yalnızca numaralı satırları yaz."
    )

    def __init__(self, anahtar: str, model: str = VARSAYILAN_MODEL,
                 log: Callable[[str], None] | None = None,
                 iptal: Callable[[], bool] | None = None):
        self._anahtar = (anahtar or "").strip()
        self._model = (model or self.VARSAYILAN_MODEL).strip()
        self._model_tazelendi = False
        self._log = log or (lambda mesaj: None)
        self._iptal = iptal or (lambda: False)
        self.devre_disi = not self._anahtar
        self._oturum: requests.Session | None = None

    def _istek(self, metin: str) -> str | None:
        if self._oturum is None:
            self._oturum = requests.Session()
        govde = {
            "contents": [{"parts": [{"text": metin}]}],
            "generationConfig": {"temperature": 0.2},
            # Altyazı içeriği müstehcen olabiliyor; güvenlik süzgeçleri
            # devreye girerse çeviri boş döner ve satırlar çevrilmemiş kalır.
            "safetySettings": [
                {"category": kategori, "threshold": "BLOCK_NONE"}
                for kategori in ("HARM_CATEGORY_HARASSMENT",
                                 "HARM_CATEGORY_HATE_SPEECH",
                                 "HARM_CATEGORY_SEXUALLY_EXPLICIT",
                                 "HARM_CATEGORY_DANGEROUS_CONTENT")
            ],
        }
        son_hata = "bilinmeyen"
        for deneme in range(self.DENEME_SAYISI):
            if self._iptal():
                raise KullaniciIptali()
            cevap = self._oturum.post(
                self.UC_NOKTA.format(model=self._model),
                params={"key": self._anahtar}, json=govde,
                timeout=self.ZAMAN_ASIMI)

            if cevap.status_code == 200:
                veri = cevap.json()
                try:
                    parcalar = veri["candidates"][0]["content"]["parts"]
                except (KeyError, IndexError):
                    # Genellikle güvenlik süzgeci ya da MAX_TOKENS; tekrar
                    # denemek yerine satırı alttaki motorlara bırak.
                    return None
                # Gemini 3.x yanıtta düşünce parçaları da gönderiyor; yalnızca
                # `text` taşıyanları alıyoruz.
                return "".join(p.get("text", "") for p in parcalar)

            son_hata = f"HTTP {cevap.status_code}: {cevap.text[:120]}"

            # Model emekliye ayrılmışsa Google yerine geçeni mesajda söylüyor.
            if cevap.status_code == 404 and not self._model_tazelendi:
                yeni_model = self._yeni_model_bul(cevap.text)
                if yeni_model:
                    self._log(f"   ℹ️ Gemini modeli «{self._model}» artık "
                              f"kullanılamıyor; «{yeni_model}» modeline geçiliyor.")
                    self._model = yeni_model
                    self._model_tazelendi = True
                    continue
                raise CeviriHatasi(son_hata)

            if cevap.status_code not in self.GECICI_KODLAR:
                raise CeviriHatasi(son_hata)

            if deneme < self.DENEME_SAYISI - 1:
                self.bekle(2.0 * (2 ** deneme))

        raise CeviriHatasi(son_hata)

    def bekle(self, saniye: float) -> None:
        """İptal edilebilir bekleme."""
        bitis = time.time() + saniye
        while time.time() < bitis:
            if self._iptal():
                raise KullaniciIptali()
            time.sleep(0.2)

    def _yeni_model_bul(self, hata_metni: str) -> str | None:
        """404 sonrası kullanılabilir bir model adı bulur.

        Önce Google'ın hata mesajındaki öneriyi okur; yoksa model listesini
        çekip generateContent destekleyen bir flash modeli seçer.
        """
        onerilen = re.search(r"use\s+models/([A-Za-z0-9._-]+)", hata_metni or "")
        if onerilen:
            return onerilen.group(1)
        try:
            cevap = self._oturum.get(self.MODEL_LISTESI,
                                     params={"key": self._anahtar, "pageSize": 200},
                                     timeout=self.ZAMAN_ASIMI)
            if cevap.status_code != 200:
                return None
            modeller = cevap.json().get("models") or []
        except Exception:
            return None

        adaylar = []
        for m in modeller:
            ad = (m.get("name") or "").replace("models/", "")
            if "generateContent" not in (m.get("supportedGenerationMethods") or []):
                continue
            # Çeviri için sade metin modeli istiyoruz: görüntü/ses/araç
            # varyantlarını ve önizlemeleri eleyelim.
            if any(k in ad for k in ("image", "tts", "transcribe", "robotics",
                                     "computer-use", "preview", "banana",
                                     "lyria", "gemma", "deep-research")):
                continue
            if "flash" not in ad or "lite" in ad:
                continue
            surum = re.search(r"gemini-(\d+(?:\.\d+)?)-flash$", ad)
            adaylar.append((float(surum.group(1)) if surum else 0.0, ad))

        if not adaylar:
            return None
        adaylar.sort(reverse=True)
        return adaylar[0][1]

    @staticmethod
    def _yaniti_coz(yanit: str, indeksler: list[int]) -> dict[int, str]:
        """«numara|çeviri» satırlarını indeks→metin sözlüğüne çevirir."""
        gecerli = set(indeksler)
        sonuc: dict[int, str] = {}
        for satir in (yanit or "").splitlines():
            satir = satir.strip()
            if not satir or "|" not in satir:
                continue
            numara, _, metin = satir.partition("|")
            try:
                i = int(numara.strip().rstrip(".").strip())
            except ValueError:
                continue
            metin = metin.strip()
            if i in gecerli and metin:
                sonuc[i] = metin
        return sonuc

    def cevir_paket(self, indeksler: list[int], satirlar: list[str],
                    hedef: str = "Türkçe") -> dict[int, str]:
        """Verilen satırları çevirir; yalnızca hizası doğrulanabilenleri döndürür."""
        if self.devre_disi or not indeksler:
            return {}

        toplanan: dict[int, str] = {}
        for bas in range(0, len(indeksler), self.PAKET_SATIR):
            if self._iptal():
                raise KullaniciIptali()
            grup = indeksler[bas:bas + self.PAKET_SATIR]
            govde = AYIRAC.join(f"{i}|{satirlar[i]}" for i in grup)
            istek = (self.YONERGE.format(hedef=hedef) + AYIRAC * 2 + govde)
            try:
                yanit = self._istek(istek)
            except KullaniciIptali:
                raise
            except Exception as e:
                self._log(f"   ℹ️ Gemini paketi çevrilemedi ({e.__class__.__name__}: "
                          f"{str(e)[:70]}).")
                if "API_KEY" in str(e).upper() or "403" in str(e) or "400" in str(e):
                    self.devre_disi = True
                    self._log("   ℹ️ Gemini devre dışı bırakıldı; diğer motorlara geçiliyor.")
                    break
                continue
            toplanan.update(self._yaniti_coz(yanit or "", grup))
        return toplanan


class MyMemoryCevirici:
    """Anahtarsız üçüncü yol: MyMemory.

    Google'ın iki uç noktası da limite takıldığında devreye girer. Günlük
    karakter kotası var (anonim kullanımda ~5000), yani bir filmi tek başına
    bitiremez ama kalan satırları kurtarır. Kota dolduğunda yanıt gövdesinde
    uyarı metni dönüyor; onu görünce bu yolu da kapatırız.
    """

    # ISO-639-1 kodu birden çok yerel koda karşılık gelebiliyor; yaygın
    # olanları elle sabitliyoruz ki "en" için Akan'ın lehçesi seçilmesin.
    TERCIHLER = {
        "en": "en-GB", "pt": "pt-PT", "zh": "zh-CN", "ar": "ar-SA",
        "de": "de-DE", "fr": "fr-FR", "es": "es-ES", "it": "it-IT",
        "ru": "ru-RU", "nl": "nl-NL", "tr": "tr-TR", "ja": "ja-JP",
        "ko": "ko-KR", "pl": "pl-PL", "sv": "sv-SE", "da": "da-DK",
        "no": "no-NO", "fi": "fi-FI", "el": "el-GR", "cs": "cs-CZ",
        "he": "he-IL", "hi": "hi-IN", "id": "id-ID", "ro": "ro-RO",
        "hu": "hu-HU", "uk": "uk-UA", "bg": "bg-BG", "sr": "sr-RS",
        "hr": "hr-HR", "sk": "sk-SK", "sl": "sl-SI", "th": "th-TH",
        "vi": "vi-VN",
    }

    # MyMemory tek istekte uzun metin kabul etmiyor.
    PARCA_SINIRI = 480

    def __init__(self, log=None, iptal=None):
        self._log = log or (lambda mesaj: None)
        self._iptal = iptal or (lambda: False)
        self._tablo: dict[str, str] | None = None
        self.kota_doldu = False

    def _yerel_kod(self, dil: str) -> str | None:
        dil = (dil or "").lower().split("-")[0].split("_")[0]
        if not dil or dil == "auto":
            return None
        if dil in self.TERCIHLER:
            return self.TERCIHLER[dil]

        if self._tablo is None:
            try:
                self._tablo = MyMemoryTranslator(
                    source="en-GB", target="tr-TR").get_supported_languages(as_dict=True)
            except Exception:
                self._tablo = {}
        for kod in self._tablo.values():
            if kod.lower().startswith(f"{dil}-"):
                return kod
        return None

    def kullanilabilir(self, kaynak_dil: str) -> bool:
        return not self.kota_doldu and self._yerel_kod(kaynak_dil) is not None

    def cevir(self, metin: str, kaynak_dil: str, hedef: str = "tr") -> str | None:
        if self.kota_doldu:
            return None
        kaynak = self._yerel_kod(kaynak_dil)
        hedef_kod = self._yerel_kod(hedef) or "tr-TR"
        if not kaynak:
            return None

        parcalar = _uzunu_parcala(metin, self.PARCA_SINIRI)
        cikti: list[str] = []
        for parca in parcalar:
            if self._iptal():
                raise KullaniciIptali()
            try:
                sonuc = MyMemoryTranslator(source=kaynak, target=hedef_kod).translate(parca)
            except Exception as e:
                if "quota" in str(e).lower() or "limit" in str(e).lower():
                    self.kota_doldu = True
                    self._log("   🚧 MyMemory günlük kotası doldu; bu yol da kapatılıyor.")
                return None
            if not sonuc:
                return None
            if "MYMEMORY WARNING" in str(sonuc).upper() or "QUOTA" in str(sonuc).upper():
                self.kota_doldu = True
                self._log("   🚧 MyMemory günlük kotası doldu; bu yol da kapatılıyor.")
                return None
            cikti.append(str(sonuc).strip())
        birlesik = " ".join(cikti).strip()
        return birlesik or None


class DeepLCevirici:
    """Anahtar verilirse ilk sırada denenen motor.

    DeepL'in Türkçe çevirisi Google'dan gözle görülür biçimde daha akıcı ve
    ücretsiz katmanı ayda 500.000 karakter — bir filmin altyazısı tipik olarak
    30-50 bin karakter, yani ayda on kadar film sığıyor. Anahtar yoksa sınıf
    hiç devreye girmez.
    """

    def __init__(self, anahtar: str, log=None):
        self._anahtar = (anahtar or "").strip()
        self._log = log or (lambda mesaj: None)
        self.devre_disi = not self._anahtar

    def cevir(self, metin: str, kaynak_dil: str, hedef: str = "tr") -> str | None:
        if self.devre_disi:
            return None
        try:
            from deep_translator import DeeplTranslator
            kaynak = (kaynak_dil or "auto").split("-")[0]
            sonuc = DeeplTranslator(api_key=self._anahtar, source=kaynak,
                                    target=hedef, use_free_api=True).translate(metin)
        except Exception as e:
            # Anahtar geçersizse ya da kota dolduysa tekrar denemenin anlamı yok.
            self.devre_disi = True
            self._log(f"   ℹ️ DeepL kullanılamadı ({e.__class__.__name__}: "
                      f"{str(e)[:60]}); diğer motorlara geçiliyor.")
            return None
        sonuc = (sonuc or "").strip()
        return sonuc or None


def _uzunu_parcala(metin: str, sinir: int) -> list[str]:
    """Uzun metni sınırı aşmayan, kelime bütünlüğünü bozmayan parçalara böler."""
    metin = " ".join((metin or "").split())
    if len(metin) <= sinir:
        return [metin] if metin else []
    parcalar: list[str] = []
    mevcut = ""
    for kelime in metin.split(" "):
        aday = f"{mevcut} {kelime}".strip()
        if len(aday) > sinir and mevcut:
            parcalar.append(mevcut)
            mevcut = kelime
        else:
            mevcut = aday
    if mevcut:
        parcalar.append(mevcut)
    return parcalar


class YerelCevirici:
    """İnternet olmadan çalışan son çare: Helsinki-NLP Marian modelleri.

    Kaynak dil İngilizce değilse önce İngilizceye köprülenir (dil→en→tr).
    `transformers` kurulu değilse sessizce devre dışı kalır.
    """

    def __init__(self, log: Callable[[str], None] | None = None,
                 iptal: Callable[[], bool] | None = None):
        self._log = log or (lambda mesaj: None)
        self._iptal = iptal or (lambda: False)
        self._tr = None
        self._kopru = None
        self._hazir: bool | None = None
        self._torch = None

    def hazirla(self, kaynak_dil: str) -> bool:
        if self._hazir is not None:
            return self._hazir
        self._hazir = False

        try:
            import torch
            from transformers import MarianMTModel, MarianTokenizer
        except Exception as e:
            self._log(f"   ℹ️ Yerel çeviri kullanılamıyor (kütüphane eksik: {e}).")
            return False
        self._torch = torch

        dil = (kaynak_dil or "").lower().split("-")[0].split("_")[0]

        def yukle(ad):
            return MarianTokenizer.from_pretrained(ad), MarianMTModel.from_pretrained(ad)

        try:
            if dil in ("", "auto", "en"):
                if dil != "en":
                    self._log("   ℹ️ Kaynak dil kesin değil; yerel çeviri İngilizce varsayıyor.")
                self._log("   ⬇️ Yerel çeviri modeli hazırlanıyor (en→tr). "
                          "İlk seferde indirme birkaç dakika sürebilir…")
            else:
                self._log(f"   ⬇️ Yerel çeviri modeli hazırlanıyor ({dil}→en→tr). "
                          f"İlk seferde indirme birkaç dakika sürebilir…")
                self._kopru = yukle(YEREL_KOPRU_KALIBI.format(dil=dil))
            self._tr = yukle(YEREL_MODEL_TR)
        except Exception as e:
            self._log(f"   ℹ️ Yerel çeviri kurulamadı ({e}); bu basamak atlanıyor.")
            self._tr = self._kopru = None
            return False

        for ikili in (self._tr, self._kopru):
            if ikili:
                ikili[1].eval()
        self._hazir = True
        return True

    def _gecir(self, ikili, metinler, ilerleme=None, taban=0.0, pay=1.0) -> list[str]:
        tokenlayici, model = ikili
        cikti: list[str] = []
        for i in range(0, len(metinler), YEREL_YIGIN):
            if self._iptal():
                raise KullaniciIptali()
            yigin = tokenlayici(metinler[i:i + YEREL_YIGIN], return_tensors="pt",
                                padding=True, truncation=True, max_length=512)
            with self._torch.no_grad():
                uretilen = model.generate(**yigin, num_beams=4, max_new_tokens=256)
            cikti += [tokenlayici.decode(c, skip_special_tokens=True) for c in uretilen]
            if ilerleme:
                ilerleme(taban + pay * min(1.0, (i + YEREL_YIGIN) / max(1, len(metinler))))
        return cikti

    def cevir_liste(self, metinler: list[str], ilerleme=None) -> list[str]:
        if self._kopru:
            ara = self._gecir(self._kopru, metinler, ilerleme, 0.0, 0.5)
            return self._gecir(self._tr, ara, ilerleme, 0.5, 0.5)
        return self._gecir(self._tr, metinler, ilerleme, 0.0, 1.0)


# --------------------------------------------------------------------------
# Orkestrasyon
# --------------------------------------------------------------------------

def cevrilmeye_deger(metin: str) -> bool:
    return bool((metin or "").strip()) and any(ch.isalnum() for ch in metin)


class CeviriMotoru:
    """Altyazı bloklarını Türkçeye çevirir.

    Blok = {"start": float, "end": float, "text": str}. Sonuç, blok indeksinden
    çeviriye eşleyen bir sözlüktür; çevrilemeyen indeksler sözlükte yer almaz,
    böylece çağıran taraf orijinal metni bırakabilir.
    """

    def __init__(self, log: Callable[[str], None],
                 ilerleme: Callable[[float, str], None],
                 iptal: Callable[[], bool],
                 sansursuz: bool = True,
                 deepl_anahtari: str = "",
                 gemini_anahtari: str = "",
                 gemini_modeli: str = "gemini-2.0-flash"):
        self._log = log
        self._ilerleme = ilerleme
        self._iptal = iptal
        self._sansursuz = sansursuz
        self._cevirici: GoogleCevirici | None = None
        self._yerel: YerelCevirici | None = None
        self._mymemory: MyMemoryCevirici | None = None
        self._deepl = DeepLCevirici(deepl_anahtari, log=log)
        self._gemini = GeminiCevirici(gemini_anahtari, gemini_modeli,
                                      log=log, iptal=iptal)
        self.son_hata: str | None = None

        # `cevir()` çalıştıktan sonra doldurulur: cümle grupları (her biri blok
        # indeksleri listesi) ve cümle indeksinden çevirisine eşleme. Çağıran
        # taraf bunlardan zaman aralığına yayılmış Türkçe bloklar kurar.
        self.cumle_gruplari: list[list[int]] = []
        self.cumle_ceviriler: dict[int, str] = {}

        # Hangi motorun kaç cümle çevirdiği. Kullanıcı, çevirinin kaydını
        # (argo/küfür) beğenmediğinde suçlunun hangi motor olduğunu günlükten
        # görebilsin diye tutuluyor.
        self.motor_sayaci: dict[str, int] = {}

    # --- gruplama --------------------------------------------------------

    @staticmethod
    def _satir_gruplari(indeksler, metinler) -> Iterator[list[int]]:
        """İstek başına karakter/satır sınırını aşmayan paketler üretir."""
        grup: list[int] = []
        uzunluk = 0
        for i in indeksler:
            satir_uzunlugu = len(metinler[i]) + 1
            if grup and (uzunluk + satir_uzunlugu > MAX_KARAKTER or len(grup) >= MAX_SATIR):
                yield grup
                grup, uzunluk = [], 0
            grup.append(i)
            uzunluk += satir_uzunlugu
        if grup:
            yield grup

    @staticmethod
    def _cumle_gruplari(bloklar) -> Iterator[list[int]]:
        """Cümlesi yarım kalan blokları birleştirir.

        Altyazı blokları cümlenin ortasından kesilir; her bloğu tek başına
        çevirmek anlamı bozar. Noktalama, uzun sessizlik ya da uzunluk sınırı
        görünce grubu kapatırız.
        """
        grup: list[int] = []
        uzunluk = 0
        onceki_bit: float | None = None

        for i, blok in enumerate(bloklar):
            metin = " ".join((blok.get("text") or "").split())

            if not cevrilmeye_deger(metin):
                if grup:
                    yield grup
                    grup, uzunluk = [], 0
                yield [i]
                onceki_bit = blok["end"]
                continue

            if grup and onceki_bit is not None and (blok["start"] - onceki_bit) > CUMLE_MAX_BOSLUK:
                yield grup
                grup, uzunluk = [], 0

            grup.append(i)
            uzunluk += len(metin) + 1
            onceki_bit = blok["end"]

            son = metin.rstrip("\"'»)]}").rstrip()
            cumle_bitti = son.endswith((".", "?", "!", "…", ":", "؟"))

            if cumle_bitti or len(grup) >= CUMLE_MAX_BLOK or uzunluk >= CUMLE_MAX_KARAKTER:
                yield grup
                grup, uzunluk = [], 0

        if grup:
            yield grup

    @staticmethod
    def _ceviriyi_dagit(ceviri: str, orijinal_metinler: list[str]) -> list[str] | None:
        """Cümle çevirisini kaynak blokların uzunluk oranına göre paylaştırır.

        Kelime sayısı blok sayısından azsa bölmek anlamsız olur; None döner ve
        çağıran taraf o grubu tek tek çevirmeye düşer.
        """
        kelimeler = (ceviri or "").split()
        n = len(orijinal_metinler)

        if n == 1:
            return [ceviri]
        if len(kelimeler) < n:
            return None

        toplam = sum(len(m) for m in orijinal_metinler) or 1
        sonuc: list[str] = []
        kalan = kelimeler

        for i, metin in enumerate(orijinal_metinler):
            if i == n - 1:
                sonuc.append(" ".join(kalan))
                break
            adet = max(1, round(len(kelimeler) * len(metin) / toplam))
            adet = min(adet, len(kalan) - (n - 1 - i))   # sonrakilere en az 1'er kelime kalsın
            sonuc.append(" ".join(kalan[:adet]))
            kalan = kalan[adet:]

        return sonuc

    # --- tek satır / grup ------------------------------------------------

    def _tek_satir_cevir(self, metin: str, kaynak_dil: str, yedek_kullan: bool = True) -> str | None:
        temiz = (metin or "").strip()
        if not cevrilmeye_deger(temiz):
            return metin

        deepl = self._deepl.cevir(temiz, kaynak_dil)
        if deepl:
            return deepl

        try:
            sonuc = self._cevirici.cevir(temiz, kaynak_dil)
            if sonuc.strip():
                return sonuc.strip()
        except KullaniciIptali:
            raise
        except CeviriHatasi:
            pass

        if not yedek_kullan or self._iptal():
            return None
        return self._cevirici.cevir_yedek(temiz, kaynak_dil)

    def _grup_cevir(self, grup, satirlar, kaynak_dil, sonuclar) -> None:
        """Bir paketi tek istekte çevirir; satır sayısı tutmazsa ikiye böler."""
        if not grup:
            return

        metinler = [satirlar[i] for i in grup]
        hizasiz = False

        # Anahtar varsa önce DeepL: hem daha akıcı hem de aylık kotası geniş.
        deepl = self._deepl.cevir(AYIRAC.join(metinler), kaynak_dil)
        if deepl:
            parcalar = deepl.split(AYIRAC)
            if len(parcalar) == len(grup):
                for i, parca in zip(grup, parcalar):
                    parca = parca.strip()
                    sonuclar[i] = parca if parca else satirlar[i]
                return

        try:
            cevrilmis = self._cevirici.cevir("\n".join(metinler), kaynak_dil)
            parcalar = cevrilmis.split("\n")
            if len(parcalar) == len(grup):
                for i, parca in zip(grup, parcalar):
                    parca = parca.strip()
                    sonuclar[i] = parca if parca else satirlar[i]
                return
            hizasiz = True
        except KullaniciIptali:
            raise
        except CeviriHatasi as e:
            self.son_hata = str(e)

        if not hizasiz:
            return          # ağ hatası: onarım turuna bırak, bölmenin faydası yok

        if len(grup) == 1:
            ceviri = self._tek_satir_cevir(satirlar[grup[0]], kaynak_dil)
            if ceviri is not None:
                sonuclar[grup[0]] = ceviri
            return

        orta = len(grup) // 2
        self._grup_cevir(grup[:orta], satirlar, kaynak_dil, sonuclar)
        self._grup_cevir(grup[orta:], satirlar, kaynak_dil, sonuclar)

    # --- ana akış --------------------------------------------------------

    def cevir(self, bloklar: list[dict], kaynak_dil: str) -> dict[int, str]:
        """Blok indeksinden çeviriye eşleme döndürür (eski, uyumluluk yolu).

        Yeni yol `cumle_ceviri` alanlarını kullanır: çeviriyi kaynak blok
        sınırlarına zorlamak yerine cümleyi bir bütün olarak zaman aralığına
        yaymak gerekiyor — bkz. `segment.metni_zamana_yay`.
        """
        self._cevirici = GoogleCevirici(iptal=self._iptal, log=self._log)
        self.son_hata = None
        self.motor_sayaci = {}
        t_baslangic = time.time()

        blok_metinleri = [" ".join((b.get("text") or "").split()) for b in bloklar]
        cumle_gruplari = list(self._cumle_gruplari(bloklar))
        satirlar = [" ".join(blok_metinleri[i] for i in grup) for grup in cumle_gruplari]
        self.cumle_gruplari = cumle_gruplari

        ceviriler: dict[int, str] = {}
        cevrilecek: list[int] = []
        for i, satir in enumerate(satirlar):
            if cevrilmeye_deger(satir):
                cevrilecek.append(i)
            else:
                ceviriler[i] = satir

        gruplar = list(self._satir_gruplari(cevrilecek, satirlar))
        if not gruplar:
            self.cumle_ceviriler = dict(ceviriler)
            return self._bloklara_dagit(cumle_gruplari, ceviriler, blok_metinleri, kaynak_dil)

        if self._gemini.devre_disi and self._deepl.devre_disi:
            self._log("   ⚠️ Gemini/DeepL anahtarı girilmedi — çeviri Google Translate "
                      "ile yapılacak. Google argoyu sözlük anlamıyla çeviriyor ve küfrü "
                      "yumuşatıyor; gerçek bir dosyada ölçüldü: polla → «horoz», "
                      "nabos → «şalgam», emir kipi Chupa → mastar «Emmek». Sert ya da "
                      "argo diyalog için Temel sekmesine ücretsiz bir Gemini anahtarı "
                      "girin (aistudio.google.com).")

        if not self._gemini.devre_disi:
            self._log(f"   🤖 Gemini ile çevriliyor ({len(cevrilecek)} cümle) — "
                      f"argo ve küfür yumuşatılmadan aktarılacak…")
            try:
                gelen = self._gemini.cevir_paket(cevrilecek, satirlar)
            except KullaniciIptali:
                raise
            except Exception as e:
                self.son_hata = str(e)
                gelen = {}
            ceviriler.update(gelen)
            if gelen:
                self.motor_sayaci["Gemini"] = len(gelen)
            self._log(f"   🤖 Gemini: {len(gelen)}/{len(cevrilecek)} cümle çevrildi.")
            kalan_idx = [i for i in cevrilecek if i not in ceviriler]
            if not kalan_idx:
                self.cumle_ceviriler = dict(ceviriler)
                return self._bloklara_dagit(cumle_gruplari, ceviriler,
                                            blok_metinleri, kaynak_dil)
            gruplar = list(self._satir_gruplari(kalan_idx, satirlar))

        self._log(f"   {len(bloklar)} satır → {len(satirlar)} cümle; "
                  f"{sum(len(g) for g in gruplar)} cümle {len(gruplar)} pakette "
                  f"gönderilecek ({ISCI_SAYISI} eşzamanlı istek).")

        _o = len(ceviriler)
        self._paketleri_gonder(gruplar, satirlar, kaynak_dil, ceviriler, cevrilecek, t_baslangic)
        self._onarim_turlari(satirlar, kaynak_dil, ceviriler, cevrilecek)
        _ad = "Google Translate" if self._deepl.devre_disi else "DeepL/Google"
        self.motor_sayaci[_ad] = self.motor_sayaci.get(_ad, 0) + len(ceviriler) - _o

        kalan = [i for i in cevrilecek if i not in ceviriler]
        if kalan and not self._iptal():
            _o = len(ceviriler)
            self._yedek_yoldan_cevir(kalan, satirlar, kaynak_dil, ceviriler)
            self.motor_sayaci["Google (yedek)"] = len(ceviriler) - _o

        kalan = [i for i in cevrilecek if i not in ceviriler]
        if kalan and not self._iptal():
            _o = len(ceviriler)
            self._mymemoryden_cevir(kalan, satirlar, kaynak_dil, ceviriler)
            self.motor_sayaci["MyMemory"] = len(ceviriler) - _o

        kalan = [i for i in cevrilecek if i not in ceviriler]
        if kalan and not self._iptal():
            _o = len(ceviriler)
            self._yerelden_cevir(kalan, satirlar, kaynak_dil, ceviriler)
            self.motor_sayaci["Yerel model"] = len(ceviriler) - _o

        if not self._cevirici.limit_asildi and self._sansursuz and not self._iptal():
            try:
                self._sansuru_onar(ceviriler, satirlar, kaynak_dil)
            except KullaniciIptali:
                pass

        basarili = sum(1 for i in cevrilecek if i in ceviriler)
        if self._cevirici.limit_asildi:
            self._log("   🚫 Google'ın birincil çeviri uç noktası bu IP'ye istek limiti uyguladı "
                      "(HTTP 429). Genellikle 30-60 dakika içinde açılıyor.")
        self._log(f"   📊 {basarili}/{len(cevrilecek)} cümle çevrildi "
                  f"({self._cevirici.istek_sayisi} istek, {sure_metni(time.time() - t_baslangic)}).")
        dokum = " · ".join(f"{ad}: {sayi}"
                                for ad, sayi in self.motor_sayaci.items() if sayi > 0)
        if dokum:
            self._log(f"   🔧 Çeviriyi yapan motorlar → {dokum}")

        self.cumle_ceviriler = dict(ceviriler)
        return self._bloklara_dagit(cumle_gruplari, ceviriler, blok_metinleri, kaynak_dil)

    def _paketleri_gonder(self, gruplar, satirlar, kaynak_dil, ceviriler,
                          cevrilecek, t_baslangic) -> None:
        def grup_isi(grup):
            yerel: dict[int, str] = {}
            self._grup_cevir(grup, satirlar, kaynak_dil, yerel)
            return yerel

        tamamlanan = 0
        isler = []
        havuz = ThreadPoolExecutor(max_workers=ISCI_SAYISI)
        try:
            isler = [havuz.submit(grup_isi, g) for g in gruplar]
            for bitmis in as_completed(isler):
                try:
                    ceviriler.update(bitmis.result())
                except KullaniciIptali:
                    pass
                except Exception as e:
                    self.son_hata = str(e)
                    self._log(f"   ⚠️ Bir çeviri paketi hata verdi ({e}); onarım turunda tekrar denenecek.")

                tamamlanan += 1
                oran = tamamlanan / len(gruplar)
                cevrilen = sum(1 for i in cevrilecek if i in ceviriler)
                self._ilerleme(oran, f"Çeviri: %{oran * 100:.0f} ({cevrilen}/{len(cevrilecek)} cümle)")
                if tamamlanan % 5 == 0 or tamamlanan == len(gruplar):
                    self._log(f"   🌐 Çeviri: %{oran * 100:.0f}  ({tamamlanan}/{len(gruplar)} paket, "
                              f"{cevrilen}/{len(cevrilecek)} cümle, "
                              f"{sure_metni(time.time() - t_baslangic)})")

                if self._iptal():
                    for bekleyen in isler:
                        bekleyen.cancel()
                    break
        finally:
            havuz.shutdown(wait=True)

        # İptalden önce biten ama okunmamış işlerin sonucu da kaybolmasın.
        for bekleyen in isler:
            if bekleyen.done() and not bekleyen.cancelled():
                try:
                    ceviriler.update(bekleyen.result())
                except Exception:
                    pass

    def _onarim_turlari(self, satirlar, kaynak_dil, ceviriler, cevrilecek) -> None:
        for tur in range(1, ONARIM_TURU + 1):
            if self._iptal():
                break
            eksik = [i for i in cevrilecek if i not in ceviriler]
            if not eksik:
                break

            if self._cevirici.limit_asildi:
                self._log("   ⏭️ Birincil uç nokta istek limitinde; onarım turları atlanıyor, "
                          "kalan satırlar yedek yoldan çevrilecek.")
                break

            self._log(f"   🔁 Onarım turu {tur}/{ONARIM_TURU}: {len(eksik)} satır tekrar deneniyor…")
            try:
                self._cevirici.bekle(min(3.0 * tur, 12.0))
            except KullaniciIptali:
                break

            kucuk_gruplar = [eksik[j:j + 10] for j in range(0, len(eksik), 10)]

            def onarim_isi(kucuk):
                yerel: dict[int, str] = {}
                self._grup_cevir(kucuk, satirlar, kaynak_dil, yerel)
                return yerel

            tamam = 0
            isler = []
            havuz = ThreadPoolExecutor(max_workers=ISCI_SAYISI)
            try:
                isler = [havuz.submit(onarim_isi, k) for k in kucuk_gruplar]
                for bitmis in as_completed(isler):
                    try:
                        ceviriler.update(bitmis.result())
                    except KullaniciIptali:
                        pass
                    except Exception as e:
                        self.son_hata = str(e)
                    tamam += 1
                    oran = tamam / len(kucuk_gruplar)
                    self._ilerleme(oran, f"Çeviri onarımı {tur}: %{oran * 100:.0f} "
                                         f"({tamam}/{len(kucuk_gruplar)} grup)")
                    if self._iptal():
                        for bekleyen in isler:
                            bekleyen.cancel()
                        break
            finally:
                havuz.shutdown(wait=True)

            for bekleyen in isler:
                if bekleyen.done() and not bekleyen.cancelled():
                    try:
                        ceviriler.update(bekleyen.result())
                    except Exception:
                        pass

    def _yedek_yoldan_cevir(self, eksik, satirlar, kaynak_dil, ceviriler) -> None:
        self._log(f"   🛟 Yedek yol: {len(eksik)} satır tek tek çevriliyor "
                  f"({YEDEK_ISCI} eşzamanlı istek)…")
        t_yedek = time.time()
        kilit = threading.Lock()
        durum = {"ardisik_hata": 0, "pes": False}
        verimsiz = 0

        for tur in range(1, YEDEK_TURU + 1):
            bu_tur = [i for i in eksik if i not in ceviriler]
            if not bu_tur or durum["pes"] or self._iptal():
                break
            if tur > 1:
                self._log(f"   🔁 Yedek yol turu {tur}/{YEDEK_TURU}: "
                          f"{len(bu_tur)} satır tekrar deneniyor…")
                try:
                    self._cevirici.bekle(YEDEK_BEKLEME * (tur - 1))
                except KullaniciIptali:
                    break
                with kilit:
                    durum["ardisik_hata"] = 0

            onceki = sum(1 for i in eksik if i in ceviriler)
            self._yedek_tek_gecis(bu_tur, satirlar, kaynak_dil, ceviriler, kilit, durum, t_yedek)

            if sum(1 for i in eksik if i in ceviriler) == onceki:
                verimsiz += 1
                if verimsiz >= 2:
                    self._log("   ⏭️ Yedek yol arka arkaya iki turda hiçbir satır çeviremedi; "
                              "kalan turlar atlanıyor.")
                    break
            else:
                verimsiz = 0

        if durum["pes"]:
            self._log(f"   ⚠️ Yedek yol da arka arkaya {LIMIT_ESIGI} kez başarısız oldu; "
                      f"çeviri burada bırakıldı.")
        kalan_sayi = sum(1 for i in eksik if i not in ceviriler)
        if kalan_sayi:
            self._log(f"   ⚠️ {kalan_sayi} satır yedek yoldan da çevrilemedi; "
                      f"o satırlarda orijinal metin kalacak.")

    def _yedek_tek_gecis(self, eksik, satirlar, kaynak_dil, ceviriler, kilit, durum, t_yedek) -> None:
        def bir_satir(i):
            if self._iptal() or durum["pes"]:
                return i, None
            try:
                ceviri = self._cevirici.cevir_yedek(satirlar[i], kaynak_dil)
            except Exception:
                ceviri = None
            with kilit:
                if ceviri:
                    durum["ardisik_hata"] = 0
                else:
                    durum["ardisik_hata"] += 1
                    if durum["ardisik_hata"] >= LIMIT_ESIGI:
                        durum["pes"] = True
            return i, ceviri

        tamam = 0
        isler = []
        havuz = ThreadPoolExecutor(max_workers=YEDEK_ISCI)
        try:
            isler = [havuz.submit(bir_satir, i) for i in eksik]
            for bitmis in as_completed(isler):
                try:
                    indeks, ceviri = bitmis.result()
                    if ceviri:
                        ceviriler[indeks] = ceviri
                except Exception as e:
                    self.son_hata = str(e)
                tamam += 1
                oran = tamam / len(eksik)
                self._ilerleme(oran, f"Yedek çeviri: %{oran * 100:.0f} ({tamam}/{len(eksik)} satır)")
                if tamam % 50 == 0 or tamam == len(eksik):
                    self._log(f"   🛟 Yedek yol: {tamam}/{len(eksik)} satır "
                              f"({sure_metni(time.time() - t_yedek)})")
                if self._iptal() or durum["pes"]:
                    for bekleyen in isler:
                        bekleyen.cancel()
                    break
        finally:
            havuz.shutdown(wait=True)

        for bekleyen in isler:
            if bekleyen.done() and not bekleyen.cancelled():
                try:
                    indeks, ceviri = bekleyen.result()
                    if ceviri:
                        ceviriler[indeks] = ceviri
                except Exception:
                    pass

    def _mymemoryden_cevir(self, eksik, satirlar, kaynak_dil, ceviriler) -> None:
        """Google'ın iki yolu da tükendiğinde MyMemory'yi dener."""
        if self._mymemory is None:
            self._mymemory = MyMemoryCevirici(log=self._log, iptal=self._iptal)
        if not self._mymemory.kullanilabilir(kaynak_dil):
            self._log(f"   ℹ️ MyMemory «{kaynak_dil}» dilini tanımıyor; bu basamak atlanıyor.")
            return

        self._log(f"   🔁 MyMemory: {len(eksik)} satır deneniyor "
                  f"(anahtarsız, günlük kotalı)…")
        t_baslangic = time.time()
        eklenen = 0
        havuz = ThreadPoolExecutor(max_workers=2)     # kota dar, nazik davran
        try:
            isler = {havuz.submit(self._mymemory.cevir, satirlar[i], kaynak_dil): i
                     for i in eksik}
            for bitmis in as_completed(isler):
                i = isler[bitmis]
                try:
                    sonuc = bitmis.result()
                except KullaniciIptali:
                    break
                except Exception as e:
                    self.son_hata = str(e)
                    continue
                if sonuc:
                    ceviriler[i] = sonuc
                    eklenen += 1
                if self._mymemory.kota_doldu or self._iptal():
                    break
        finally:
            havuz.shutdown(wait=False, cancel_futures=True)

        self._log(f"   🔁 MyMemory: {eklenen}/{len(eksik)} satır çevrildi "
                  f"({sure_metni(time.time() - t_baslangic)}).")

    def _yerelden_cevir(self, eksik, satirlar, kaynak_dil, ceviriler) -> None:
        if self._yerel is None:
            self._yerel = YerelCevirici(log=self._log, iptal=self._iptal)
        if not self._yerel.hazirla(kaynak_dil):
            return

        self._log(f"   💻 Yerel çeviri: {len(eksik)} satır makinede çevriliyor "
                  f"(internet gerekmiyor)…")
        t_yerel = time.time()
        try:
            sonuclar = self._yerel.cevir_liste(
                [satirlar[i] for i in eksik],
                ilerleme=lambda o: self._ilerleme(o, f"Yerel çeviri: %{o * 100:.0f}"))
        except KullaniciIptali:
            return
        except Exception as e:
            self.son_hata = str(e)
            self._log(f"   ⚠️ Yerel çeviri hata verdi ({e}); atlanıyor.")
            return

        eklenen = 0
        for i, ceviri in zip(eksik, sonuclar):
            if ceviri and ceviri.strip():
                ceviriler[i] = ceviri.strip()
                eklenen += 1
        self._log(f"   💻 Yerel çeviri: {eklenen}/{len(eksik)} satır çevrildi "
                  f"({sure_metni(time.time() - t_yerel)}).")

    def _bloklara_dagit(self, cumle_gruplari, cumle_ceviriler, blok_metinleri, kaynak_dil):
        """Cümle bazlı çevirileri tekrar altyazı bloklarına indirger."""
        blok_ceviriler: dict[int, str] = {}
        yedek_bloklar: list[int] = []

        for cumle_idx, grup in enumerate(cumle_gruplari):
            ceviri = cumle_ceviriler.get(cumle_idx)
            if ceviri is None:
                continue

            dagitim = self._ceviriyi_dagit(ceviri, [blok_metinleri[i] for i in grup])
            if dagitim is None:
                yedek_bloklar.extend(grup)
                continue

            for blok_idx, metin in zip(grup, dagitim):
                blok_ceviriler[blok_idx] = metin

        if yedek_bloklar and not self._iptal():
            self._log(f"   ↩️ {len(yedek_bloklar)} satır tek tek çevriliyor "
                      f"(cümle çevirisi bloklara bölünemedi).")
            for paket in self._satir_gruplari(yedek_bloklar, blok_metinleri):
                if self._iptal():
                    break
                try:
                    self._grup_cevir(paket, blok_metinleri, kaynak_dil, blok_ceviriler)
                except KullaniciIptali:
                    break
                except Exception as e:
                    self.son_hata = str(e)

            kalan = [i for i in yedek_bloklar if i not in blok_ceviriler]
            if kalan and not self._iptal():
                self._yedek_yoldan_cevir(kalan, blok_metinleri, kaynak_dil, blok_ceviriler)

            kalan = [i for i in yedek_bloklar if i not in blok_ceviriler]
            if kalan and not self._iptal():
                self._yerelden_cevir(kalan, blok_metinleri, kaynak_dil, blok_ceviriler)

        return blok_ceviriler

    def _sansuru_onar(self, ceviriler, satirlar, kaynak_dil) -> None:
        """Google'ın yıldızladığı satırları tekrar ister.

        Yalnızca kaynakta yıldız yokken çeviride belirenler şüphelidir; kaynağın
        kendisinde `***` varsa ona dokunulmaz.
        """
        supheli = [i for i, ceviri in ceviriler.items()
                   if SANSUR_DESENI.search(ceviri) and not SANSUR_DESENI.search(satirlar[i])]
        if not supheli:
            return

        self._log(f"   🔞 {len(supheli)} satırda yıldızlanmış çeviri bulundu, tekrar isteniyor…")
        duzelen = 0
        for i in supheli:
            if self._iptal():
                break
            yeni = self._tek_satir_cevir(satirlar[i], kaynak_dil, yedek_kullan=False)
            if yeni and not SANSUR_DESENI.search(yeni):
                ceviriler[i] = yeni
                duzelen += 1
        self._log(f"   🔞 {duzelen}/{len(supheli)} satırdaki sansür kaldırıldı.")


def google_destekliyor_mu(dil: str) -> bool:
    try:
        return bool(GoogleTranslator(source="auto", target="tr").is_language_supported(dil))
    except Exception:
        return False
