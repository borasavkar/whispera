"""Sürüm bilgisinin tek kaynağı."""

SURUM = "2.3.0"
UYGULAMA_ADI = "Whispera"


def surum_metni(kisa: bool = False) -> str:
    return f"v{SURUM}" if kisa else f"{UYGULAMA_ADI} v{SURUM}"
