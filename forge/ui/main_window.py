"""Ana pencere: düzen, ayar formları ve işçi iş parçacığıyla bağlantı."""

from __future__ import annotations

import os
import subprocess
import sys

from PySide6.QtCore import Qt, QThread, QTimer, Signal
from PySide6.QtGui import QAction, QIcon, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .. import media
from ..config import (
    CIKTI_FORMATLARI,
    GOREVLER,
    MEDYA_UZANTILARI,
    MODELLER,
    Ayarlar,
    dil_secenekleri,
    varsayilan_model_klasoru,
)
from ..version import SURUM, UYGULAMA_ADI
from ..whisper_engine import WhisperMotoru
from ..worker import Isci
from .theme import stil_sayfasi
from .widgets import (
    AkisGorunumu,
    Ayirac,
    BirakmaAlani,
    BolumBaslik,
    GunlukGorunumu,
    IstegeBagliSayi,
    IstegeBagliTamSayi,
    Kart,
    Onay,
    Rozet,
    SecimKutusu,
)

DURUM_SIMGELERI = {
    "bekliyor": "○",
    "isleniyor": "▶",
    "bitti": "✓",
    "hata": "✕",
}

# İptal sinyali ancak iki kod çözme penceresi arasında görülüyor; large-v3 gibi
# büyük modellerde tek pencere on saniyeyi bulabiliyor.
KAPANIS_BEKLEME_MS = 15000


# --------------------------------------------------------------------------
# Yardımcı iş parçacıkları
# --------------------------------------------------------------------------

class DonanimSorgusu(QThread):
    """torch'u arka planda yükleyip donanım rozetini doldurur.

    İlk `import torch` saniyeler sürüyor; pencerenin açılışını bekletmemek için
    ayrı bir iş parçacığında yapılır.
    """

    bitti = Signal(str, bool)

    def __init__(self, motor: WhisperMotoru, parent=None):
        super().__init__(parent)
        self._motor = motor

    def run(self):
        try:
            bilgi = self._motor.donanim_bilgisi()
            gpu = not bilgi.startswith("CPU")
        except Exception as e:
            bilgi, gpu = f"Donanım okunamadı ({e.__class__.__name__})", False
        self.bitti.emit(bilgi, gpu)


class FFmpegSorgusu(QThread):
    bitti = Signal(str, bool)

    def __init__(self, elle_yol: str, parent=None):
        super().__init__(parent)
        self._elle = elle_yol

    def run(self):
        yol = media.ffmpeg_coz(self._elle)
        if not yol:
            self.bitti.emit("FFmpeg bulunamadı", False)
            return
        surum = media.ffmpeg_surumu(yol)
        if surum is None:
            self.bitti.emit("FFmpeg çalıştırılamadı", False)
            return
        self.bitti.emit(f"FFmpeg {media.ffmpeg_kisa_surum(surum)}", True)


# --------------------------------------------------------------------------
# Form yardımcıları
# --------------------------------------------------------------------------

def alan(etiket: str, widget: QWidget, yardim: str = "") -> QWidget:
    """Etiket + denetim + (varsa) küçük açıklamadan oluşan dikey alan."""
    kap = QWidget()
    duzen = QVBoxLayout(kap)
    duzen.setContentsMargins(0, 0, 0, 0)
    duzen.setSpacing(4)

    baslik = QLabel(etiket)
    baslik.setObjectName("formEtiket")
    duzen.addWidget(baslik)
    duzen.addWidget(widget)

    if yardim:
        aciklama = QLabel(yardim)
        aciklama.setObjectName("yardimMetni")
        aciklama.setWordWrap(True)
        duzen.addWidget(aciklama)
        widget.setToolTip(yardim)
    return kap


def ikili(sol: QWidget, sag: QWidget) -> QWidget:
    kap = QWidget()
    duzen = QHBoxLayout(kap)
    duzen.setContentsMargins(0, 0, 0, 0)
    duzen.setSpacing(10)
    duzen.addWidget(sol, 1)
    duzen.addWidget(sag, 1)
    return kap


def kaydirilabilir(icerik: QWidget) -> QScrollArea:
    alan_ = QScrollArea()
    alan_.setWidget(icerik)
    alan_.setWidgetResizable(True)
    alan_.setFrameShape(QFrame.NoFrame)
    alan_.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    return alan_


# --------------------------------------------------------------------------
# Ana pencere
# --------------------------------------------------------------------------

class AnaPencere(QMainWindow):

    def __init__(self):
        super().__init__()
        self.ayarlar = Ayarlar.yukle()
        self.motor = WhisperMotoru(log=lambda mesaj: self._gunluk_ekle(mesaj))
        self.isci: Isci | None = None
        self._sorgular: list[QThread] = []
        self._son_ciktilar: list[str] = []
        self._aktif_yol: str = ""
        self._iptal_edildi_mi = False

        self.setWindowTitle(f"{UYGULAMA_ADI} v{SURUM}")
        self.setMinimumSize(1040, 700)
        self._pencereyi_konumlandir()
        self._simgeyi_yukle()

        self._arayuzu_kur()
        self._ayarlari_widgetlara_yaz()
        self._kisayollari_kur()

        self._gunluk_ekle(f"🎬 {UYGULAMA_ADI} v{SURUM} — saf openai-whisper")
        self._gunluk_ekle("   Başlamak için dosya sürükleyin ya da bırakma alanına tıklayın.")
        self.akis.basliga_yaz("işlem başlayınca üretilen altyazılar buraya akacak")

        self._donanimi_sorgula()
        self._ffmpegi_sorgula()

    # ----------------------------------------------------------------------
    # Kurulum
    # ----------------------------------------------------------------------

    def _pencereyi_konumlandir(self) -> None:
        p = self.ayarlar.pencere or {}
        ekran = QApplication.primaryScreen().availableGeometry()
        g = min(int(p.get("g", 1240)), ekran.width())
        h = min(int(p.get("h", 860)), ekran.height())
        x = max(ekran.left(), min(int(p.get("x", 120)), ekran.right() - g))
        y = max(ekran.top(), min(int(p.get("y", 80)), ekran.bottom() - h))
        self.setGeometry(x, y, g, h)

    def _simgeyi_yukle(self) -> None:
        for ad in ("subs.ico", "icon.ico"):
            yol = os.path.join(os.path.dirname(os.path.dirname(
                os.path.dirname(os.path.abspath(__file__)))), ad)
            if os.path.isfile(yol):
                self.setWindowIcon(QIcon(yol))
                return

    def _arayuzu_kur(self) -> None:
        merkez = QWidget()
        duzen = QVBoxLayout(merkez)
        duzen.setContentsMargins(0, 0, 0, 0)
        duzen.setSpacing(0)

        duzen.addWidget(self._baslik_serit())

        govde = QWidget()
        govde_duzen = QHBoxLayout(govde)
        govde_duzen.setContentsMargins(16, 14, 16, 12)

        self.ayirici = QSplitter(Qt.Horizontal)
        self.ayirici.setChildrenCollapsible(False)
        self.ayirici.addWidget(self._sol_panel())
        self.ayirici.addWidget(self._sag_panel())
        self.ayirici.setStretchFactor(0, 0)
        self.ayirici.setStretchFactor(1, 1)
        self.ayirici.setSizes([470, 720])
        govde_duzen.addWidget(self.ayirici)

        duzen.addWidget(govde, 1)
        duzen.addWidget(self._alt_serit())
        self.setCentralWidget(merkez)

    # --- başlık ----------------------------------------------------------

    def _baslik_serit(self) -> QWidget:
        serit = QFrame()
        serit.setObjectName("baslikSerit")
        serit.setFixedHeight(60)

        duzen = QHBoxLayout(serit)
        duzen.setContentsMargins(20, 0, 20, 0)
        duzen.setSpacing(8)

        ad = QLabel("Subtitle")
        ad.setObjectName("baslikAd")
        forge = QLabel("Forge")
        forge.setObjectName("baslikAd")
        forge.setProperty("vurgulu", "true")

        # İki renkli tek kelime: aralarında boşluk olmasın diye kendi kabında.
        marka = QWidget()
        marka_duzen = QHBoxLayout(marka)
        marka_duzen.setContentsMargins(0, 0, 0, 0)
        marka_duzen.setSpacing(0)
        marka_duzen.addWidget(ad)
        marka_duzen.addWidget(forge)

        surum = QLabel(f"v{SURUM}")
        surum.setObjectName("surumRozeti")
        surum.setAlignment(Qt.AlignBottom)

        duzen.addWidget(marka)
        duzen.addWidget(surum)
        duzen.addStretch(1)

        self.rozet_asama = Rozet("Hazır", "notr")
        self.rozet_gpu = Rozet("Donanım okunuyor…", "notr")
        self.rozet_ffmpeg = Rozet("FFmpeg aranıyor…", "notr")
        for rozet in (self.rozet_asama, self.rozet_gpu, self.rozet_ffmpeg):
            duzen.addWidget(rozet)

        return serit

    # --- sol panel -------------------------------------------------------

    def _sol_panel(self) -> QWidget:
        panel = QWidget()
        panel.setMinimumWidth(400)
        panel.setMaximumWidth(560)
        duzen = QVBoxLayout(panel)
        duzen.setContentsMargins(0, 0, 0, 0)
        duzen.setSpacing(12)

        self.birakma = BirakmaAlani()
        self.birakma.dosyalar_birakildi.connect(self.dosya_ekle)
        self.birakma.tiklandi.connect(self.gozat)
        duzen.addWidget(self.birakma)

        duzen.addWidget(self._kuyruk_karti(), 1)
        duzen.addWidget(self._ayar_sekmeleri(), 2)
        return panel

    def _kuyruk_karti(self) -> QWidget:
        kart = Kart("Kuyruk")

        self.lbl_kuyruk = QLabel("boş")
        self.lbl_kuyruk.setObjectName("yardimMetni")
        kart.baslik_satiri.addWidget(self.lbl_kuyruk)

        btn_ekle = QPushButton("Ekle")
        btn_ekle.setObjectName("kucukDugme")
        btn_ekle.clicked.connect(self.gozat)

        btn_cikar = QPushButton("Çıkar")
        btn_cikar.setObjectName("kucukDugme")
        btn_cikar.clicked.connect(self.secileni_cikar)

        btn_temizle = QPushButton("Temizle")
        btn_temizle.setObjectName("kucukDugme")
        btn_temizle.clicked.connect(self.kuyrugu_temizle)

        for dugme in (btn_ekle, btn_cikar, btn_temizle):
            kart.baslik_satiri.addWidget(dugme)
        self._kuyruk_dugmeleri = [btn_ekle, btn_cikar, btn_temizle]

        self.liste = QListWidget()
        self.liste.setSelectionMode(QListWidget.ExtendedSelection)
        self.liste.setMinimumHeight(110)
        self.liste.itemDoubleClicked.connect(self._klasoru_ac_oge)
        kart.ekle(self.liste, 1)
        return kart

    # --- ayar sekmeleri --------------------------------------------------

    def _ayar_sekmeleri(self) -> QWidget:
        self.sekmeler = QTabWidget()
        self.sekmeler.addTab(kaydirilabilir(self._temel_sekme()), "Temel")
        self.sekmeler.addTab(kaydirilabilir(self._gelismis_sekme()), "Gelişmiş")
        return self.sekmeler

    def _temel_sekme(self) -> QWidget:
        sayfa = QWidget()
        duzen = QVBoxLayout(sayfa)
        duzen.setContentsMargins(14, 14, 14, 14)
        duzen.setSpacing(12)

        self.cmb_model = QComboBox()
        self.cmb_model.setEditable(True)
        self.cmb_model.addItems(MODELLER)
        duzen.addWidget(alan(
            "Model", self.cmb_model,
            "Büyük model daha doğru ama daha yavaş. «turbo» çoğu iş için "
            "en iyi denge; kendi .pt dosyanızın yolunu da yazabilirsiniz."))

        self.cmb_dil = SecimKutusu(dil_secenekleri())
        duzen.addWidget(alan(
            "Konuşulan dil", self.cmb_dil,
            "Dili biliyorsanız seçin — tespit adımı atlanır ve sonuç kesinleşir."))

        self.cmb_gorev = SecimKutusu(list(GOREVLER.items()))
        duzen.addWidget(alan(
            "Görev", self.cmb_gorev,
            "«translate» konuşmayı doğrudan İngilizce metne çevirir; "
            "başka bir hedef dil desteklenmez."))

        self.cmb_format = SecimKutusu([(f, f if f != "all" else "hepsi (all)")
                                       for f in CIKTI_FORMATLARI])
        self.cmb_cihaz = SecimKutusu([("auto", "Otomatik"), ("cuda", "GPU (cuda/ROCm)"),
                                      ("cpu", "CPU")])
        duzen.addWidget(ikili(
            alan("Çıktı formatı", self.cmb_format),
            alan("İşlem birimi", self.cmb_cihaz)))

        self.txt_cikti = QLineEdit()
        self.txt_cikti.setPlaceholderText("Kaynak dosyanın yanına")
        btn_cikti = QPushButton("Seç")
        btn_cikti.setObjectName("kucukDugme")
        btn_cikti.clicked.connect(self._cikti_klasoru_sec)
        btn_cikti_sifirla = QPushButton("Sıfırla")
        btn_cikti_sifirla.setObjectName("kucukDugme")
        btn_cikti_sifirla.clicked.connect(lambda: self.txt_cikti.setText(""))

        cikti_satiri = QWidget()
        cikti_duzen = QHBoxLayout(cikti_satiri)
        cikti_duzen.setContentsMargins(0, 0, 0, 0)
        cikti_duzen.setSpacing(6)
        cikti_duzen.addWidget(self.txt_cikti, 1)
        cikti_duzen.addWidget(btn_cikti)
        cikti_duzen.addWidget(btn_cikti_sifirla)
        duzen.addWidget(alan("Çıktı klasörü", cikti_satiri))

        duzen.addWidget(Ayirac())
        duzen.addWidget(BolumBaslik("Ek işlemler"))

        self.chk_turkce = Onay(
            "İşlem bitince ayrıca Türkçe altyazı üret",
            "Orijinal altyazı «Dosya_EN.srt» olarak, Türkçesi «Dosya.srt» olarak kaydedilir.")
        self.chk_sansursuz = Onay(
            "Sansürsüz mod",
            "Küfür ve argonun yumuşatılmadan yazılması için dile uygun bir başlangıç "
            "istemi verilir; çeviride yıldızlanan satırlar tekrar istenir.")
        self.chk_dil_oylama = Onay(
            "Dili örnekleyerek tespit et",
            "Whisper varsayılan olarak yalnızca ilk 30 saniyeye bakar. Bu seçenek "
            "dosyanın geneline yayılmış pencerelerden oy toplar — müzikle veya "
            "sessizlikle başlayan dosyalarda çok daha isabetli.")
        self.chk_tekrar = Onay(
            "Tekrar döngülerini ayıkla",
            "Model aynı cümleyi arka arkaya üretmeye başlarsa (halüsinasyon döngüsü) "
            "fazlalık bloklar atılır.")
        self.chk_orijinal_sil = Onay(
            "Çeviriden sonra orijinal dildeki altyazıyı sil",
            "Türkçe altyazı eksiksiz yazıldıysa «Dosya_EN.srt» gibi orijinal dildeki "
            "dosya Geri Dönüşüm Kutusu'na taşınır (kalıcı silinmez). Çevrilemeyen "
            "satır varsa ya da iş iptal edildiyse orijinal korunur.")
        self.chk_turkce.toggled.connect(self.chk_orijinal_sil.setEnabled)
        orijinal_sil_satiri = QWidget()
        orijinal_sil_duzen = QHBoxLayout(orijinal_sil_satiri)
        orijinal_sil_duzen.setContentsMargins(26, 0, 0, 0)   # Türkçe seçeneğine bağlı
        orijinal_sil_duzen.addWidget(self.chk_orijinal_sil)

        duzen.addWidget(self.chk_turkce)
        duzen.addWidget(orijinal_sil_satiri)
        for onay in (self.chk_sansursuz, self.chk_dil_oylama, self.chk_tekrar):
            duzen.addWidget(onay)

        self.txt_ffmpeg = QLineEdit()
        self.txt_ffmpeg.setPlaceholderText("Sistemdeki ffmpeg kullanılacak")
        self.txt_ffmpeg.textChanged.connect(lambda _: self._ffmpeg_zamanlayici.start(600))
        btn_ffmpeg = QPushButton("Seç")
        btn_ffmpeg.setObjectName("kucukDugme")
        btn_ffmpeg.clicked.connect(self._ffmpeg_sec)

        ffmpeg_satiri = QWidget()
        ffmpeg_duzen = QHBoxLayout(ffmpeg_satiri)
        ffmpeg_duzen.setContentsMargins(0, 0, 0, 0)
        ffmpeg_duzen.setSpacing(6)
        ffmpeg_duzen.addWidget(self.txt_ffmpeg, 1)
        ffmpeg_duzen.addWidget(btn_ffmpeg)

        self.txt_gemini = QLineEdit()
        self.txt_gemini.setPlaceholderText("Boş — Google + MyMemory kullanılır")
        self.txt_gemini.setEchoMode(QLineEdit.Password)
        duzen.addWidget(Ayirac())
        duzen.addWidget(alan(
            "Gemini API anahtarı (isteğe bağlı)", self.txt_gemini,
            "Girilirse çeviride ilk sırada Gemini denenir. Google Translate "
            "argoyu sözlükten çeviriyor ve küfrü yumuşatıyor (ölçüldü: "
            "«polla» → «horoz», «nabos» → «şalgam», emir kipi «Chupa» → mastar "
            "«Emmek»). Dil modeli hem bağlamı görüyor hem de sertlik derecesini "
            "koruyor. Anahtarı aistudio.google.com adresinden ücretsiz alınır."))

        self.txt_deepl = QLineEdit()
        self.txt_deepl.setPlaceholderText("Boş — DeepL denenmez")
        self.txt_deepl.setEchoMode(QLineEdit.Password)
        self.chk_deepl = Onay(
            "DeepL'i kullan",
            "Açıkken Gemini'nin çeviremediği satırlar (günlük kota dolduğunda ya da "
            "hata verdiğinde) Google'dan önce DeepL'e gider. Kapalıyken anahtar "
            "saklanır ama DeepL'e hiç istek gitmez; aylık karakter kotası harcanmaz.")
        self.chk_deepl.toggled.connect(self.txt_deepl.setEnabled)
        duzen.addWidget(Ayirac())
        duzen.addWidget(self.chk_deepl)
        duzen.addWidget(alan(
            "DeepL API anahtarı (isteğe bağlı)", self.txt_deepl,
            "«DeepL'i kullan» açıkken Gemini'den sonra, Google'dan önce denenir. "
            "Ücretsiz katman ayda 500.000 karakter (bir film ~30-50 bin)."))

        duzen.addWidget(Ayirac())
        duzen.addWidget(alan(
            "FFmpeg yolu", ffmpeg_satiri,
            "Ses FFmpeg ile çözülür. Boş bırakılırsa PATH'teki kurulum kullanılır."))

        self._ffmpeg_zamanlayici = QTimer(self)
        self._ffmpeg_zamanlayici.setSingleShot(True)
        self._ffmpeg_zamanlayici.timeout.connect(self._ffmpegi_sorgula)

        duzen.addStretch(1)
        return sayfa

    def _gelismis_sekme(self) -> QWidget:
        sayfa = QWidget()
        duzen = QVBoxLayout(sayfa)
        duzen.setContentsMargins(14, 14, 14, 14)
        duzen.setSpacing(12)

        # --- kod çözme ---
        duzen.addWidget(BolumBaslik("Kod çözme"))

        self.spn_temperature = QDoubleSpinBox()
        self.spn_temperature.setRange(0.0, 1.0)
        self.spn_temperature.setSingleStep(0.1)
        self.spn_temperature.setDecimals(2)

        self.spn_temp_artis = QDoubleSpinBox()
        self.spn_temp_artis.setRange(0.0, 1.0)
        self.spn_temp_artis.setSingleStep(0.1)
        self.spn_temp_artis.setDecimals(2)

        duzen.addWidget(ikili(
            alan("Sıcaklık", self.spn_temperature),
            alan("Geri düşüş artışı", self.spn_temp_artis)))
        duzen.addWidget(self._yardim(
            "0 en kararlı sonucu verir. Kod çözme eşiklere takılırsa sıcaklık "
            "artış kadar yükseltilip tekrar denenir; artış 0 ise tek deneme yapılır."))

        self.spn_beam = QSpinBox()
        self.spn_beam.setRange(0, 20)
        self.spn_best_of = QSpinBox()
        self.spn_best_of.setRange(1, 20)
        duzen.addWidget(ikili(
            alan("Işın sayısı (beam)", self.spn_beam),
            alan("Aday sayısı (best of)", self.spn_best_of)))
        duzen.addWidget(self._yardim(
            "Işın araması yalnızca sıcaklık 0 iken çalışır; sıcaklık yükselince "
            "«aday sayısı» devreye girer. Büyütmek doğruluğu biraz artırır, hızı düşürür."))

        self.spn_patience = IstegeBagliSayi(0.5, 10.0, 0.1, 2, "varsayılan (1.0)")
        self.spn_uzunluk = IstegeBagliSayi(0.0, 5.0, 0.1, 2, "varsayılan")
        duzen.addWidget(ikili(
            alan("Sabır (patience)", self.spn_patience),
            alan("Uzunluk cezası", self.spn_uzunluk)))

        self.txt_suppress = QLineEdit()
        duzen.addWidget(alan(
            "Bastırılan token'lar", self.txt_suppress,
            "Virgülle ayrılmış token kimlikleri. «-1» yaygın noktalama dışındaki "
            "özel karakterleri bastırır."))

        self.txt_istem = QLineEdit()
        self.txt_istem.setPlaceholderText("Boş — sansürsüz mod açıksa kendi kalıbını kullanır")
        duzen.addWidget(alan(
            "Başlangıç istemi", self.txt_istem,
            "Özel adlar, terimler ya da noktalama tarzı için ipucu. Doldurursanız "
            "sansürsüz modun hazır kalıbının yerine bu geçer."))

        self.chk_istem_tasi = Onay(
            "İstemi her pencereye taşı",
            "İstem her 30 saniyelik pencerede tekrar verilir; önceki metne "
            "koşullanmayı zayıflatabilir.")
        self.chk_onceki_metin = Onay(
            "Önceki metne koşullan",
            "Açıkken pencereler arası tutarlılık artar, ama model bir kez "
            "takılırsa döngüye girme ihtimali de artar.")
        self.chk_fp16 = Onay(
            "fp16 (yarım hassasiyet)",
            "GPU'da model ağırlıkları da yarı hassasiyetle tutulur: bellek "
            "yarıya iner (large-v3 için 5,75 GB yerine 2,87 GB) ve işlem "
            "hızlanır. CPU'da yok sayılır.")
        for onay in (self.chk_istem_tasi, self.chk_onceki_metin, self.chk_fp16):
            duzen.addWidget(onay)

        # --- eşikler ---
        duzen.addWidget(Ayirac())
        duzen.addWidget(BolumBaslik("Eşikler"))

        self.spn_sikistirma = IstegeBagliSayi(0.5, 10.0, 0.1, 2, "kapalı")
        self.spn_logprob = IstegeBagliSayi(-10.0, 0.0, 0.1, 2, "kapalı")
        duzen.addWidget(ikili(
            alan("Sıkıştırma oranı", self.spn_sikistirma),
            alan("Ortalama log olasılık", self.spn_logprob)))
        duzen.addWidget(self._yardim(
            "Bu eşikler «kod çözme başarısız» kararını verir: metin fazla tekrarlıysa "
            "(sıkıştırma) ya da model kendine güvenmiyorsa (log olasılık) pencere "
            "daha yüksek sıcaklıkla tekrar denenir."))

        self.spn_sessizlik = IstegeBagliSayi(0.0, 1.0, 0.05, 2, "kapalı")
        self.spn_halusinasyon = IstegeBagliSayi(0.5, 60.0, 0.5, 1, "kapalı")
        duzen.addWidget(ikili(
            alan("Konuşma yok eşiği", self.spn_sessizlik),
            alan("Halüsinasyon sessizliği (sn)", self.spn_halusinasyon)))
        duzen.addWidget(self._yardim(
            "«Konuşma yok eşiği» whisper'da şu karşılaştırmadır: "
            "no_speech_prob > eşik ise pencere sessizlik sayılıp ATLANIR. "
            "Yani eşiği DÜŞÜRMEK daha çok pencereyi attırır, yükseltmek daha az. "
            "«Kapalı» yaparsanız hiçbir pencere sessizlik diye atılmaz — fısıltı, "
            "müzik altındaki konuşma ve nefesli sesler kaybolmaz. "
            "Halüsinasyon eşiği kelime zaman damgası gerektirir; bu kadar uzun "
            "sessizlikler atlanır."))

        # --- kelime zaman damgaları ---
        duzen.addWidget(Ayirac())
        duzen.addWidget(BolumBaslik("Kelime zaman damgaları"))

        self.chk_kelime_zaman = Onay(
            "Kelime kelime zamanla (deneysel)",
            "Her kelimenin başlangıç/bitiş anını çıkarır ve segment zamanlarını "
            "buna göre düzeltir. Aşağıdaki seçeneklerin hepsi bunu gerektirir.")
        self.chk_vurgula = Onay(
            "Söylenen kelimenin altını çiz",
            "srt/vtt çıktısında karaoke etkisi; dosya boyutunu büyütür.")
        duzen.addWidget(self.chk_kelime_zaman)
        duzen.addWidget(self.chk_vurgula)

        self.chk_akilli = Onay(
            "Altyazıyı kelime zamanlarına göre yeniden bölümle",
            "Whisper'ın 8-10 saniyelik uzun blokları yerine cümle sonlarında, "
            "duraklamalarda ve süre/karakter sınırında bölünmüş kısa bloklar "
            "üretir. Blok bitişi son kelimenin bittiği andır, yani konuşma "
            "olmayan boşluklar altyazıyla doldurulmaz.")
        duzen.addWidget(self.chk_akilli)

        self.spn_blok_karakter = QSpinBox(); self.spn_blok_karakter.setRange(20, 200)
        self.spn_blok_sure = QDoubleSpinBox()
        self.spn_blok_sure.setRange(1.0, 30.0); self.spn_blok_sure.setSingleStep(0.5)
        self.spn_blok_sure.setDecimals(1)
        duzen.addWidget(ikili(
            alan("Blok başına karakter", self.spn_blok_karakter),
            alan("Blok başına saniye", self.spn_blok_sure)))

        self.spn_blok_bosluk = QDoubleSpinBox()
        self.spn_blok_bosluk.setRange(0.1, 5.0); self.spn_blok_bosluk.setSingleStep(0.1)
        self.spn_blok_bosluk.setDecimals(2)
        self.spn_satir_karakter = QSpinBox(); self.spn_satir_karakter.setRange(20, 100)
        duzen.addWidget(ikili(
            alan("Bölen sessizlik (sn)", self.spn_blok_bosluk),
            alan("Satır genişliği", self.spn_satir_karakter)))
        duzen.addWidget(Ayirac())
        duzen.addWidget(BolumBaslik("Akıllı bölümleme kapalıyken"))
        duzen.addWidget(self._yardim(
            "Bu üç alan whisper'ın kendi altyazı yazıcısına aktarılır ve yalnızca "
            "yukarıdaki akıllı bölümleme KAPALIYKEN işe yarar. Genişlik ile sayıyı "
            "birlikte doldurmak whisper'ın segment sınırlarını yok saymasına ve "
            "cümle ortasından bölmesine yol açıyor — akıllı bölümleme tam da bunu "
            "düzeltmek için var."))

        self.spn_satir_genislik = IstegeBagliTamSayi(10, 200, "sınırsız")
        self.spn_satir_sayisi = IstegeBagliTamSayi(1, 10, "sınırsız")
        duzen.addWidget(ikili(
            alan("Whisper satır genişliği", self.spn_satir_genislik),
            alan("Whisper satır sayısı", self.spn_satir_sayisi)))

        self.spn_kelime_sayisi = IstegeBagliTamSayi(1, 30, "sınırsız")
        duzen.addWidget(alan(
            "Satır başına kelime", self.spn_kelime_sayisi,
            "«Satır genişliği» doluyken bu ayarın etkisi olmaz."))

        self.txt_on_noktalama = QLineEdit()
        self.txt_son_noktalama = QLineEdit()
        duzen.addWidget(alan("Sonraki kelimeye eklenecek noktalama", self.txt_on_noktalama))
        duzen.addWidget(alan("Önceki kelimeye eklenecek noktalama", self.txt_son_noktalama))

        self.chk_kelime_zaman.toggled.connect(self._kelime_alanlarini_guncelle)

        # --- diğer ---
        duzen.addWidget(Ayirac())
        duzen.addWidget(BolumBaslik("Diğer"))

        self.spn_threads = QSpinBox()
        self.spn_threads.setRange(0, max(1, os.cpu_count() or 8))
        self.spn_threads.setSpecialValueText("otomatik")

        self.spn_tekrar_limit = QSpinBox()
        self.spn_tekrar_limit.setRange(2, 200)
        duzen.addWidget(ikili(
            alan("CPU iş parçacığı", self.spn_threads),
            alan("Tekrar limiti", self.spn_tekrar_limit)))

        self.txt_klipler = QLineEdit()
        duzen.addWidget(alan(
            "İşlenecek aralıklar", self.txt_klipler,
            "Saniye cinsinden «başlangıç,bitiş,başlangıç,bitiş…». «0» tüm dosya demektir."))

        self.txt_model_klasor = QLineEdit()
        tasinabilir = varsayilan_model_klasoru()
        self.txt_model_klasor.setPlaceholderText(
            tasinabilir if tasinabilir else "~/.cache/whisper")
        btn_model_klasor = QPushButton("Seç")
        btn_model_klasor.setObjectName("kucukDugme")
        btn_model_klasor.clicked.connect(self._model_klasoru_sec)
        model_satiri = QWidget()
        model_duzen = QHBoxLayout(model_satiri)
        model_duzen.setContentsMargins(0, 0, 0, 0)
        model_duzen.setSpacing(6)
        model_duzen.addWidget(self.txt_model_klasor, 1)
        model_duzen.addWidget(btn_model_klasor)
        model_yardim = ("Model dosyalarının indirileceği/okunacağı yer. "
                        "Boş bırakılırsa: ")
        model_yardim += ("uygulamanın yanındaki «models» klasörü (taşınabilir kurulum)."
                         if tasinabilir else "~/.cache/whisper")
        duzen.addWidget(alan("Model klasörü", model_satiri, model_yardim))

        duzen.addWidget(Ayirac())
        btn_varsayilan = QPushButton("Gelişmiş ayarları varsayılana döndür")
        btn_varsayilan.clicked.connect(self._varsayilanlara_don)
        duzen.addWidget(btn_varsayilan)

        duzen.addStretch(1)
        return sayfa

    @staticmethod
    def _yardim(metin: str) -> QLabel:
        etiket = QLabel(metin)
        etiket.setObjectName("yardimMetni")
        etiket.setWordWrap(True)
        return etiket

    # --- sağ panel -------------------------------------------------------

    def _sag_panel(self) -> QWidget:
        sekmeler = QTabWidget()

        self.akis = AkisGorunumu()
        self.gunluk = GunlukGorunumu()
        sekmeler.addTab(self._sekme_kabi(self.akis), "Canlı akış")
        sekmeler.addTab(self._sekme_kabi(self.gunluk), "Günlük")
        self.sag_sekmeler = sekmeler
        return sekmeler

    @staticmethod
    def _sekme_kabi(icerik: QWidget) -> QWidget:
        kap = QWidget()
        duzen = QVBoxLayout(kap)
        duzen.setContentsMargins(12, 12, 12, 12)
        duzen.addWidget(icerik)
        return kap

    # --- alt şerit -------------------------------------------------------

    def _alt_serit(self) -> QWidget:
        serit = QFrame()
        serit.setObjectName("altSerit")

        duzen = QVBoxLayout(serit)
        duzen.setContentsMargins(20, 12, 20, 14)
        duzen.setSpacing(9)

        ust = QHBoxLayout()
        self.lbl_durum = QLabel("Hazır.")
        self.lbl_durum.setObjectName("durumMetni")
        self.lbl_yuzde = QLabel("")
        self.lbl_yuzde.setObjectName("yuzdeMetni")
        ust.addWidget(self.lbl_durum, 1)
        ust.addWidget(self.lbl_yuzde)
        duzen.addLayout(ust)

        self.cubuk = QProgressBar()
        self.cubuk.setRange(0, 1000)
        self.cubuk.setValue(0)
        self.cubuk.setTextVisible(False)
        self.cubuk.setFixedHeight(12)
        duzen.addWidget(self.cubuk)

        alt = QHBoxLayout()
        alt.setSpacing(10)

        self.btn_basla = QPushButton("Başlat")
        self.btn_basla.setObjectName("anaDugme")
        self.btn_basla.setMinimumWidth(180)
        self.btn_basla.clicked.connect(self.basla)

        self.btn_iptal = QPushButton("İptal")
        self.btn_iptal.setObjectName("iptalDugme")
        self.btn_iptal.setMinimumWidth(120)
        self.btn_iptal.setEnabled(False)
        self.btn_iptal.clicked.connect(self.iptal)

        self.btn_klasor = QPushButton("Çıktı klasörünü aç")
        self.btn_klasor.setEnabled(False)
        self.btn_klasor.clicked.connect(self._cikti_klasorunu_ac)

        alt.addWidget(self.btn_basla)
        alt.addWidget(self.btn_iptal)
        alt.addStretch(1)
        alt.addWidget(self.btn_klasor)
        duzen.addLayout(alt)

        return serit

    def _kisayollari_kur(self) -> None:
        sil = QAction(self)
        sil.setShortcut(QKeySequence.Delete)
        sil.triggered.connect(self.secileni_cikar)
        self.liste.addAction(sil)
        self.liste.setContextMenuPolicy(Qt.ActionsContextMenu)

        basla = QAction(self)
        basla.setShortcut(QKeySequence("Ctrl+Return"))
        basla.triggered.connect(self.basla)
        self.addAction(basla)

        ac = QAction(self)
        ac.setShortcut(QKeySequence.Open)
        ac.triggered.connect(self.gozat)
        self.addAction(ac)

    # ----------------------------------------------------------------------
    # Ayar ↔ widget aktarımı
    # ----------------------------------------------------------------------

    def _ayarlari_widgetlara_yaz(self) -> None:
        a = self.ayarlar

        self.cmb_model.setCurrentText(a.model)
        self.cmb_dil.degeri_ayarla(a.dil)
        self.cmb_gorev.degeri_ayarla(a.gorev)
        self.cmb_format.degeri_ayarla(a.cikti_formati)
        self.cmb_cihaz.degeri_ayarla(a.cihaz)
        self.txt_cikti.setText(a.cikti_klasoru)
        self.txt_ffmpeg.setText(a.ffmpeg_yolu)
        self.txt_deepl.setText(a.deepl_anahtari)
        self.chk_deepl.setChecked(a.deepl_kullan)
        self.txt_deepl.setEnabled(a.deepl_kullan)
        self.txt_gemini.setText(a.gemini_anahtari)

        self.chk_turkce.setChecked(a.turkce_ceviri)
        self.chk_orijinal_sil.setChecked(a.orijinali_sil)
        self.chk_orijinal_sil.setEnabled(a.turkce_ceviri)
        self.chk_sansursuz.setChecked(a.sansursuz)
        self.chk_dil_oylama.setChecked(a.dil_oylamasi)
        self.chk_tekrar.setChecked(a.tekrar_filtresi)

        self.spn_temperature.setValue(a.temperature)
        self.spn_temp_artis.setValue(a.temperature_increment_on_fallback)
        self.spn_beam.setValue(a.beam_size)
        self.spn_best_of.setValue(a.best_of)
        self.spn_patience.degeri_ayarla(a.patience)
        self.spn_uzunluk.degeri_ayarla(a.length_penalty)
        self.txt_suppress.setText(a.suppress_tokens)
        self.txt_istem.setText(a.initial_prompt)
        self.chk_istem_tasi.setChecked(a.carry_initial_prompt)
        self.chk_onceki_metin.setChecked(a.condition_on_previous_text)
        self.chk_fp16.setChecked(a.fp16)

        self.spn_sikistirma.degeri_ayarla(a.compression_ratio_threshold)
        self.spn_logprob.degeri_ayarla(a.logprob_threshold)
        self.spn_sessizlik.degeri_ayarla(a.no_speech_threshold)
        self.spn_halusinasyon.degeri_ayarla(a.hallucination_silence_threshold)

        self.chk_kelime_zaman.setChecked(a.word_timestamps)
        self.chk_akilli.setChecked(a.akilli_bolumleme)
        self.spn_blok_karakter.setValue(a.blok_max_karakter)
        self.spn_blok_sure.setValue(a.blok_max_sure)
        self.spn_blok_bosluk.setValue(a.blok_max_bosluk)
        self.spn_satir_karakter.setValue(a.satir_max_karakter)
        self.chk_vurgula.setChecked(a.highlight_words)
        self.spn_satir_genislik.degeri_ayarla(a.max_line_width)
        self.spn_satir_sayisi.degeri_ayarla(a.max_line_count)
        self.spn_kelime_sayisi.degeri_ayarla(a.max_words_per_line)
        self.txt_on_noktalama.setText(a.prepend_punctuations)
        self.txt_son_noktalama.setText(a.append_punctuations)

        self.spn_threads.setValue(a.threads)
        self.spn_tekrar_limit.setValue(a.tekrar_limiti)
        self.txt_klipler.setText(a.clip_timestamps)
        self.txt_model_klasor.setText(a.model_klasoru)

        self._kelime_alanlarini_guncelle(a.word_timestamps)

    def _widgetlardan_ayarlari_oku(self) -> None:
        a = self.ayarlar

        a.model = self.cmb_model.currentText().strip() or "turbo"
        a.dil = self.cmb_dil.deger()
        a.gorev = self.cmb_gorev.deger()
        a.cikti_formati = self.cmb_format.deger()
        a.cihaz = self.cmb_cihaz.deger()
        a.cikti_klasoru = self.txt_cikti.text().strip()
        a.ffmpeg_yolu = self.txt_ffmpeg.text().strip()
        a.deepl_anahtari = self.txt_deepl.text().strip()
        a.deepl_kullan = self.chk_deepl.isChecked()
        a.gemini_anahtari = self.txt_gemini.text().strip()

        a.turkce_ceviri = self.chk_turkce.isChecked()
        a.orijinali_sil = self.chk_orijinal_sil.isChecked()
        a.sansursuz = self.chk_sansursuz.isChecked()
        a.dil_oylamasi = self.chk_dil_oylama.isChecked()
        a.tekrar_filtresi = self.chk_tekrar.isChecked()

        a.temperature = self.spn_temperature.value()
        a.temperature_increment_on_fallback = self.spn_temp_artis.value()
        a.beam_size = self.spn_beam.value()
        a.best_of = self.spn_best_of.value()
        a.patience = self.spn_patience.deger()
        a.length_penalty = self.spn_uzunluk.deger()
        a.suppress_tokens = self.txt_suppress.text().strip() or "-1"
        a.initial_prompt = self.txt_istem.text().strip()
        a.carry_initial_prompt = self.chk_istem_tasi.isChecked()
        a.condition_on_previous_text = self.chk_onceki_metin.isChecked()
        a.fp16 = self.chk_fp16.isChecked()

        a.compression_ratio_threshold = self.spn_sikistirma.deger()
        a.logprob_threshold = self.spn_logprob.deger()
        a.no_speech_threshold = self.spn_sessizlik.deger()
        a.hallucination_silence_threshold = self.spn_halusinasyon.deger()

        a.word_timestamps = self.chk_kelime_zaman.isChecked()
        a.akilli_bolumleme = self.chk_akilli.isChecked()
        a.blok_max_karakter = self.spn_blok_karakter.value()
        a.blok_max_sure = self.spn_blok_sure.value()
        a.blok_max_bosluk = self.spn_blok_bosluk.value()
        a.satir_max_karakter = self.spn_satir_karakter.value()
        a.highlight_words = self.chk_vurgula.isChecked()
        a.max_line_width = self.spn_satir_genislik.deger()
        a.max_line_count = self.spn_satir_sayisi.deger()
        a.max_words_per_line = self.spn_kelime_sayisi.deger()
        a.prepend_punctuations = self.txt_on_noktalama.text()
        a.append_punctuations = self.txt_son_noktalama.text()

        a.threads = self.spn_threads.value()
        a.tekrar_limiti = self.spn_tekrar_limit.value()
        a.clip_timestamps = self.txt_klipler.text().strip() or "0"
        a.model_klasoru = self.txt_model_klasor.text().strip()

    def _kelime_alanlarini_guncelle(self, acik: bool) -> None:
        """Kelime zaman damgası kapalıyken ona bağlı alanlar erişilemez olsun."""
        for widget in (self.chk_vurgula, self.spn_satir_genislik, self.spn_satir_sayisi,
                       self.spn_kelime_sayisi, self.txt_on_noktalama,
                       self.txt_son_noktalama, self.spn_halusinasyon,
                       self.chk_akilli, self.spn_blok_karakter, self.spn_blok_sure,
                       self.spn_blok_bosluk, self.spn_satir_karakter):
            widget.setEnabled(acik)

    def _varsayilanlara_don(self) -> None:
        varsayilan = Ayarlar()
        a = self.ayarlar
        for ad in ("temperature", "temperature_increment_on_fallback", "beam_size",
                   "best_of", "patience", "length_penalty", "suppress_tokens",
                   "initial_prompt", "carry_initial_prompt", "condition_on_previous_text",
                   "fp16", "compression_ratio_threshold", "logprob_threshold",
                   "no_speech_threshold", "hallucination_silence_threshold",
                   "word_timestamps", "highlight_words", "max_line_width",
                   "max_line_count", "max_words_per_line", "prepend_punctuations",
                   "append_punctuations", "threads", "clip_timestamps",
                   "tekrar_limiti", "model_klasoru"):
            setattr(a, ad, getattr(varsayilan, ad))
        self._ayarlari_widgetlara_yaz()
        self._gunluk_ekle("↩️ Gelişmiş ayarlar varsayılana döndürüldü.")

    # ----------------------------------------------------------------------
    # Kuyruk
    # ----------------------------------------------------------------------

    def dosya_ekle(self, yollar: list[str]) -> None:
        mevcut = {self.liste.item(i).data(Qt.UserRole) for i in range(self.liste.count())}
        eklenen = 0
        for yol in yollar:
            yol = os.path.abspath(yol)
            if yol in mevcut or not os.path.isfile(yol):
                continue
            oge = QListWidgetItem(f"{DURUM_SIMGELERI['bekliyor']}  {os.path.basename(yol)}")
            oge.setData(Qt.UserRole, yol)
            oge.setToolTip(yol)
            self.liste.addItem(oge)
            mevcut.add(yol)
            eklenen += 1

        if eklenen:
            self.ayarlar.son_klasor = os.path.dirname(os.path.abspath(yollar[0]))
            self._gunluk_ekle(f"➕ {eklenen} dosya kuyruğa eklendi.")
        self._kuyrugu_yenile()

    def gozat(self) -> None:
        kaliplar = " ".join(f"*{u}" for u in MEDYA_UZANTILARI)
        yollar, _ = QFileDialog.getOpenFileNames(
            self, "Medya dosyalarını seçin",
            self.ayarlar.son_klasor or os.path.expanduser("~"),
            f"Medya dosyaları ({kaliplar});;Tüm dosyalar (*.*)")
        if yollar:
            self.dosya_ekle(yollar)

    def secileni_cikar(self) -> None:
        if self._calisiyor():
            return
        for oge in self.liste.selectedItems():
            self.liste.takeItem(self.liste.row(oge))
        self._kuyrugu_yenile()

    def kuyrugu_temizle(self) -> None:
        if self._calisiyor():
            return
        self.liste.clear()
        self._kuyrugu_yenile()

    def _kuyruk_yollari(self) -> list[str]:
        return [self.liste.item(i).data(Qt.UserRole) for i in range(self.liste.count())]

    def _kuyrugu_yenile(self) -> None:
        sayi = self.liste.count()
        self.lbl_kuyruk.setText("boş" if sayi == 0 else f"{sayi} dosya")
        self.btn_basla.setEnabled(sayi > 0 and not self._calisiyor())

    def _ogeyi_bul(self, yol: str) -> QListWidgetItem | None:
        """Kuyruk satırını yola göre bulur.

        Sıra numarasıyla aramıyoruz: biten dosyalar listeden silindiği için
        işçinin gönderdiği sıra ile listedeki satır numarası birbirini tutmaz.
        """
        for i in range(self.liste.count()):
            oge = self.liste.item(i)
            if oge.data(Qt.UserRole) == yol:
                return oge
        return None

    def _oge_durumu(self, yol: str, durum: str) -> None:
        oge = self._ogeyi_bul(yol)
        if oge is None:
            return
        oge.setText(f"{DURUM_SIMGELERI[durum]}  {os.path.basename(yol)}")
        if durum == "isleniyor":
            self.liste.setCurrentItem(oge)
            self.liste.scrollToItem(oge)

    def _ogeyi_sil(self, yol: str) -> None:
        oge = self._ogeyi_bul(yol)
        if oge is not None:
            self.liste.takeItem(self.liste.row(oge))
        self._kuyrugu_yenile()

    def _klasoru_ac_oge(self, oge: QListWidgetItem) -> None:
        self._klasoru_ac(os.path.dirname(oge.data(Qt.UserRole)))

    # ----------------------------------------------------------------------
    # Çalıştırma
    # ----------------------------------------------------------------------

    def _calisiyor(self) -> bool:
        return self.isci is not None and self.isci.isRunning()

    def basla(self) -> None:
        if self._calisiyor():
            return

        dosyalar = self._kuyruk_yollari()
        if not dosyalar:
            QMessageBox.information(self, "Kuyruk boş",
                                    "Önce bir medya dosyası ekleyin.")
            return

        self._widgetlardan_ayarlari_oku()

        if not media.ffmpeg_coz(self.ayarlar.ffmpeg_yolu):
            QMessageBox.critical(
                self, "FFmpeg bulunamadı",
                "Ses FFmpeg ile çözülüyor ve sisteminizde bulunamadı.\n\n"
                "Kurmak için:\n    winget install Gyan.FFmpeg\n\n"
                "Zaten kuruluysa «FFmpeg yolu» alanından ffmpeg.exe dosyasını seçin.")
            return

        for uyari in self.ayarlar.dogrula():
            self._gunluk_ekle(f"⚠️ {uyari}")
        self.ayarlar.duzelt()
        self._ayarlari_widgetlara_yaz()
        self.ayarlar.kaydet()

        self._iptal_edildi_mi = False
        self.akis.temizle()
        self.akis.basliga_yaz("metin geldikçe burada görünecek")
        for yol in self._kuyruk_yollari():
            self._oge_durumu(yol, "bekliyor")

        self._arayuzu_kilitle(True)
        self._son_ciktilar = []

        self.isci = Isci(dosyalar, self.ayarlar, self.motor, self)
        self.isci.gunluk.connect(self._gunluk_ekle)
        self.isci.ilerleme.connect(self._ilerleme_guncelle)
        self.isci.segment.connect(self.akis.segment_ekle)
        self.isci.dosya_basladi.connect(self._dosya_basladi)
        self.isci.dosya_bitti.connect(self._dosya_bitti)
        self.isci.tamamlandi.connect(self._is_tamamlandi)
        self.isci.akis_temizle.connect(self.akis.temizle)
        self.isci.durum_degisti.connect(
            lambda ad: self.rozet_asama.guncelle(ad, "mesgul"))
        self.isci.start()

    def iptal(self) -> None:
        if not self._calisiyor():
            return
        self._iptal_edildi_mi = True
        self.isci.iptal_et()
        self.btn_iptal.setEnabled(False)
        self.btn_iptal.setText("Durduruluyor…")
        self._gunluk_ekle("⚠️ İptal sinyali gönderildi; mevcut adım güvenle sonlandırılıyor…")

    def _arayuzu_kilitle(self, kilitli: bool) -> None:
        self.sekmeler.setEnabled(not kilitli)
        self.birakma.setEnabled(not kilitli)
        for dugme in self._kuyruk_dugmeleri:
            dugme.setEnabled(not kilitli)
        self.btn_basla.setEnabled(not kilitli and self.liste.count() > 0)
        self.btn_basla.setText("İşleniyor…" if kilitli else "Başlat")
        self.btn_iptal.setEnabled(kilitli)
        self.btn_iptal.setText("İptal")
        if not kilitli:
            self.rozet_asama.guncelle("Hazır", "notr")

    # --- sinyal alıcıları ------------------------------------------------

    def _gunluk_ekle(self, mesaj: str) -> None:
        self.gunluk.satir_ekle(mesaj)

    def _ilerleme_guncelle(self, oran: float, etiket: str) -> None:
        self.cubuk.setValue(int(max(0.0, min(1.0, oran)) * 1000))
        self.lbl_durum.setText(etiket)
        self.lbl_yuzde.setText(f"%{oran * 100:.0f}" if oran > 0 else "")

    def _dosya_basladi(self, sira: int, toplam: int, yol: str) -> None:
        self._aktif_yol = yol
        self._oge_durumu(yol, "isleniyor")
        self.akis.temizle()
        self.akis.basliga_yaz(f"[{sira}/{toplam}] {os.path.basename(yol)}")
        self.setWindowTitle(f"[{sira}/{toplam}] {os.path.basename(yol)} — {UYGULAMA_ADI}")

    def _dosya_bitti(self, sira: int, basarili: bool, yazilanlar: list) -> None:
        yol = self._aktif_yol
        if basarili:
            # Biten dosya kuyruktan düşer; yoksa yeni bir dosya eklenip
            # «Başlat»a basıldığında eskiler baştan işlenir.
            self._ogeyi_sil(yol)
        else:
            self._oge_durumu(yol, "hata")
        if yazilanlar:
            self._son_ciktilar = list(yazilanlar)
            self.btn_klasor.setEnabled(True)

    def _is_tamamlandi(self, basarili: int, toplam: int, basarisiz: list) -> None:
        self._arayuzu_kilitle(False)
        self.setWindowTitle(f"{UYGULAMA_ADI} v{SURUM}")
        self.isci = None
        self._aktif_yol = ""
        self._bitis_bildirimi(basarili, toplam, basarisiz)

    def _bitis_bildirimi(self, basarili: int, toplam: int, basarisiz: list) -> None:
        """İş bitince kullanıcıyı uyarır.

        Uzun işlerde pencere arkada kalıyor; görev çubuğunu yakıp söndürmek
        (`alert`) kullanıcı başka bir şeyle uğraşırken de dikkat çeker, kutu da
        sonucu özetler.
        """
        QApplication.alert(self)

        if self._iptal_edildi_mi:
            self.rozet_asama.guncelle("Durduruldu", "notr")
            QMessageBox.information(
                self, "Durduruldu",
                f"İşlem durduruldu.\n\n{basarili}/{toplam} dosya tamamlanmıştı.")
        elif basarisiz:
            self.rozet_asama.guncelle(f"{len(basarisiz)} hata", "kotu")
            QMessageBox.warning(
                self, "Bitti — bazı dosyalar başarısız",
                f"{basarili}/{toplam} dosya tamamlandı.\n\n"
                f"Başarısız:\n• " + "\n• ".join(basarisiz) +
                "\n\nAyrıntı için Günlük sekmesine bakın.")
        elif toplam:
            self.rozet_asama.guncelle("Tamamlandı", "iyi")
            QMessageBox.information(
                self, "Bitti",
                f"{toplam} dosyanın tamamı işlendi.\n\n"
                f"Altyazılar kaydedildi.")


    # ----------------------------------------------------------------------
    # Yan işler
    # ----------------------------------------------------------------------

    def _donanimi_sorgula(self) -> None:
        sorgu = DonanimSorgusu(self.motor, self)
        sorgu.bitti.connect(
            lambda bilgi, gpu: self.rozet_gpu.guncelle(bilgi, "iyi" if gpu else "notr"))
        self._sorguyu_calistir(sorgu)

    def _ffmpegi_sorgula(self) -> None:
        sorgu = FFmpegSorgusu(self.txt_ffmpeg.text().strip(), self)
        sorgu.bitti.connect(
            lambda metin, iyi: self.rozet_ffmpeg.guncelle(metin, "iyi" if iyi else "kotu"))
        self._sorguyu_calistir(sorgu)

    def _sorguyu_calistir(self, sorgu: QThread) -> None:
        # Referansı tutmazsak iş parçacığı çöp toplayıcıya gidip çökmeye yol açar.
        self._sorgular.append(sorgu)
        sorgu.finished.connect(lambda: self._sorgular.remove(sorgu)
                               if sorgu in self._sorgular else None)
        sorgu.start()

    def _cikti_klasoru_sec(self) -> None:
        klasor = QFileDialog.getExistingDirectory(
            self, "Çıktı klasörünü seçin",
            self.txt_cikti.text() or self.ayarlar.son_klasor or os.path.expanduser("~"))
        if klasor:
            self.txt_cikti.setText(klasor)

    def _model_klasoru_sec(self) -> None:
        klasor = QFileDialog.getExistingDirectory(
            self, "Model klasörünü seçin",
            self.txt_model_klasor.text() or os.path.expanduser("~"))
        if klasor:
            self.txt_model_klasor.setText(klasor)

    def _ffmpeg_sec(self) -> None:
        yol, _ = QFileDialog.getOpenFileName(
            self, "ffmpeg.exe dosyasını seçin", "",
            "ffmpeg (ffmpeg.exe ffmpeg);;Tüm dosyalar (*.*)")
        if not yol:
            return
        if media.ffmpeg_surumu(yol) is None:
            QMessageBox.critical(
                self, "Geçersiz FFmpeg",
                "Seçilen dosya çalıştırılamadı ya da ffmpeg değil.\n\n"
                "ffmpeg.exe dosyasını seçtiğinizden emin olun "
                "(ffplay.exe / ffprobe.exe değil).")
            return
        self.txt_ffmpeg.setText(yol)

    def _cikti_klasorunu_ac(self) -> None:
        if self._son_ciktilar:
            self._klasoru_ac(os.path.dirname(self._son_ciktilar[0]))

    def _klasoru_ac(self, klasor: str) -> None:
        if not os.path.isdir(klasor):
            return
        try:
            if sys.platform == "win32":
                os.startfile(klasor)                    # noqa: S606
            elif sys.platform == "darwin":
                subprocess.Popen(["open", klasor])
            else:
                subprocess.Popen(["xdg-open", klasor])
        except Exception as e:
            self._gunluk_ekle(f"⚠️ Klasör açılamadı: {e}")

    # ----------------------------------------------------------------------

    def _pencereyi_gizle(self) -> None:
        """Pencereyi hemen ekrandan kaldırır.

        Süren bir iş iptal edilirken on saniyeye kadar beklenebiliyor; bu süre
        boyunca pencerenin donmuş hâlde durması uygulamanın kilitlendiği
        izlenimi veriyor. Önce gizleyip temizliği arkada yapıyoruz.
        """
        try:
            self.hide()
            QApplication.processEvents()
        except Exception:
            pass

    def closeEvent(self, olay) -> None:
        if self._calisiyor():
            cevap = QMessageBox.question(
                self, "İşlem sürüyor",
                "Bir işlem devam ediyor. Yine de çıkmak istiyor musunuz?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if cevap != QMessageBox.Yes:
                olay.ignore()
                return

        # Ayarlar her hâlükârda önce yazılsın: aşağıdaki adımlar takılırsa bile
        # kullanıcının ayarları kaybolmasın.
        self._widgetlardan_ayarlari_oku()
        self._pencereyi_gizle()
        geo = self.geometry()
        self.ayarlar.pencere = {"x": geo.x(), "y": geo.y(),
                                "g": geo.width(), "h": geo.height()}
        self.ayarlar.kaydet()

        if self._calisiyor():
            self._iptal_edildi_mi = True
            self.isci.iptal_et()
            # İptal ancak iki pencere arasında görülüyor; büyük modellerde tek
            # pencere on saniyeyi bulabildiği için cömert davranıyoruz.
            if not self.isci.wait(KAPANIS_BEKLEME_MS):
                self._gunluk_ekle("⚠️ İşlem verilen sürede durmadı; süreç yine de sonlandırılacak.")

        for sorgu in list(self._sorgular):
            sorgu.wait(3000)

        # Model GPU belleğini tutuyor; süreç ölmeden önce açıkça bırak.
        try:
            self.motor.bosalt()
        except Exception:
            pass

        olay.accept()


def uygulamayi_calistir() -> int:
    QApplication.setApplicationName(UYGULAMA_ADI)
    uygulama = QApplication(sys.argv)
    uygulama.setStyle("Fusion")
    uygulama.setStyleSheet(stil_sayfasi())

    pencere = AnaPencere()
    pencere.show()
    kod = uygulama.exec()

    # Pencere kapandıktan sonra hiçbir şey arkada kalmasın.
    #
    # Normal yorumlayıcı çıkışı burada takılabiliyor: iptal edilmiş bir işçi
    # hâlâ torch'un içindeyse ya da bir çeviri isteği zaman aşımını bekliyorsa,
    # `concurrent.futures` atexit'te havuz iş parçacıklarını join etmeye
    # çalışır ve süreç görünmez biçimde ayakta kalır — GPU belleği de onunla
    # birlikte. Ayarlar closeEvent'te zaten diske yazıldığı için süreci
    # doğrudan sonlandırmak güvenli; işletim sistemi VRAM ve RAM'i anında geri
    # alır.
    del pencere
    for akis in (sys.stdout, sys.stderr):
        try:
            akis.flush()
        except Exception:
            pass
    os._exit(kod)
