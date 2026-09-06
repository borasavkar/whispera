@echo off
rem Whispera'u tek klasorluk bir uygulamaya derler.
rem Sonuc: dist\Whispera\Whispera.exe

cd /d "%~dp0"

if not exist ".venv\Scripts\pyinstaller.exe" (
    echo PyInstaller bulunamadi. Once kurun:
    echo     .venv\Scripts\python.exe -m pip install pyinstaller
    pause
    exit /b 1
)

tasklist /FI "IMAGENAME eq Whispera.exe" 2>nul | find /I "Whispera.exe" >nul
if not errorlevel 1 (
    echo Whispera.exe calisiyor - derleme dosyanin uzerine yazamaz.
    echo Uygulamayi kapatip tekrar deneyin.
    pause
    exit /b 1
)

echo Derleniyor... ROCm calisma zamani buyuk oldugu icin birkac dakika surebilir.
".venv\Scripts\pyinstaller.exe" Whispera.spec --noconfirm --clean

if errorlevel 1 (
    echo.
    echo DERLEME BASARISIZ.
    pause
    exit /b 1
)

rem Modeller derlemeye dahil edilmiyor (gigabaytlarca yer kaplar ve her
rem derlemede yeniden kopyalanir). Bunun yerine proje kokundeki models
rem klasorune bir kavsak (junction) baglaniyor: aninda olusur, yer kaplamaz,
rem exe onu kendi yanindaki "models" klasoru gibi gorur.
if exist "models" (
    if not exist "dist\Whispera\models" (
        mklink /J "dist\Whispera\models" "models" >nul
        if errorlevel 1 (
            echo UYARI: models kavsagi olusturulamadi, kopyalaniyor...
            xcopy /E /I /Q /Y "models" "dist\Whispera\models" >nul
        ) else (
            echo Modeller baglandi: dist\Whispera\models -^> models
        )
    )
)

echo.
echo Bitti: dist\Whispera\Whispera.exe
echo.
echo NOT: Baska bir makineye tasiyacaksaniz models klasorunu gercek kopya
echo      olarak alin (kavsak degil): dist\Whispera\models icine kopyalayin.
pause
