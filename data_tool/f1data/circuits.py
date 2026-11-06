"""Static lookup tables: circuit identifiers, layout history and team colours."""

from __future__ import annotations

OUTLINE_GEOJSON_URL = ("https://raw.githubusercontent.com/bacinger/f1-circuits/"
                       "master/f1-circuits.geojson")

# Jolpica circuitId -> (OpenF1 circuit_short_name or None, bacinger/f1-circuits id or None)
CIRCUITS: dict[str, tuple[str | None, str | None]] = {
    "albert_park": ("Melbourne", "au-1953"),
    "americas": ("Austin", "us-2012"),
    "bahrain": ("Sakhir", "bh-2002"),
    "baku": ("Baku", "az-2016"),
    "catalunya": ("Catalunya", "es-1991"),
    "hungaroring": ("Hungaroring", "hu-1986"),
    "imola": ("Imola", "it-1953"),
    "interlagos": ("Interlagos", "br-1940"),
    "istanbul": ("Istanbul", "tr-2005"),
    "jeddah": ("Jeddah", "sa-2021"),
    "losail": ("Lusail", "qa-2004"),
    "madring": ("Madring", "es-2026"),
    "marina_bay": ("Singapore", "sg-2008"),
    "miami": ("Miami", "us-2022"),
    "monaco": ("Monte Carlo", "mc-1929"),
    "monza": ("Monza", "it-1922"),
    "portimao": ("Algarve International Circuit", "pt-2008"),
    "red_bull_ring": ("Spielberg", "at-1969"),
    "rodriguez": ("Mexico City", "mx-1962"),
    "shanghai": ("Shanghai", "cn-2004"),
    "silverstone": ("Silverstone", "gb-1948"),
    "spa": ("Spa-Francorchamps", "be-1925"),
    "suzuka": ("Suzuka", "jp-1962"),
    "vegas": ("Las Vegas", "us-2023"),
    "villeneuve": ("Montreal", "ca-1978"),
    "yas_marina": ("Yas Marina Circuit", "ae-2009"),
    "zandvoort": ("Zandvoort", "nl-1948"),
    "estoril": (None, "pt-1972"),
    "galvez": (None, "ar-1952"),
    "hockenheimring": (None, "de-1932"),
    "indianapolis": (None, "us-1909"),
    "jacarepagua": (None, "br-1977"),
    "kyalami": (None, "za-1961"),
    "magny_cours": (None, "fr-1960"),
    "mugello": (None, "it-1914"),
    "nurburgring": (None, "de-1927"),
    "ricard": (None, "fr-1969"),
    "sepang": (None, "my-1999"),
    "sochi": (None, "ru-2014"),
    "watkins_glen": (None, "us-1956"),
}

# Races before this year used a noticeably different layout from the modern
# outline, so the package is flagged. Not exhaustive: every timing-only race
# also carries a general "layout may differ" note.
LAYOUT_CHANGED_BEFORE: dict[str, int] = {
    "albert_park": 2022,
    "bahrain": 2005,
    "catalunya": 2023,
    "hockenheimring": 2002,
    "hungaroring": 2003,
    "imola": 2008,
    "magny_cours": 2003,
    "marina_bay": 2023,
    "monza": 2000,
    "nurburgring": 2002,
    "red_bull_ring": 1997,
    "silverstone": 2011,
    "spa": 2007,
    "suzuka": 2003,
    "yas_marina": 2021,
}

# Single races run on an alternative layout of the same venue.
ALTERNATE_LAYOUT_RACES: set[tuple[int, str]] = {
    (2010, "bahrain"),   # 6.3 km "Endurance" layout
    (2020, "bahrain"),   # Sakhir GP on the outer layout (see layout_warning)
}

# Team colours for races with no colour in the source data (Jolpica has none).
# These are display choices only, not source data, and are labelled that way.
TEAM_COLOURS: dict[str, str] = {
    "alfa": "9B0000", "alphatauri": "2B4562", "alpine": "2293D1", "arrows": "F58220",
    "aston_martin": "229971", "audi": "BB0A30", "bar": "D8D8D8", "benetton": "00A651",
    "bmw_sauber": "6CA6DA", "brawn": "C8F000", "cadillac": "B5B5B5", "caterham": "1E7F4F",
    "force_india": "F58220", "ferrari": "E8002D", "footwork": "D0D0D0", "forti": "FFD800",
    "haas": "B6BABD", "honda": "E0E0E0", "hrt": "A08C5B", "jaguar": "006F3C",
    "jordan": "F2C200", "lola": "C00000", "lotus_f1": "B79A55", "lotus_racing": "1E7F4F",
    "ligier": "2A6EBB", "manor": "E0313A", "marussia": "B0141C", "mclaren": "FF8000",
    "mercedes": "27F4D2", "mf1": "C00000", "minardi": "D8D820",
    "prost": "2A5FBF", "racing_point": "F596C8", "rb": "6692FF", "red_bull": "3671C6",
    "renault": "FFD800", "sauber": "52E252", "spyker": "F26A21", "spyker_mf1": "F26A21",
    "stewart": "E0E0E0", "super_aguri": "E03C31", "toro_rosso": "469BFF", "toyota": "D5001C",
    "tyrrell": "2A5FBF", "virgin": "C8102E", "williams": "64C4FF",
}
DEFAULT_COLOUR = "C8C8C8"


def layout_warning(circuit_id: str, season: int, race_name: str) -> str | None:
    """Return a warning when this race ran on a layout unlike the available outline."""
    if (season, circuit_id) in ALTERNATE_LAYOUT_RACES and (
            season != 2020 or "Sakhir" in race_name):
        return f"{season} used an alternate layout; the outline shown is the standard one."
    changed = LAYOUT_CHANGED_BEFORE.get(circuit_id)
    if changed and season < changed:
        return f"Layout changed by {changed}; the outline shown is a later version."
    return None
