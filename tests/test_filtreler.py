"""Halüsinasyon savunması: bilinen artıklar, tekrar döngüleri, istem ekosu."""

from forge import whisper_engine as we


def seg(metin, bas=0.0, son=1.0):
    return {"text": metin, "start": bas, "end": son}


def test_bilinen_artiklar_yakalanir():
    assert we.halusinasyon_mu("Subtitles by the Amara.org community")
    assert we.halusinasyon_mu("  ¡Suscríbete al canal!  ")
    assert we.halusinasyon_mu("Thanks for watching!")
    assert not we.halusinasyon_mu("Bugün hava çok güzel.")
    assert not we.halusinasyon_mu("")


def test_halusinasyonlari_ele_sayar():
    temiz, atilan = we.halusinasyonlari_ele(
        [seg("Merhaba."), seg("Thank you for watching."), seg("Nasılsın?")])
    assert atilan == 1
    assert [s["text"] for s in temiz] == ["Merhaba.", "Nasılsın?"]


def test_tekrar_filtresi_ardisik_kopyalari_kirpar():
    segmentler = [seg("Hayır."), seg("hayır. "), seg("Hayır."), seg("Evet."), seg("Hayır.")]
    temiz, atilan, en_uzun = we.tekrar_filtrele(segmentler, limit=1)
    assert [s["text"] for s in temiz] == ["Hayır.", "Evet.", "Hayır."]
    assert atilan == 2
    assert en_uzun == 3


def test_bozuk_cikti_butun_dosya_dongusu():
    assert we.bozuk_cikti_mi([seg("aynı")] * 10)


def test_bozuk_cikti_kismi_cokus_kayan_pencereyle_yakalanir():
    saglikli = [seg(f"Farklı cümle {i}.") for i in range(40)]
    cokus = [seg("Hayır hayır.")] * 8
    assert we.bozuk_cikti_mi(saglikli + cokus)


def test_saglikli_cikti_kisa_tekrarlara_ragmen_bozuk_sayilmaz():
    metinler = [seg(f"Cümle {i}.") for i in range(30)]
    metinler[10:13] = [seg("Teşekkür ederim!")] * 3
    assert not we.bozuk_cikti_mi(metinler)


def test_az_segmentte_karar_verilmez():
    assert not we.bozuk_cikti_mi([seg("aynı")] * 5)


def test_istem_ekosu_tam_ve_kismi():
    istem = "Le volgarità è un bel po' di tutto, parolacce comprese."
    assert we.istem_ekosu_mu(istem, istem)
    assert we.istem_ekosu_mu("Le volgarità è un bel po' troppo", istem)
    # Yalnızca sıradan kısa kelimeler örtüşüyorsa alarm yok.
    assert not we.istem_ekosu_mu("è un gatto", istem)
    assert not we.istem_ekosu_mu("qualsiasi cosa", None)


def test_zamanlari_duzelt_tek_yonlu_yapar():
    segmentler = [
        {"text": "a", "start": 0.0, "end": 2.0,
         "words": [{"start": 0.0, "end": 3.0}, {"start": 0.5, "end": 1.0}]},
        {"text": "b", "start": 1.5, "end": 1.0},           # geriye gidiyor
        {"text": "c", "start": float("nan"), "end": None},
    ]
    we.zamanlari_duzelt(segmentler)
    assert segmentler[0]["words"][0]["end"] == 2.0
    assert segmentler[0]["words"][1]["start"] == 2.0
    assert segmentler[1]["start"] == 2.0 and segmentler[1]["end"] > 2.0
    assert segmentler[2]["start"] >= segmentler[1]["end"]
    for s in segmentler:
        assert s["end"] > s["start"]
