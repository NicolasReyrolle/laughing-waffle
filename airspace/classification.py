"""Classification and parsing of OpenAIP airspace types, classes, and limits."""

from typing import Any
from airspace.constants import AIRSPACE_TYPE_MAP, ICAO_CLASS_MAP


def parse_vertical_limit(limit_obj: dict[str, Any] | None) -> tuple[float, str, bool]:
    """Convert an OpenAIP vertical limit object to meters, formatted label, and AGL flag.

    Args:
        limit_obj: OpenAIP limit dictionary containing 'value', 'unit'
            (0: m, 1: ft, 6: FL), and 'referenceDatum' (0: GND/AGL, 1: MSL, 2: STD),
            or None if unbounded/surface.

    Returns:
        A tuple of:
            - meters (float): Altitude or height converted to meters.
            - label (str): Formatted human-readable label (e.g. 'SFC', 'FL 095', '1500 ft AGL').
            - is_agl (bool): True if reference datum is GND (Above Ground Level), False otherwise.
    """
    if not limit_obj:
        return 0.0, "SFC", True

    value = float(limit_obj.get("value", 0))
    unit = limit_obj.get("unit", 1)  # 0: m, 1: ft, 6: FL
    datum = limit_obj.get("referenceDatum", 1)  # 0: GND (AGL), 1: MSL, 2: STD

    is_agl = datum == 0
    datum_str = {0: "AGL", 1: "AMSL", 2: "STD"}.get(datum, "AMSL")

    if value == 0 and is_agl:
        return 0.0, "SFC", True

    if unit == 6:  # Flight Level
        meters = value * 100.0 * 0.3048
        label = f"FL {int(value):03d}"
    elif unit == 0:  # Meters
        meters = value
        label = f"{int(value)} m {datum_str}"
    else:  # Feet (default unit == 1)
        meters = value * 0.3048
        label = f"{int(value)} ft {datum_str}"

    return round(meters, 1), label, is_agl


def _is_h24_schedule(oh: Any) -> bool:
    """Determine whether operating hours represent a permanent H24 schedule.

    Args:
        oh: Operating hours structure from OpenAIP (string such as "H24", or a list
            of day schedule dictionaries).

    Returns:
        True if the schedule is permanently active H24 without NOTAM activation,
        False if conditional or restricted.
    """
    if isinstance(oh, str):
        return oh.upper() == "H24"
    if isinstance(oh, list) and oh:
        return len(oh) == 7 and all(
            d.get("startTime") == "00:00"
            and d.get("endTime") == "00:00"
            and not d.get("byNotam")
            for d in oh
        )
    return True


def is_activatable_airspace(airspace: dict[str, Any]) -> bool:
    """Detect whether an airspace is conditional/activatable rather than permanent H24.

    Checks flags (byNotam, onDemand, onRequest), operating hours schedules,
    and presence of conditional keywords (e.g. HX, NOTAM, ACTIVABLE, SUP AIP, AZBA)
    in name, remarks, and activity text.

    Args:
        airspace: OpenAIP airspace dictionary.

    Returns:
        True if the airspace is conditional/activatable, False if permanent or uncontrolled.
    """
    if airspace.get("byNotam") or airspace.get("onDemand") or airspace.get("onRequest"):
        return True

    hours = airspace.get("hoursOfOperation")
    if isinstance(hours, dict):
        oh = hours.get("operatingHours")
        if not _is_h24_schedule(oh):
            return True

    text_blob = " ".join(
        str(airspace.get(field, "")) for field in ("name", "remarks", "activity")
    ).upper()
    conditional_keywords = ("HX", "NOTAM", "ACTIVABLE", "SUP AIP", "AZBA", "BY NOTAM")
    return any(kw in text_blob for kw in conditional_keywords)


def _classify_controlled(
    airspace: dict[str, Any], asp_type: str, icao_class: str
) -> str:
    """Classify controlled or restricted airspace into RED or ORANGE category.

    Args:
        airspace: OpenAIP airspace dictionary.
        asp_type: Normalized airspace type code (e.g. 'CTR', 'TMA', 'CTA').
        icao_class: Normalized ICAO class code (e.g. 'A', 'B', 'C', 'D', 'E', 'G').

    Returns:
        Category string: 'RED' for strictly controlled/prohibited H24 airspaces,
        or 'ORANGE' for activatable/conditional or lower-class controlled airspaces.
    """
    if is_activatable_airspace(airspace):
        return "ORANGE"
    if asp_type in ("TMA", "CTA", "MTMA"):
        return "RED" if icao_class in ("A", "B", "C", "D") else "ORANGE"
    if icao_class in ("A", "B", "C", "D") or asp_type in ("P", "CTR"):
        return "RED"
    return "ORANGE"


def classify_airspace(
    airspace: dict[str, Any],
    include_other: bool = False,
    excluded_types: set[str] | None = None,
) -> str | None:
    """Assign a color category (RED, ORANGE, YELLOW, BLUE) or None if irrelevant.

    Filters out high-level navigation/FIR regions, optionally filters 'OTHER' types
    and user-specified excluded types, then categorizes airspaces into visual styles
    suitable for paragliding VFR navigation.

    Args:
        airspace: OpenAIP airspace dictionary.
        include_other: Whether to include airspaces with type 'OTHER'. Defaults to False.
        excluded_types: Optional set of uppercase airspace type strings to ignore.

    Returns:
        Color category name ('RED', 'ORANGE', 'YELLOW', 'BLUE'), or None if excluded/irrelevant.
    """
    asp_type = AIRSPACE_TYPE_MAP.get(airspace.get("type", 0), "OTHER")
    icao_class = ICAO_CLASS_MAP.get(airspace.get("icaoClass", 8), "SUA")

    if asp_type in ("FIR", "UIR", "ACC", "ADIZ"):
        return None
    if asp_type == "OTHER" and not include_other:
        return None
    if excluded_types and asp_type in excluded_types:
        return None
    if icao_class == "E":
        return "BLUE"
    if asp_type in (
        "RMZ",
        "TMZ",
        "ATZ",
        "MATZ",
        "HTZ",
        "TIZ",
        "TIA",
        "GLIDING",
        "SPORT",
    ):
        return "YELLOW"
    if asp_type in ("R", "D", "TRA", "TSA", "MTR", "ALERT", "WARNING", "PROTECTED"):
        return "ORANGE"
    return _classify_controlled(airspace, asp_type, icao_class)
