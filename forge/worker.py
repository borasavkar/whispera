"""Arka planda çalışan iş yürütücüsü.

Arayüz iş parçacığı hiçbir zaman bloklanmasın diye bütün ağır işler burada,
tek bir `QThread` içinde sırayla koşar. Dışarıya yalnızca sinyal gönderilir;
arayüz de yalnızca `iptal_et()` çağırır.

Bir dosyanın ilerleme ekseni aşağıdaki gibi bölünür — kullanıcı ilerleme
çubuğuna bakıp hangi aşamada olduğunu anlayabilsin diye aşama etiketi de
birlikte gider.
"""

from __future__ import annotations

import os
import threading
import time
import traceback

from PySide6.QtCore import QThread, Signal

from dataclasses import replace

from .config import Ayarlar, UYGULAMA_KLASORU
from . import media, segment, subtitles, translate
from .whisper_engine import (
    BellekYetersiz,
    IstemEkosu,
    KullaniciIptali,
    WhisperMotoru,
    bellek_hatasi_mi,
    bos_segmentleri_ele,
    bozuk_cikti_mi,
    halusinasyonlari_ele,
    istem_ekosu_mu,
    tekrar_filtrele,
    zamanlari_duzelt,
)

# Bir dosyanın ilerleme ekseni: (aşama_başı, aşama_sonu)
ASAMA = {
    "ses": (0.00, 0.05),
    "model": (0.05, 0.08),
    "dil": (0.08, 0.14),
    "metin": (0.14, 0.74),
    "yazma": (0.74, 0.78),
    "ceviri": (0.78, 1.00),
}

DIL_GUVEN_ESIGI = 0.75

# Bu kadar blok üst üste başlangıç isteminin tekrarıysa işlem baştan alınır.
ISTEM_EKO_SINIRI = 3

# Tekrar döngüsü tespit edilince yeniden denemede kullanılacak en az ışın sayısı.
DONGU_KIRICI_ISIN = 12


def _cesitlilik(sonuc: dict) -> float:
    """Sonuçtaki benzersiz metin oranı; iki denemeyi kıyaslamak için."""
    metinler = [" ".join((x.get("text") or "").split()).casefold()
                for x in (sonuc.get("segments") or [])
                if (x.get("text") or "").strip()]
    return len(set(metinler)) / len(metinler) if metinler else 0.0


class Isci(QThread):
    """Kuyruktaki dosyaları sırayla işler."""

    gunluk = Signal(str)                    # terminal satırı
    ilerleme = Signal(float, str)           # 0..1, etiket
    segment = Signal(float, float, str)     # başlangıç, bitiş, metin
    dosya_basladi = Signal(int, int, str)   # sıra, toplam, yol
    dosya_bitti = Signal(int, bool, list)   # sıra, başarılı mı, yazılan dosyalar
    tamamlandi = Signal(int, int, list)     # başarılı, toplam, başarısız adlar
    durum_degisti = Signal(str)             # kısa aşama adı (rozet için)
    akis_temizle = Signal()                 # canlı akış baştan alınacak

    def __init__(self, dosyalar: list[str], ayarlar: Ayarlar,
                 motor: WhisperMotoru, parent=None):
        super().__init__(parent)
        self._dosyalar = list(dosyalar)
        self._ayarlar = ayarlar
        self._motor = motor
        self._iptal = threading.Event()
        self._ceviri_tabani = 0.0

    # --- dışarıya açık ---------------------------------------------------

    def iptal_et(self) -> None:
        self._iptal.set()

    def iptal_edildi(self) -> bool:
        return self._iptal.is_set()

    # --- yardımcılar -----------------------------------------------------

    def _log(self, mesaj: str) -> None:
        self.gunluk.emit(mesaj)

    def _asama_ilerleme(self, asama: str, oran: float, etiket: str) -> None:
        bas, son = ASAMA[asama]
        self.ilerleme.emit(bas + (son - bas) * max(0.0, min(1.0, oran)), etiket)

    def _ceviri_ilerleme(self, oran: float, etiket: str) -> None:
        """Çeviri aşamasında çubuk geri sarmasın.

        Çeviri birden çok turdan geçiyor (paket → onarım → yedek → yerel) ve her
        tur kendi içinde 0'dan başlıyor. Kullanıcıya tek bir ilerleme göstermek
        için o ana kadarki en yüksek değeri taban alırız.
        """
        self._ceviri_tabani = max(self._ceviri_tabani, max(0.0, min(1.0, oran)))
        self._asama_ilerleme("ceviri", self._ceviri_tabani, etiket)

    def _iptal_kontrol(self) -> None:
        if self._iptal.is_set():
            raise KullaniciIptali()

    # --- ana döngü -------------------------------------------------------

    def run(self) -> None:                                  # QThread giriş noktası
        toplam = len(self._dosyalar)
        basarili = 0
        basarisiz: list[str] = []
        t_kuyruk = time.time()

        if toplam > 1:
            self._log("=" * 52)
            self._log(f"📋 KUYRUK BAŞLADI — {toplam} dosya sırayla işlenecek.")

        for sira, dosya in enumerate(self._dosyalar, 1):
            if self._iptal.is_set():
                break

            self.dosya_basladi.emit(sira, toplam, dosya)
            self._log("")
            self._log("=" * 52)
            self._log(f"📼 [{sira}/{toplam}] {os.path.basename(dosya)}")
            self._log("=" * 52)

            try:
                yazilanlar = self._bir_dosya(dosya)
                if yazilanlar:
                    basarili += 1
                    self.dosya_bitti.emit(sira, True, yazilanlar)
                else:
                    if not self._iptal.is_set():
                        basarisiz.append(os.path.basename(dosya))
                    self.dosya_bitti.emit(sira, False, [])
            except KullaniciIptali:
                self._log("🛑 İşlem kullanıcı tarafından durduruldu.")
                self.dosya_bitti.emit(sira, False, [])
                break
            except BellekYetersiz as e:
                # Kullanıcının çözebileceği bir durum; yığın izi göstermenin
                # anlamı yok, öneriler yeterli.
                self._log("❌ GPU BELLEĞİ YETMEDİ")
                for satir in str(e).splitlines():
                    self._log(f"   {satir}")
                self.ilerleme.emit(0.0, "GPU belleği yetmedi.")
                self._motor.bosalt()
                basarisiz.append(os.path.basename(dosya))
                self.dosya_bitti.emit(sira, False, [])
            except Exception as e:
                if bellek_hatasi_mi(e):
                    self._log("❌ GPU belleği yetmedi (işlem sırasında).")
                    self._log("   Daha küçük bir model deneyin ya da işlem birimini CPU yapın.")
                    self.ilerleme.emit(0.0, "GPU belleği yetmedi.")
                    self._motor.bosalt()
                else:
                    self._hatayi_raporla(e)
                basarisiz.append(os.path.basename(dosya))
                self.dosya_bitti.emit(sira, False, [])

        self._log("")
        self._log("=" * 52)
        if self._iptal.is_set():
            self._log(f"🛑 DURDURULDU — {basarili}/{toplam} dosya tamamlandı "
                      f"({media.sure_metni(time.time() - t_kuyruk)}).")
            self.ilerleme.emit(0.0, "İptal edildi.")
        else:
            self._log(f"🏁 BİTTİ — {basarili}/{toplam} dosya başarılı "
                      f"({media.sure_metni(time.time() - t_kuyruk)}).")
        if basarisiz:
            self._log(f"   ❌ Başarısız: {', '.join(basarisiz)}")

        # Kuyruk bittiğinde model belleği bırakır. Bellekte tutmak sonraki işi
        # hızlandırırdı ama boşta dururken gigabaytlarca VRAM'i tutmak buna
        # değmiyor; bir sonraki iş modeli yeniden yükler.
        if self._motor.bosalt():
            self._log("🧹 Model bellekten atıldı; bellek serbest.")

        self.durum_degisti.emit("Hazır")
        self.tamamlandi.emit(basarili, toplam, basarisiz)

    # --- tek dosya -------------------------------------------------------

    def _bir_dosya(self, dosya: str) -> list[str]:
        """Bir dosyayı baştan sona işler; yazılan dosya yollarını döndürür."""
        ayarlar = self._ayarlar
        t_dosya = time.time()

        if not os.path.isfile(dosya):
            self._log("❌ Dosya bulunamadı, atlanıyor.")
            return []

        # --- 1. Ses -------------------------------------------------------
        self.durum_degisti.emit("Ses çözülüyor")
        ffmpeg_yolu = media.ffmpeg_coz(ayarlar.ffmpeg_yolu)
        if not ffmpeg_yolu:
            self._log("❌ FFmpeg bulunamadı; ses çözülemiyor.")
            return []

        self._asama_ilerleme("ses", 0.0, "Ses çözülüyor…")
        self._log(f"🎬 Ses çözülüyor ({os.path.basename(ffmpeg_yolu)})…")
        ses = media.sesi_coz(dosya, ffmpeg_yolu)
        toplam_sure = len(ses) / media.ORNEKLEME_HIZI
        self._log(f"   Süre: {media.sure_metni(toplam_sure)}")
        self._asama_ilerleme("ses", 1.0, "Ses hazır.")
        self._iptal_kontrol()

        # --- 2. Model -----------------------------------------------------
        self.durum_degisti.emit("Model yükleniyor")
        self._asama_ilerleme("model", 0.0, "Model yükleniyor…")
        cihaz = self._motor.modeli_hazirla(
            ayarlar, ilerleme=lambda o, e: self._asama_ilerleme("model", o, e))
        self._asama_ilerleme("model", 1.0, f"Model hazır ({cihaz.upper()}).")
        self._iptal_kontrol()

        # --- 3. Dil -------------------------------------------------------
        dil = self._dili_belirle(ses, cihaz)
        self._iptal_kontrol()

        # --- 4. Metne dönüştürme -----------------------------------------
        self.durum_degisti.emit("Metne dönüştürülüyor")
        istem_dili = "en" if ayarlar.gorev == "translate" else (dil or "")
        istem = ayarlar.etkin_istem(istem_dili)
        if istem:
            kaynak = "kendi isteminiz" if (ayarlar.initial_prompt or "").strip() else "sansürsüz kalıp"
            self._log(f"💬 Başlangıç istemi kullanılıyor ({kaynak}).")

        self._log("🎙️ Sesler metne dönüştürülüyor…")
        t_metin = time.time()
        sonuc = self._metne_donustur(ses, dil, istem, cihaz)

        tespit_edilen = sonuc.get("language") or dil or "auto"
        segmentler = sonuc.get("segments") or []
        self._log(f"✅ Metne dönüştürme bitti: {len(segmentler)} segment "
                  f"({media.sure_metni(time.time() - t_metin)})")
        self._iptal_kontrol()

        # --- 5. Temizlik --------------------------------------------------
        if istem:
            # Üç ardışık blok istemin tekrarıysa işlem baştan alınıyor; ama tek
            # başına sızan bir blok o eşiği tetiklemiyor ve çıktıya giriyor
            # («Le volgarità è un bel po'» gibi). Onları burada eliyoruz.
            once = len(segmentler)
            segmentler = [x for x in segmentler
                          if not istem_ekosu_mu(x.get("text") or "", istem)]
            if once - len(segmentler):
                self._log(f"🧽 {once - len(segmentler)} blok başlangıç isteminin "
                          f"sızıntısıydı, ayıklandı.")

        segmentler, artik = halusinasyonlari_ele(segmentler)
        if artik:
            self._log(f"🧽 {artik} satır bilinen altyazı artığıydı "
                      f"(«Subtitles by…», «Transcription by…»), ayıklandı.")

        if ayarlar.tekrar_filtresi:
            segmentler, atilan, en_uzun = tekrar_filtrele(segmentler, ayarlar.tekrar_limiti)
            if atilan:
                self._log(f"⚠️ Tekrar döngüsü: aynı metin arka arkaya {en_uzun} kez üretilmiş; "
                          f"{atilan} blok ayıklandı.")

        segmentler = bos_segmentleri_ele(zamanlari_duzelt(segmentler))
        sonuc["segments"] = segmentler

        # --- 5b. Akıllı bölümleme -----------------------------------------
        if segmentler and ayarlar.akilli_bolumleme_acik():
            onceki = len(segmentler)
            sonuc, uygulandi, elenen = segment.sonuca_uygula(
                sonuc, **ayarlar.bolumleme_secenekleri())
            if uygulandi:
                segmentler = sonuc["segments"]
                self._log(f"✂️ Bloklar kelime zamanlarına göre yeniden kuruldu: "
                          f"{onceki} → {len(segmentler)} blok.")
                if elenen > 0:
                    self._log(f"   {elenen} blok konuşma hızı akla yatkın olmadığı "
                              f"için elendi (sessizliğe iliştirilmiş uydurma).")
            else:
                self._log("ℹ️ Kelime zamanı üretilmediği için akıllı bölümleme atlandı.")

        if not segmentler:
            self._log("⚠️ Hiç konuşma çıkarılamadı — dosya yazılmadı.")
            self.ilerleme.emit(0.0, "Konuşma bulunamadı.")
            return []

        # --- 6. Yazma -----------------------------------------------------
        self.durum_degisti.emit("Dosyalar yazılıyor")
        self._asama_ilerleme("yazma", 0.3, "Altyazı dosyaları yazılıyor…")

        ceviri_kaynagi = "en" if ayarlar.gorev == "translate" else tespit_edilen
        ceviri_yapilacak = (
            ayarlar.turkce_ceviri
            and (ceviri_kaynagi or "").lower() != "tr"
            and not self._iptal.is_set()
        )

        ek_ad = f"_{(ceviri_kaynagi or 'src').upper()}" if ceviri_yapilacak else ""
        yazilanlar = subtitles.guvenli_yaz(
            sonuc, dosya, ayarlar.cikti_formati, ayarlar.cikti_klasoru,
            ayarlar.yazici_secenekleri(), ek_ad, self._log)

        if not yazilanlar:
            return []
        for yol in yazilanlar:
            self._log(f"💾 {os.path.basename(yol)}")
        self._asama_ilerleme("yazma", 1.0, "Altyazı yazıldı.")

        # --- 7. Türkçe çeviri --------------------------------------------
        if ceviri_yapilacak:
            yazilanlar += self._turkceye_cevir(sonuc, dosya, ceviri_kaynagi)
        elif ayarlar.turkce_ceviri and (ceviri_kaynagi or "").lower() == "tr":
            self._log("ℹ️ Kaynak zaten Türkçe; ayrıca çeviri dosyası üretilmedi.")

        if not self._iptal.is_set():
            self._log(f"🎉 Tamamlandı ({media.sure_metni(time.time() - t_dosya)}).")
            self.ilerleme.emit(1.0, f"Tamamlandı — {media.sure_metni(time.time() - t_dosya)}")

        return yazilanlar

    # --- metne dönüştürme ------------------------------------------------

    def _metne_donustur(self, ses, dil, istem, cihaz) -> dict:
        """Transkripsiyonu yapar; istem çıktıyı bozarsa istemsiz tekrarlar.

        Whisper `initial_prompt`'u metnin devamı gibi işliyor. Konuşmanın seyrek
        olduğu ya da müziğin bastırdığı dosyalarda model istemin kendisini
        üretmeye başlayabiliyor; `condition_on_previous_text` açıkken de bu
        döngü bir daha kırılmıyor ve saatlik bir film tek cümlenin tekrarı
        olarak çıkıyor. Burada iki ağ var: akış sırasında erken yakalama ve
        bittikten sonra çeşitlilik denetimi.
        """
        sonuc = self._tek_gecis(ses, dil, istem, cihaz)

        if bozuk_cikti_mi(sonuc.get("segments") or []):
            # Not: bu denetim eskiden yalnızca istem varken çalışıyordu, ama
            # tekrar döngüsü istemden bağımsız da oluşuyor — kod çözme kendi
            # içinde dejenere bir hipoteze kilitleniyor. Gerçek bir örnekte
            # dosyanın ilk iki dakikası kusursuzken kalan beş dakikası tek bir
            # «Hayır hayır.» tekrarıydı ve hiç istem yoktu.
            if istem:
                self._log("⚠️ Çıktı tekrar döngüsüne düşmüş; başlangıç istemi olmadan ve "
                          "«önceki metne koşullanma» kapalı hâlde yeniden deneniyor…")
            else:
                self._log("⚠️ Çıktı tekrar döngüsüne düşmüş; «önceki metne koşullanma» "
                          "kapalı ve arama genişletilmiş hâlde yeniden deneniyor…")
            self.akis_temizle.emit()
            yeniden = self._tek_gecis(ses, dil, None, cihaz, dongu_kirici=True)

            # Yeniden deneme de çökerse elde daha iyi olanı bırakırız; ikisi de
            # kötüyse en azından daha çeşitli olanı seçmiş oluruz.
            if bozuk_cikti_mi(yeniden.get("segments") or []):
                self._log("   ⚠️ İkinci deneme de tekrar döngüsüne düştü; "
                          "iki sonuçtan daha çeşitli olanı kullanılıyor.")
                if _cesitlilik(yeniden) <= _cesitlilik(sonuc):
                    return sonuc
            return yeniden

        return sonuc

    def _tek_gecis(self, ses, dil, istem, cihaz, dongu_kirici: bool = False) -> dict:
        """Tek bir transkripsiyon geçişi.

        `dongu_kirici` açıkken `condition_on_previous_text` kapatılır. Whisper
        bunu kendi belgelerinde tekrar döngüleri için öneriyor: pencereler arası
        tutarlılık biraz düşer ama model bir kez takıldığında oradan çıkabilir.
        Yalnızca döngü tespit edilip yeniden denenirken devreye girer.
        """
        t_metin = time.time()
        ayarlar = self._ayarlar
        if dongu_kirici:
            # İki kol birlikte: önceki metne koşullanmayı kapatmak whisper'ın
            # kendi önerdiği çıkış yolu; ışın sayısını artırmak ise aramayı
            # genişletip dejenere hipoteze kilitlenmeyi zorlaştırıyor
            # (Kasai vd. 2022, arXiv:2204.05424 — beam araması `beam_size`
            # kadar tamamlanmış aday bulur bulmaz duruyor).
            ayarlar = replace(
                ayarlar,
                condition_on_previous_text=False,
                beam_size=max(int(ayarlar.beam_size or 1) * 2, DONGU_KIRICI_ISIN))
        eko = {"ardisik": 0}

        def _ilerleme(oran: float) -> None:
            self._iptal_kontrol()
            if eko["ardisik"] >= ISTEM_EKO_SINIRI:
                raise IstemEkosu()
            gecen = time.time() - t_metin
            kalan = (gecen / oran - gecen) if oran > 0.02 else 0
            etiket = f"Metne dönüştürülüyor: %{oran * 100:.0f}"
            if kalan > 1:
                etiket += f" · ~{media.sure_metni(kalan)} kaldı"
            self._asama_ilerleme("metin", oran, etiket)

        def _segment(bas: float, son: float, metin: str) -> None:
            if istem_ekosu_mu(metin, istem):
                eko["ardisik"] += 1
            else:
                eko["ardisik"] = 0
            self.segment.emit(bas, son, metin)

        try:
            return self._motor.cevir_metne(
                ses, ayarlar, dil, istem, cihaz,
                ilerleme=_ilerleme,
                segment_geldi=_segment,
                bilgi_satiri=lambda satir: self._log(f"   {satir}"),
                iptal=self._iptal.is_set,
            )
        except IstemEkosu:
            self._log(f"⚠️ Model başlangıç istemini tekrarlamaya başladı "
                      f"({ISTEM_EKO_SINIRI} blok üst üste); istemsiz yeniden başlatılıyor.")
            self.akis_temizle.emit()
            return self._tek_gecis(ses, dil, None, cihaz, dongu_kirici=True)

    # --- aşamalar --------------------------------------------------------

    def _dili_belirle(self, ses, cihaz: str) -> str | None:
        ayarlar = self._ayarlar

        if ayarlar.dil != "auto":
            self._log(f"✅ Dil elle seçildi: {ayarlar.dil.upper()}")
            return ayarlar.dil

        if not ayarlar.dil_oylamasi:
            self._log("🔎 Dil tespiti whisper'a bırakıldı (ilk 30 saniye).")
            return None

        self.durum_degisti.emit("Dil tespit ediliyor")
        self._log("🔎 Dil tespit ediliyor (dosyanın geneline yayılmış örneklerle)…")
        dil, guven, oylar = self._motor.dil_oyla(
            ses, ayarlar.dil_oylama_pencere,
            iptal=self._iptal.is_set,
            ilerleme=lambda o, e: self._asama_ilerleme("dil", o, e),
        )

        if not dil:
            return None

        siralama = sorted(oylar.items(), key=lambda x: x[1], reverse=True)
        self._log("   Oylar: " + "  ".join(f"{d.upper()}:{a:.2f}" for d, a in siralama[:4]))
        self._log(f"✅ Dil: {dil.upper()} (güven %{guven * 100:.1f})")
        if guven < DIL_GUVEN_ESIGI:
            self._log(f"⚠️ Dil tespiti zayıf (%{guven * 100:.1f}); "
                      f"sonuç beklediğiniz gibi değilse dili elle seçin.")
        return dil

    def _turkceye_cevir(self, sonuc: dict, dosya: str, kaynak_dil: str) -> list[str]:
        ayarlar = self._ayarlar
        self.durum_degisti.emit("Türkçeye çevriliyor")
        self._log("=" * 52)
        self._log("🌐 Altyazı Türkçeye çevriliyor…")

        if not translate.google_destekliyor_mu(kaynak_dil):
            self._log(f"⚠️ «{kaynak_dil}» Google Çeviri'de tanınmadı; otomatik algılamaya geçiliyor.")
            kaynak_dil = "auto"

        self._ceviri_tabani = 0.0
        motor = translate.CeviriMotoru(
            log=self._log,
            ilerleme=self._ceviri_ilerleme,
            iptal=self._iptal.is_set,
            sansursuz=ayarlar.sansursuz,
            deepl_anahtari=ayarlar.deepl_anahtari,
            gemini_anahtari=ayarlar.gemini_anahtari,
            gemini_modeli=ayarlar.gemini_modeli,
        )

        bloklar = [{"start": s["start"], "end": s["end"], "text": s.get("text") or ""}
                   for s in sonuc["segments"]]
        try:
            ceviriler = motor.cevir(bloklar, kaynak_dil)
        except KullaniciIptali:
            self._log("🛑 Çeviri yarıda kesildi; o ana kadar çevrilenler kaydedilemedi.")
            return []

        if self._iptal.is_set():
            self._log("🛑 Çeviri yarıda kesildi; o ana kadar çevrilenler kaydediliyor.")

        secenekler = ayarlar.bolumleme_secenekleri()
        if motor.cumle_gruplari:
            # Tercih edilen yol: çeviri cümle bazında, kendi zaman aralığına
            # yayılarak yazılır. Kaynak blok sınırlarına paylaştırmak Türkçede
            # kopuk parçalar üretiyor.
            tr_sonuc, cevrilemeyen = subtitles.cevrilmis_sonuc_cumleden(
                sonuc, bloklar, motor.cumle_gruplari, motor.cumle_ceviriler,
                max_karakter=secenekler["max_karakter"],
                max_sure=secenekler["max_sure"],
                satir_genisligi=secenekler["satir_genisligi"],
                max_satir=secenekler["max_satir"])
            toplam = len(motor.cumle_gruplari)
            birim = "cümle"
        else:
            tr_sonuc = subtitles.cevrilmis_sonuc(sonuc, ceviriler, "tr",
                                                 ayarlar.satir_max_karakter)
            cevrilemeyen = len(bloklar) - len(ceviriler)
            toplam = len(bloklar)
            birim = "satır"

        yazilanlar = subtitles.guvenli_yaz(
            tr_sonuc, dosya, ayarlar.cikti_formati, ayarlar.cikti_klasoru,
            None, "", self._log)          # çeviri metninde kelime zamanı yok

        for yol in yazilanlar:
            self._log(f"🇹🇷 {os.path.basename(yol)}")
        self._log(f"   {len(tr_sonuc.get('segments') or [])} Türkçe blok yazıldı.")

        if cevrilemeyen > 0 and not self._iptal.is_set():
            self._log(f"⚠️ {cevrilemeyen}/{toplam} {birim} çevrilemedi; "
                      f"o yerlerde orijinal metin bırakıldı.")
            if motor.son_hata:
                self._log(f"   Son hata: {motor.son_hata}")

        return yazilanlar

    # --- hata ------------------------------------------------------------

    def _hatayi_raporla(self, e: Exception) -> None:
        self._log(f"❌ KRİTİK HATA: {e.__class__.__name__}: {e}")
        yol = os.path.join(UYGULAMA_KLASORU, "HATA_RAPORU.txt")
        try:
            with open(yol, "w", encoding="utf-8") as f:
                f.write(traceback.format_exc())
            self._log(f"   Ayrıntılar: {os.path.basename(yol)}")
        except Exception:
            pass
        self.ilerleme.emit(0.0, "Hata oluştu.")
