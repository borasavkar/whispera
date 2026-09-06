"""Ayarların tanımı, doğrulanması ve diske yazılması.

`Ayarlar` sınıfı whisper CLI'ının bütün parametrelerini birebir taşır; ek olarak
uygulamaya özgü (çeviri, kuyruk, ffmpeg) alanları da tutar. Arayüz yalnızca bu
nesneyi doldurur, motor tarafı da yalnızca bunu okur.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass, field, fields
from typing import Any

# --------------------------------------------------------------------------
# Sabitler / seçenek listeleri
# --------------------------------------------------------------------------

MODELLER = (
    "tiny", "tiny.en",
    "base", "base.en",
    "small", "small.en",
    "medium", "medium.en",
    "large-v1", "large-v2", "large-v3",
    "turbo", "large-v3-turbo",
)

CIKTI_FORMATLARI = ("srt", "vtt", "txt", "tsv", "json", "jsonl", "all")

GOREVLER = {
    "transcribe": "Aynı dilde yaz (transcribe)",
    "translate": "İngilizceye çevir (translate)",
}

# Sık kullanılanlar dil listesinin en üstünde dursun.
ONE_CIKAN_DILLER = ("tr", "en", "de", "fr", "es", "it", "ru", "ar", "ja", "ko", "zh")

DIL_ADLARI = {
    "af": "Afrikaanca", "am": "Amharca", "ar": "Arapça", "as": "Assamca",
    "az": "Azerice", "ba": "Başkurtça", "be": "Belarusça", "bg": "Bulgarca",
    "bn": "Bengalce", "bo": "Tibetçe", "br": "Bretonca", "bs": "Boşnakça",
    "ca": "Katalanca", "cs": "Çekçe", "cy": "Galce", "da": "Danca",
    "de": "Almanca", "el": "Yunanca", "en": "İngilizce", "es": "İspanyolca",
    "et": "Estonca", "eu": "Baskça", "fa": "Farsça", "fi": "Fince",
    "fo": "Faroece", "fr": "Fransızca", "gl": "Galiçyaca", "gu": "Gucaratça",
    "ha": "Hausaca", "haw": "Hawaiice", "he": "İbranice", "hi": "Hintçe",
    "hr": "Hırvatça", "ht": "Haiti Kreolü", "hu": "Macarca", "hy": "Ermenice",
    "id": "Endonezce", "is": "İzlandaca", "it": "İtalyanca", "ja": "Japonca",
    "jw": "Cavaca", "ka": "Gürcüce", "kk": "Kazakça", "km": "Khmerce",
    "kn": "Kannada", "ko": "Korece", "la": "Latince", "lb": "Lüksemburgca",
    "ln": "Lingala", "lo": "Laoca", "lt": "Litvanca", "lv": "Letonca",
    "mg": "Malgaşça", "mi": "Maorice", "mk": "Makedonca", "ml": "Malayalam",
    "mn": "Moğolca", "mr": "Marathi", "ms": "Malayca", "mt": "Maltaca",
    "my": "Birmanca", "ne": "Nepalce", "nl": "Felemenkçe", "nn": "Nynorsk",
    "no": "Norveççe", "oc": "Oksitanca", "pa": "Pencapça", "pl": "Lehçe",
    "ps": "Peştuca", "pt": "Portekizce", "ro": "Rumence", "ru": "Rusça",
    "sa": "Sanskritçe", "sd": "Sindhi", "si": "Sinhalaca", "sk": "Slovakça",
    "sl": "Slovence", "sn": "Shona", "so": "Somalice", "sq": "Arnavutça",
    "sr": "Sırpça", "su": "Sundaca", "sv": "İsveççe", "sw": "Svahili",
    "ta": "Tamilce", "te": "Telugu", "tg": "Tacikçe", "th": "Tayca",
    "tk": "Türkmence", "tl": "Tagalogca", "tr": "Türkçe", "tt": "Tatarca",
    "uk": "Ukraynaca", "ur": "Urduca", "uz": "Özbekçe", "vi": "Vietnamca",
    "yi": "Yidiş", "yo": "Yorubaca", "yue": "Kantonca", "zh": "Çince",
}

MEDYA_UZANTILARI = (
    ".mp4", ".mkv", ".avi", ".mov", ".webm", ".flv", ".wmv", ".m4v",
    ".mpg", ".mpeg", ".ts", ".m2ts", ".3gp", ".ogv",
    ".mp3", ".wav", ".m4a", ".flac", ".aac", ".ogg", ".opus", ".wma", ".aiff",
)

# Sansürsüz mod için dile göre başlangıç istemi.
#
# DİKKAT: Bu metinler KISA ve üslup örneği niteliğinde olmalı, talimat
# olmamalı. Whisper `initial_prompt`'u transkriptin devamı gibi işler; uzun bir
# talimat cümlesi verildiğinde — özellikle sesin diliyle aynı dildeyse — model
# onu papağan gibi tekrarlayıp bütün dosyayı bu metinle doldurabiliyor.
# Deneyle görüldü: 3 cümlelik Almanca talimat + Almanca ses = 7 segmentin
# 7'si de istemin kendisi. Kısa hâliyle sorun çıkmıyor.
SANSURSUZ_ISTEMLER = {
    "tr": "Küfür ve argo aynen yazılır.",
    "en": "Profanity is written out in full.",
    "de": "Schimpfwörter werden ausgeschrieben.",
    "fr": "Les grossièretés sont écrites telles quelles.",
    "it": "Le volgarità sono scritte per intero.",
    "es": "Las palabrotas se escriben tal cual.",
    "ru": "Ругательства записываются полностью.",
    "pt": "Os palavrões são escritos por extenso.",
    "nl": "Scheldwoorden worden voluit geschreven.",
}

UYGULAMA_KLASORU = (
    os.path.dirname(sys.executable) if getattr(sys, "frozen", False)
    else os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)

AYAR_DOSYASI = os.path.join(UYGULAMA_KLASORU, "forge_settings.json")

# Uygulamanın yanındaki `models` klasörü — varsa modeller oradan okunur.
TASINABILIR_MODEL_KLASORU = os.path.join(UYGULAMA_KLASORU, "models")


def varsayilan_model_klasoru() -> str:
    """Ayarlarda elle bir yol verilmemişse kullanılacak model klasörü.

    Uygulamanın yanında bir `models` klasörü varsa taşınabilir kurulum
    demektir; yoksa whisper'ın kendi varsayılanı (~/.cache/whisper) kalır.
    """
    return TASINABILIR_MODEL_KLASORU if os.path.isdir(TASINABILIR_MODEL_KLASORU) else ""


# --------------------------------------------------------------------------
# Ayar nesnesi
# --------------------------------------------------------------------------

@dataclass
class Ayarlar:
    # --- Temel ---
    model: str = "large-v3-turbo"
    dil: str = "auto"                    # "auto" ya da iki harfli kod
    gorev: str = "transcribe"            # transcribe | translate
    cikti_formati: str = "srt"
    cikti_klasoru: str = ""              # boş = kaynak dosyanın yanına
    cihaz: str = "auto"                  # auto | cuda | cpu
    model_klasoru: str = ""              # boş = ~/.cache/whisper

    # --- Kod çözme (decoding) ---
    temperature: float = 0.0
    temperature_increment_on_fallback: float = 0.2
    best_of: int = 5
    beam_size: int = 5
    # Whisper'ın varsayılanı None (=1.0, "ilk gelen alır"). 2.0 yapılmasının
    # sebebi ölçüm: beam araması `beam_size` kadar tamamlanmış aday bulur
    # bulmaz duruyor (Kasai vd. 2022, arXiv:2204.05424) ve bu, konuşması
    # seyrek dosyalarda dejenere bir tekrar hipotezine kilitlenmeye yol
    # açıyor. Gerçek bir dosyada ölçüldü — ikinci yarıdaki benzersiz metin:
    #   ışın 5,  sabır yok : 3-10     ← dosyanın yarısı «Hayır hayır.» tekrarı
    #   ışın 12, sabır yok : 32-40    (69-74 sn)
    #   ışın 5,  sabır 2.0 : 33-34    (38-46 sn)  ← aynı kalite, %40 daha hızlı
    patience: float | None = 2.0
    length_penalty: float | None = None
    suppress_tokens: str = "-1"
    initial_prompt: str = ""
    carry_initial_prompt: bool = False
    condition_on_previous_text: bool = True
    fp16: bool = True

    # --- Eşikler ---
    compression_ratio_threshold: float | None = 2.4
    logprob_threshold: float | None = -1.0
    # Whisper'ın kendi varsayılanı 0.6; burada bilerek düşürüldü. Bora'nın
    # arşivi konuşmanın seyrek, müziğin baskın olduğu filmlerden oluşuyor ve
    # 0.6 ile model sessiz açılışlarda cümle uyduruyor. Bir filmde ölçüldü:
    # 0.60 → 3 uydurma blok, 0.40 → 3, 0.30 → 2.
    no_speech_threshold: float | None = 0.3
    hallucination_silence_threshold: float | None = None

    # --- Kelime zaman damgaları ---
    # Varsayılan açık: altyazı için asıl fark yaratan ayar bu. Segment
    # zamanlarını kelime hizasına çekiyor ve akıllı bölümlemeyi mümkün kılıyor.
    word_timestamps: bool = True
    prepend_punctuations: str = "\"'\u201c\u00bf([{-"
    append_punctuations: str = "\"'.\u3002,\uff0c!\uff01?\uff1f:\uff1a\u201d)]}\u3001"
    highlight_words: bool = False
    max_line_width: int | None = None
    max_line_count: int | None = None
    max_words_per_line: int | None = None

    # --- Diğer ---
    threads: int = 0
    clip_timestamps: str = "0"

    # --- Altyazı bölümleme (kelime zaman damgası gerektirir) ---
    akilli_bolumleme: bool = True
    blok_max_karakter: int = 84          # iki satır x 42
    blok_max_sure: float = 6.0           # saniye
    blok_max_bosluk: float = 0.7         # bu kadar sessizlik bloğu böler

    # --- Uygulamaya özgü ---
    ffmpeg_yolu: str = ""
    turkce_ceviri: bool = True
    deepl_anahtari: str = ""             # boşsa DeepL hiç denenmez
    gemini_anahtari: str = ""            # boşsa Gemini hiç denenmez
    gemini_modeli: str = "gemini-3.6-flash"
    sansursuz: bool = True
    dil_oylamasi: bool = True
    dil_oylama_pencere: int = 8
    tekrar_filtresi: bool = True
    tekrar_limiti: int = 4
    satir_max_karakter: int = 42         # çeviri altyazısında satır kırma genişliği

    # --- Pencere durumu ---
    pencere: dict = field(default_factory=lambda: {"x": 120, "y": 80, "g": 1240, "h": 860})
    son_klasor: str = ""

    # ----------------------------------------------------------------------

    @classmethod
    def yukle(cls, yol: str = AYAR_DOSYASI) -> "Ayarlar":
        try:
            with open(yol, "r", encoding="utf-8") as f:
                ham = json.load(f)
        except Exception:
            return cls()
        if not isinstance(ham, dict):
            return cls()

        gecerli = {f.name for f in fields(cls)}
        temiz = {k: v for k, v in ham.items() if k in gecerli}
        try:
            nesne = cls(**temiz)
        except Exception:
            # Tek bir bozuk alan bütün ayarları çöpe atmasın.
            nesne = cls()
            for k, v in temiz.items():
                try:
                    setattr(nesne, k, v)
                except Exception:
                    pass
        return nesne._gocur()

    # Type annotation YOK: dataclass alanı olmasın diye.
    EMEKLI_GEMINI_MODELLERI = frozenset({
        "gemini-2.0-flash", "gemini-2.0-flash-lite", "gemini-1.5-flash",
        "gemini-1.5-pro", "gemini-pro",
    })

    def _gocur(self) -> "Ayarlar":
        """Eski ayar dosyalarındaki geçersiz değerleri günceller.

        Google emekliye ayırdığı model adlarına 404 döndürüyor. Çalışma anında
        otomatik geçiş var, ama kayıtlı ad düzeltilmezse her koşuda bir istek
        boşa gidiyor.
        """
        if self.gemini_modeli in self.EMEKLI_GEMINI_MODELLERI:
            self.gemini_modeli = type(self).gemini_modeli
        return self

    def kaydet(self, yol: str = AYAR_DOSYASI) -> None:
        gecici = yol + ".yeni"
        try:
            with open(gecici, "w", encoding="utf-8") as f:
                json.dump(asdict(self), f, ensure_ascii=False, indent=2)
            os.replace(gecici, yol)
        except Exception:
            try:
                os.remove(gecici)
            except Exception:
                pass

    # ----------------------------------------------------------------------

    def sicaklik_dizisi(self) -> tuple[float, ...]:
        """whisper CLI'ındaki temperature + fallback mantığının aynısı."""
        taban = float(self.temperature)
        artis = float(self.temperature_increment_on_fallback or 0.0)
        if artis <= 0:
            return (taban,)
        degerler: list[float] = []
        deger = taban
        while deger < 1.0 + 1e-6:
            degerler.append(round(deger, 6))
            deger += artis
        return tuple(degerler) or (taban,)

    def bolumleme_secenekleri(self) -> dict[str, Any]:
        return {
            "max_karakter": int(self.blok_max_karakter),
            "max_sure": float(self.blok_max_sure),
            "max_bosluk": float(self.blok_max_bosluk),
            "satir_genisligi": int(self.satir_max_karakter),
            "max_satir": int(self.max_line_count or 2),
        }

    def akilli_bolumleme_acik(self) -> bool:
        """Kendi bölümleyicimiz devrede mi?

        `highlight_words` kelime kelime altı çizili çıktı istiyor; o zaman
        blokları biz yeniden kurarsak kelime zamanları kaybolur, bu yüzden
        whisper'ın kendi yazıcısına bırakırız.
        """
        return (self.akilli_bolumleme and self.word_timestamps
                and not self.highlight_words)

    def yazici_secenekleri(self) -> dict[str, Any]:
        return {
            "highlight_words": bool(self.highlight_words),
            "max_line_width": self.max_line_width,
            "max_line_count": self.max_line_count,
            "max_words_per_line": self.max_words_per_line,
        }

    def transcribe_argumanlari(self, dil: str | None, istem: str | None) -> dict[str, Any]:
        """`model.transcribe(...)` çağrısına doğrudan verilebilecek sözlük."""
        return {
            "language": dil,
            "task": self.gorev,
            "temperature": self.sicaklik_dizisi(),
            "best_of": int(self.best_of),
            "beam_size": int(self.beam_size),
            "patience": self.patience,
            "length_penalty": self.length_penalty,
            "suppress_tokens": self.suppress_tokens,
            "initial_prompt": istem or None,
            "carry_initial_prompt": bool(self.carry_initial_prompt),
            "condition_on_previous_text": bool(self.condition_on_previous_text),
            "compression_ratio_threshold": self.compression_ratio_threshold,
            "logprob_threshold": self.logprob_threshold,
            "no_speech_threshold": self.no_speech_threshold,
            "word_timestamps": bool(self.word_timestamps),
            "prepend_punctuations": self.prepend_punctuations,
            "append_punctuations": self.append_punctuations,
            "clip_timestamps": self.clip_timestamps or "0",
            "hallucination_silence_threshold": self.hallucination_silence_threshold,
        }

    def dogrula(self) -> list[str]:
        """Kullanıcıya gösterilecek uyarı listesi döndürür (boşsa sorun yok)."""
        uyarilar: list[str] = []

        gerektirenler = []
        if self.highlight_words:
            gerektirenler.append("Kelimeleri vurgula")
        if self.max_line_width is not None:
            gerektirenler.append("Satır genişliği")
        if self.max_line_count is not None:
            gerektirenler.append("Satır sayısı")
        if self.max_words_per_line is not None:
            gerektirenler.append("Satır başına kelime")
        if self.hallucination_silence_threshold is not None:
            gerektirenler.append("Halüsinasyon sessizlik eşiği")
        if gerektirenler and not self.word_timestamps:
            uyarilar.append(
                f"{', '.join(gerektirenler)} kelime zaman damgası gerektirir — "
                "«Kelime zaman damgaları» otomatik açılacak.")

        if self.beam_size and self.temperature > 0:
            uyarilar.append(
                "Işın araması (beam_size) yalnızca sıcaklık 0 iken çalışır; "
                "sıcaklık 0'dan büyük olduğu için best_of ile örnekleme yapılacak.")
        if self.model.endswith(".en") and self.dil not in ("auto", "en"):
            uyarilar.append(
                f"«{self.model}» yalnızca İngilizce bir modeldir; seçilen dil yok sayılabilir.")
        if self.cihaz == "cpu" and self.fp16:
            uyarilar.append("CPU üzerinde fp16 desteklenmez; otomatik olarak fp32'ye düşülecek.")
        if self.gorev == "translate" and self.turkce_ceviri:
            uyarilar.append(
                "Görev «İngilizceye çevir» olduğu için Türkçe altyazı İngilizce metinden üretilecek.")
        return uyarilar

    def duzelt(self) -> None:
        """dogrula()'da anlatılan otomatik düzeltmeleri uygular."""
        if (self.highlight_words
                or self.max_line_width is not None
                or self.max_line_count is not None
                or self.max_words_per_line is not None
                or self.hallucination_silence_threshold is not None):
            self.word_timestamps = True

    def etkin_istem(self, dil: str | None) -> str | None:
        """Kullanıcının kendi istemi varsa o kazanır; yoksa sansürsüz kalıbı."""
        elle = (self.initial_prompt or "").strip()
        if elle:
            return elle
        if not self.sansursuz:
            return None
        return SANSURSUZ_ISTEMLER.get((dil or "").lower())


def dil_etiketi(kod: str) -> str:
    if kod == "auto":
        return "Otomatik algıla"
    ad = DIL_ADLARI.get(kod)
    return f"{ad} ({kod})" if ad else kod


def dil_secenekleri() -> list[tuple[str, str]]:
    """(kod, etiket) çiftleri — sık kullanılanlar en üstte, sonra alfabetik."""
    secenekler = [("auto", dil_etiketi("auto"))]
    secenekler += [(k, dil_etiketi(k)) for k in ONE_CIKAN_DILLER]
    kalan = sorted(
        (k for k in DIL_ADLARI if k not in ONE_CIKAN_DILLER),
        key=lambda k: DIL_ADLARI[k].casefold(),
    )
    secenekler += [(k, dil_etiketi(k)) for k in kalan]
    return secenekler
