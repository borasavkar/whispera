"""Altyazı/metin dosyalarının yazılması.

Orijinal çıktı için whisper'ın kendi yazıcıları (`whisper.utils.get_writer`)
kullanılır — böylece srt/vtt/tsv/json ve `--highlight_words`,
`--max_line_width` gibi seçenekler CLI ile birebir aynı sonucu verir.

Türkçe çeviri çıktısı bizim ürettiğimiz metinden geldiği için kelime zaman
damgalarıyla eşleşmez; orada `words` alanı düşürülür ve satır kırma işini biz
yaparız.
"""

from __future__ import annotations

import copy
import os
from typing import Any, Callable

FORMAT_UZANTILARI = ("srt", "vtt", "txt", "tsv", "json", "jsonl")


def istenen_formatlar(cikti_formati: str) -> list[str]:
    if cikti_formati == "all":
        return list(FORMAT_UZANTILARI)
    return [cikti_formati]


def hedef_klasor(medya_yolu: str, cikti_klasoru: str) -> str:
    """Boş ayar = kaynak dosyanın yanı."""
    klasor = (cikti_klasoru or "").strip()
    if not klasor:
        return os.path.dirname(os.path.abspath(medya_yolu))
    os.makedirs(klasor, exist_ok=True)
    return klasor


def yaz(sonuc: dict, medya_yolu: str, cikti_formati: str, cikti_klasoru: str,
        secenekler: dict[str, Any] | None = None, ek_ad: str = "") -> list[str]:
    """Sonucu istenen formatlarda yazar; yazılan dosya yollarını döndürür.

    `ek_ad` dosya adının sonuna eklenir ("Film" + "_EN" → "Film_EN.srt").
    """
    from whisper.utils import get_writer

    klasor = hedef_klasor(medya_yolu, cikti_klasoru)
    taban = os.path.splitext(os.path.basename(medya_yolu))[0] + (ek_ad or "")

    # Yazıcılar dosya adını verilen yolun taban adından türetiyor; uzantı
    # önemsiz olduğu için sahte bir yol yeterli.
    sahte_yol = os.path.join(klasor, taban + ".medya")

    yazilanlar: list[str] = []
    for format_adi in istenen_formatlar(cikti_formati):
        yazici = get_writer(format_adi, klasor)
        yazici(sonuc, sahte_yol, secenekler or {})
        yazilanlar.append(os.path.join(klasor, f"{taban}.{format_adi}"))
    return yazilanlar


def satir_kir(metin: str, max_karakter: int = 42) -> str:
    """Uzun bir altyazı satırını dengeli 2-3 satıra böler.

    Kelime ortasından bölmez; satır uzunluklarını hedefe yaklaştırmak için her
    kelimede «bu kelimeyi eklemek hedefe yaklaştırıyor mu» diye bakar.
    """
    metin = " ".join((metin or "").split())
    if len(metin) <= max_karakter:
        return metin

    kelimeler = metin.split(" ")
    if len(kelimeler) < 2:
        return metin

    satir_sayisi = min(3, max(2, -(-len(metin) // max_karakter)))
    satir_sayisi = min(satir_sayisi, len(kelimeler))
    hedef = len(metin) / satir_sayisi

    satirlar: list[str] = []
    mevcut: list[str] = []
    for kelime in kelimeler:
        if not mevcut:
            mevcut.append(kelime)
            continue
        simdiki = " ".join(mevcut)
        aday = f"{simdiki} {kelime}"
        son_satir = len(satirlar) == satir_sayisi - 1
        if not son_satir and abs(len(aday) - hedef) > abs(len(simdiki) - hedef):
            satirlar.append(simdiki)
            mevcut = [kelime]
        else:
            mevcut.append(kelime)
    if mevcut:
        satirlar.append(" ".join(mevcut))

    return "\n".join(satirlar)


def cevrilmis_sonuc(sonuc: dict, ceviriler: dict[int, str], dil: str = "tr",
                    max_karakter: int = 42) -> dict:
    """Segment metinleri çevirileriyle değiştirilmiş yeni bir sonuç sözlüğü.

    Çevirisi olmayan indeksler orijinal metinle bırakılır. `words` alanı
    düşürülür: çeviri kelime kelime hizalanmadığı için kelime zamanlarına
    dayanan yazıcı yolu yanlış sonuç verirdi.
    """
    yeni = copy.deepcopy(sonuc)
    segmentler = []
    metinler = []

    for i, segment in enumerate(yeni.get("segments") or []):
        metin = ceviriler.get(i)
        if metin is None or not str(metin).strip():
            metin = segment.get("text") or ""
        metin = satir_kir(str(metin).strip(), max_karakter)
        if not metin.strip():
            continue
        segment["text"] = metin
        segment.pop("words", None)
        segment["id"] = len(segmentler)
        segmentler.append(segment)
        metinler.append(metin.replace("\n", " "))

    yeni["segments"] = segmentler
    yeni["text"] = " ".join(metinler)
    yeni["language"] = dil
    return yeni


def cevrilmis_sonuc_cumleden(sonuc: dict, bloklar: list[dict],
                             cumle_gruplari: list[list[int]],
                             cumle_ceviriler: dict[int, str],
                             *, max_karakter: int = 84, max_sure: float = 6.0,
                             satir_genisligi: int = 42, max_satir: int = 2,
                             dil: str = "tr") -> tuple[dict, int]:
    """Çeviriyi cümle bazında, kendi zaman aralığına yayarak yazar.

    Eski yol çeviriyi kaynak bloklara karakter oranıyla paylaştırıyordu. Türkçe
    kelime sırası kaynak dilden farklı olduğu için bu «…biliyorsun, değil» /
    «mi?» gibi kopuk parçalar üretiyordu; akıllı bölümleme blokları kısaltınca
    iyice belirginleşti.

    Burada cümle bir bütün olarak ele alınır: grubun ilk bloğunun başlangıcı ile
    son bloğunun bitişi arasına Türkçe metnin kendisi yayılır.

    Döndürür: (sonuç, çevrilemeyen_cümle_sayısı)
    """
    from . import segment

    yeni_bloklar: list[dict] = []
    cevrilemeyen = 0

    for indeks, grup in enumerate(cumle_gruplari):
        if not grup:
            continue
        bas = float(bloklar[grup[0]]["start"])
        son = float(bloklar[grup[-1]]["end"])

        metin = cumle_ceviriler.get(indeks)
        if not metin or not str(metin).strip():
            # Çevrilemeyen cümlede orijinal metin kalsın; boş bırakmak
            # altyazıda delik açar.
            metin = " ".join((bloklar[i].get("text") or "") for i in grup)
            if metin.strip():
                cevrilemeyen += 1

        yeni_bloklar.extend(segment.metni_zamana_yay(
            bas, son, str(metin),
            max_karakter=max_karakter, max_sure=max_sure,
            satir_genisligi=satir_genisligi, max_satir=max_satir))

    yeni = dict(sonuc)
    yeni["segments"] = [
        {"id": i, "seek": 0, "start": b["start"], "end": b["end"], "text": b["text"]}
        for i, b in enumerate(yeni_bloklar)
    ]
    yeni["text"] = " ".join(b["text"].replace("\n", " ") for b in yeni_bloklar)
    yeni["language"] = dil
    return yeni, cevrilemeyen


def bos_mu(sonuc: dict) -> bool:
    return not any((s.get("text") or "").strip() for s in sonuc.get("segments") or [])


def guvenli_yaz(sonuc: dict, medya_yolu: str, cikti_formati: str, cikti_klasoru: str,
                secenekler: dict[str, Any] | None = None, ek_ad: str = "",
                log: Callable[[str], None] | None = None) -> list[str]:
    """`yaz` ile aynı, ama tek tek format hatalarında işlemi kesmez."""
    log = log or (lambda mesaj: None)
    if bos_mu(sonuc):
        log("⛔ Yazılabilir altyazı satırı çıkmadı; dosya oluşturulmadı.")
        return []
    try:
        return yaz(sonuc, medya_yolu, cikti_formati, cikti_klasoru, secenekler, ek_ad)
    except Exception as e:
        log(f"❌ Çıktı yazılamadı ({e.__class__.__name__}: {e}).")
        return []
