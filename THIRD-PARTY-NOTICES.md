# Üçüncü taraf bileşenler ve lisanslar

Whispera'nın **kendi kaynak kodu MIT** lisanslıdır (bkz. `LICENSE`).

Bu dosya, hazır `.exe` dağıtımının içine giren üçüncü taraf bileşenleri ve
onların lisanslarını listeler. Yalnızca kaynak koddan çalıştırıyorsanız bu
yükümlülüklerin çoğu sizi ilgilendirmez — kütüphaneleri siz kendi ortamınıza
kuruyorsunuz demektir.

> Bu bir hukuki görüş değildir. Ticari dağıtım yapacaksanız bir hukukçuya
> danışın.

## Özet

| Bileşen | Sürüm | Lisans | Not |
|---|---|---|---|
| PySide6 / Qt 6 | 6.11.2 | **LGPL-3.0** | Aşağıya bakın — tek dikkat gerektiren bileşen |
| shiboken6 | 6.11.2 | LGPL-3.0 | PySide6 ile aynı |
| openai-whisper | 20250625 | MIT | |
| PyTorch | 2.13.0+rocm10.0.0 | BSD-3-Clause (+ Apache-2.0, MIT, BSL-1.0) | |
| torchaudio / torchvision | 2.11 / 0.28 | BSD-3-Clause | |
| ROCm SDK (AMD) | 10.0.0 | MIT / BSD / NCSA karışımı | AMD GPU çalışma zamanı |
| NumPy | 2.5.2 | BSD-3-Clause | |
| numba / llvmlite | 0.67 / 0.49 | BSD-2-Clause, Apache-2.0 WITH LLVM-exception | |
| requests | 2.34.2 | Apache-2.0 | |
| deep-translator | 1.11.4 | MIT | |
| tiktoken, regex | — | MIT, Apache-2.0 | |
| sympy, networkx, fsspec, idna | — | BSD-3-Clause | |
| urllib3, filelock, MarkupSafe, beautifulsoup4 | — | MIT / BSD | |
| certifi | 2026.7.22 | **MPL-2.0** | Aşağıya bakın |
| tqdm | 4.70.0 | **MPL-2.0** ve MIT | Aşağıya bakın |

## PySide6 / Qt — LGPL-3.0

Qt, Whispera'da **LGPL-3.0** koşullarıyla kullanılıyor. LGPL'in dağıtımda
istediği üç şey var:

**1. Bildirim.** Qt'nin LGPL-3.0 altında kullanıldığı belirtilmeli ve lisans
metni dağıtıma eklenmeli. Bu dosya birinci şartı karşılıyor; `.exe` paketine
LGPL-3.0 metnini de koyun (https://www.gnu.org/licenses/lgpl-3.0.txt).

**2. Kullanıcı Qt'yi değiştirebilmeli (relinking).** Whispera PyInstaller'ın
**onedir** kipiyle paketleniyor: Qt kütüphaneleri `_internal/PySide6/` altında
ayrı `.dll` dosyaları olarak duruyor (`Qt6Core.dll`, `Qt6Gui.dll`,
`Qt6Widgets.dll`, `Qt6Network.dll`, `Qt6OpenGL.dll`). Kullanıcı bunları uyumlu
bir sürümle değiştirebilir, dolayısıyla bu şart sağlanıyor.

> **Onefile kipine geçmeyin.** Tek dosyalık `.exe`'de Qt DLL'leri paketin içine
> gömülür ve değiştirilemez hâle gelir; bu, LGPL uyumunu bozar.

**3. Qt kaynak koduna erişim.** Qt'yi değiştirmediyseniz yukarı akış kaynağına
işaret etmek yeterli: https://download.qt.io/official_releases/QtForPython/

## MPL-2.0 bileşenleri (certifi, tqdm)

MPL-2.0 dosya düzeyinde copyleft'tir: bu kütüphaneleri **değiştirmediğiniz**
sürece tek yükümlülük lisansı bildirmektir. Değiştirirseniz, değiştirdiğiniz
dosyaların kaynağını yayımlamanız gerekir. Whispera bu kütüphaneleri olduğu
gibi kullanıyor.

## ffmpeg dağıtıma dahil DEĞİL

Whispera ses çıkarmak için ffmpeg kullanır ama **ffmpeg'i paketlemez**;
sistemdeki ya da kullanıcının Temel sekmesinden gösterdiği ffmpeg çağrılır.
Bu yüzden ffmpeg'in lisansı (GPL ya da LGPL, derlemesine göre değişir)
Whispera dağıtımını bağlamaz. ffmpeg'i `.exe` ile birlikte dağıtmaya karar
verirseniz durum değişir; o zaman kullandığınız ffmpeg derlemesinin lisansını
ayrıca incelemeniz gerekir.

## Whisper model ağırlıkları

Model dosyaları (`large-v3-turbo` vb.) dağıtıma dahil değildir; ilk
çalıştırmada OpenAI'dan indirilir. openai-whisper ve model ağırlıkları MIT
lisanslıdır.

## Release'e `.exe` koyarken kontrol listesi

- [ ] `LICENSE` (Whispera — MIT) pakete eklendi
- [ ] `THIRD-PARTY-NOTICES.md` (bu dosya) pakete eklendi
- [ ] LGPL-3.0 lisans metni pakete eklendi
- [ ] Paketleme **onedir** kipinde (Qt DLL'leri ayrı dosya olarak duruyor)
- [ ] `forge_settings.json` pakete **girmedi** (API anahtarı içerir)
