"""Çeviri: cümle gruplama, dağıtım ve motorların sırası (HTTP taklit edilir)."""

import json
import re

import pytest
import requests

from forge import translate as tr


# --- saf yardımcılar --------------------------------------------------------

def blok(metin, bas, son):
    return {"text": metin, "start": bas, "end": son}


def test_cevrilmeye_deger():
    assert tr.cevrilmeye_deger("Merhaba")
    assert not tr.cevrilmeye_deger("   ")
    assert not tr.cevrilmeye_deger("♪ ... ♪")


def test_cumle_gruplari_yarim_cumleyi_birlestirir():
    bloklar = [
        blok("Gelin bu gizemin", 0.0, 1.0),
        blok("peşine birlikte düşelim.", 1.1, 2.0),
        blok("♪♪", 2.1, 3.0),                     # çevrilmeye değmez, tek başına
        blok("Tamam", 3.1, 3.5),
        blok("sonra görüşürüz", 6.0, 7.0),        # 2 sn'den uzun boşluk
    ]
    assert list(tr.CeviriMotoru._cumle_gruplari(bloklar)) == [[0, 1], [2], [3], [4]]


def test_ceviriyi_dagit_uzunluk_oranina_gore():
    dagit = tr.CeviriMotoru._ceviriyi_dagit
    assert dagit("tek parça", ["one"]) == ["tek parça"]
    assert dagit("bir", ["a", "b"]) is None           # kelime blok sayısından az
    sonuc = dagit("bir iki üç dört", ["uzun bir blok metni", "kısa"])
    assert len(sonuc) == 2 and all(sonuc)
    assert " ".join(sonuc) == "bir iki üç dört"


def test_gemini_yaniti_numaralarla_hizalanir():
    yanit = "0|Merhaba\n2. | Nasılsın\n7|istenmeyen\nbozuk satır\n1|"
    assert tr.GeminiCevirici._yaniti_coz(yanit, [0, 1, 2]) == {0: "Merhaba", 2: "Nasılsın"}


# --- motor sırası -----------------------------------------------------------

class SahteYanit:
    def __init__(self, kod, veri=None):
        self.status_code = kod
        self._veri = veri if veri is not None else {}
        self.text = json.dumps(self._veri)

    def json(self):
        return self._veri


GEMINI_GUNLUK_KOTA = SahteYanit(429, {"error": {"details": [
    {"violations": [{"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier"}]},
    {"retryDelay": "8s"},
]}})

SOZLUK = {
    "Hello there.": "Merhaba oradaki.",
    "How are you?": "Nasılsın?",
    "Fine thanks.": "İyiyim sağ ol.",
}


class SahteAg:
    """Motorların HTTP çağrılarını yakalar; hangi motorun ne zaman çağrıldığını tutar."""

    def __init__(self, gemini=None, deepl=None, google=True):
        self.gemini = gemini        # None: 200 ile yanıt ver; SahteYanit: onu döndür
        self.deepl = deepl
        self.google = google
        self.cagrilar: list[str] = []
        self.gemini_modelleri: list[str] = []

    def post(self, url, params=None, json=None, data=None, headers=None, timeout=None):
        if "generativelanguage" in url:
            self.cagrilar.append("gemini")
            self.gemini_modelleri.append(url.split("models/")[1].split(":")[0])
            if self.gemini is not None:
                return self.gemini
            metin = json["contents"][0]["parts"][0]["text"]
            satirlar = re.findall(r"^(\d+)\|(.*)$", metin, re.MULTILINE)
            # Yalnızca ilk iki satırı çevir; üçüncüsü alttaki motorlara kalsın.
            cikti = "\n".join(f"{i}|{SOZLUK[m]}" for i, m in satirlar[:2])
            return SahteYanit(200, {"candidates": [{"content": {"parts": [{"text": cikti}]}}]})
        if "deepl.com" in url:
            self.cagrilar.append("deepl")
            if self.deepl is not None:
                return self.deepl
            return SahteYanit(200, {"translations": [{"text": SOZLUK[t]} for t in json["text"]]})
        if "translate_a" in url:
            self.cagrilar.append("google")
            if not self.google:
                return SahteYanit(503)
            ceviri = "\n".join(SOZLUK[s] for s in data["q"].split("\n"))
            return SahteYanit(200, {"sentences": [{"trans": ceviri}]})
        raise AssertionError(f"beklenmeyen istek: {url}")


@pytest.fixture
def ag(monkeypatch):
    def kur(**kw):
        sahte = SahteAg(**kw)
        monkeypatch.setattr(requests.Session, "post",
                            lambda self, url, **k: sahte.post(url, **k))
        monkeypatch.setattr(requests, "post", lambda url, **k: sahte.post(url, **k))
        return sahte
    return kur


def motor(gemini="", deepl=""):
    kayit: list[str] = []
    m = tr.CeviriMotoru(log=kayit.append, ilerleme=lambda *a: None,
                        iptal=lambda: False, gemini_anahtari=gemini,
                        deepl_anahtari=deepl)
    return m, kayit


BLOKLAR = [
    blok("Hello there.", 0.0, 1.0),
    blok("How are you?", 1.2, 2.0),
    blok("Fine thanks.", 2.2, 3.0),
]
BEKLENEN = {0: "Merhaba oradaki.", 1: "Nasılsın?", 2: "İyiyim sağ ol."}


def test_gemini_kalanini_deeple_birakir_google_cagrilmaz(ag):
    sahte = ag()
    m, _ = motor(gemini="g-anahtar", deepl="d-anahtar:fx")
    assert m.cevir(BLOKLAR, "en") == BEKLENEN
    assert sahte.cagrilar == ["gemini", "deepl"]
    assert m.motor_sayaci == {"Gemini": 2, "DeepL": 1}


def test_gemini_hepsini_cevirirse_diger_motorlara_istek_gitmez(ag):
    sahte = ag()
    m, _ = motor(gemini="g-anahtar", deepl="d-anahtar:fx")
    # İki blokluk dosya: Gemini'nin ikisini de çevirmesi yeterli.
    assert m.cevir(BLOKLAR[:2], "en") == {0: BEKLENEN[0], 1: BEKLENEN[1]}
    assert sahte.cagrilar == ["gemini"]


def test_gunluk_kota_dolunca_modeller_sonra_deepl_sonra_google(ag):
    sahte = ag(gemini=GEMINI_GUNLUK_KOTA, deepl=SahteYanit(456))
    m, kayit = motor(gemini="g-anahtar", deepl="d-anahtar:fx")
    assert m.cevir(BLOKLAR, "en") == BEKLENEN

    # Kota model başına: üç model de sırayla denenir, beklemeden geçilir.
    assert sahte.gemini_modelleri == list(tr.GeminiCevirici.YEDEK_MODELLER)
    ilk_diger = next(i for i, c in enumerate(sahte.cagrilar) if c != "gemini")
    assert set(sahte.cagrilar[:ilk_diger]) == {"gemini"}
    assert sahte.cagrilar[ilk_diger] == "deepl"
    assert set(sahte.cagrilar[ilk_diger + 1:]) == {"google"}

    assert m.motor_sayaci == {"Google Translate": 3}
    assert m._gemini.kota_doldu
    assert m._deepl.kota_doldu
    assert any("kotası tüm modellerde" in s for s in kayit)


def test_anahtar_yoksa_dogrudan_google_ve_uyari(ag):
    sahte = ag()
    m, kayit = motor()
    assert m.cevir(BLOKLAR, "en") == BEKLENEN
    assert set(sahte.cagrilar) == {"google"}
    assert any("Gemini anahtarı girilmedi" in s for s in kayit)
