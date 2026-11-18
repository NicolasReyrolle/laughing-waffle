"""Constants and configuration settings for OpenAIP airspace processing."""

from typing import Any

import requests as _requests

try:
    from curl_cffi import requests as _curl_requests  # type: ignore[import-untyped,import-not-found]

    requests: Any = _curl_requests
    _curl_cffi_available = True
except ImportError:
    requests: Any = _requests  # type: ignore[no-redef]
    _curl_cffi_available = False

HAVE_CURL_CFFI: bool = _curl_cffi_available

OPENAIP_AIRSPACES_URL = "https://api.core.openaip.net/api/airspaces"
NOMINATIM_SEARCH_URL = "https://nominatim.openstreetmap.org/search"
OPEN_METEO_ELEVATION_URL = "https://api.open-meteo.com/v1/elevation"

# OpenAIP ICAO class integer codes
ICAO_CLASS_MAP: dict[int, str] = {
    0: "A",
    1: "B",
    2: "C",
    3: "D",
    4: "E",
    5: "F",
    6: "G",
    8: "SUA",
}

# OpenAIP airspace type integer codes
AIRSPACE_TYPE_MAP: dict[int, str] = {
    0: "OTHER",
    1: "R",
    2: "D",
    3: "P",
    4: "CTR",
    5: "TMZ",
    6: "RMZ",
    7: "TMA",
    8: "TRA",
    9: "TSA",
    10: "FIR",
    11: "UIR",
    12: "ADIZ",
    13: "ATZ",
    14: "MATZ",
    15: "AIRWAY",
    16: "MTR",
    17: "ALERT",
    18: "WARNING",
    19: "PROTECTED",
    20: "HTZ",
    21: "GLIDING",
    22: "TRP",
    23: "TIZ",
    24: "TIA",
    25: "MTMA",
    26: "CTA",
    27: "ACC",
    28: "SPORT",
    29: "LOW_ALT_RESTRICTION",
}

# Color definitions (hex RGB + KML folder label)
CATEGORY_STYLES: dict[str, dict[str, str]] = {
    "RED": {
        "folder": "1. Rouge - Interdit / Controle H24 (CTR, P, TMA A-D)",
        "hex": "#E53935",
    },
    "ORANGE": {
        "folder": "2. Orange - Activable / Reglemente (R, D, TMA HX/NOTAM)",
        "hex": "#FB8C00",
    },
    "YELLOW": {
        "folder": "3. Jaune - Vigilance / Radio-Transpondeur (RMZ, TMZ, ATZ, Sport)",
        "hex": "#FDD835",
    },
    "BLUE": {
        "folder": "4. Bleu - Classe E (VFR autorise, trafic IFR)",
        "hex": "#1E88E5",
    },
}

__all__ = [
    "AIRSPACE_TYPE_MAP",
    "CATEGORY_STYLES",
    "HAVE_CURL_CFFI",
    "ICAO_CLASS_MAP",
    "NOMINATIM_SEARCH_URL",
    "OPENAIP_AIRSPACES_URL",
    "OPEN_METEO_ELEVATION_URL",
    "requests",
]
