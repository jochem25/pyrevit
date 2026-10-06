# -*- coding: utf-8 -*-
"""Locatie-logica zonder Revit (CPython of IronPython).

RD -> ProjectPosition en Project Info-waarden na een (mislukte) PDOK-bevraging.

    python extensions/GIS2BIM.extension/tests/test_location_mapping.py
"""
import os

# Rechtstreeks laden: het package-__init__ importeert pyrevit
_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..", "lib", "gis2bim", "revit", "location.py",
)
try:
    import importlib.util as _ilu

    _spec = _ilu.spec_from_file_location("gis2bim_location", _PATH)
    _mod = _ilu.module_from_spec(_spec)
    _spec.loader.exec_module(_mod)
except ImportError:  # IronPython 2.7
    import imp

    _mod = imp.load_source("gis2bim_location", _PATH)
project_position_args = _mod.project_position_args
build_project_info_values = _mod.build_project_info_values
ONBEKEND = _mod.ONBEKEND

FT = 0.3048
TOL = 1e-6


def test_rd_x_is_eastwest_rd_y_is_northsouth():
    # 5008 Zeekant 37 Den Haag
    east_ft, north_ft, elev_ft, angle = project_position_args(78653.0, 458298.0)
    assert abs(east_ft * FT - 78653.0) < TOL, east_ft
    assert abs(north_ft * FT - 458298.0) < TOL, north_ft
    assert elev_ft == 0.0
    assert angle == 0.0


def test_elevation_and_angle():
    _, _, elev_ft, angle = project_position_args(1.0, 2.0, 3.0, 180.0)
    assert abs(elev_ft * FT - 3.0) < TOL
    assert abs(angle - 3.141592653589793) < TOL


# Wat Locatie Instellen meegeeft als PDOK niets oplevert (minimale data)
MINIMAAL = {
    "rd_x": 78653.96,
    "rd_y": 458298.52,
    "gemeente": "2586 AB Den Haag",
    "postcode": "2586AB",
    "provincie": "",
    "windgebied": "",
    "kadaster_gemeente": "",
    "kadaster_sectie": "",
    "kadaster_perceel": "",
}


def test_mislukte_bevraging_markeert_in_plaats_van_overslaan():
    waarden, onbekend = build_project_info_values(MINIMAAL, "Zeekant", "37")
    for veld in (
        "GIS2BIM_Provincie",
        "GIS2BIM_Windgebied",
        "GIS2BIM_Kadaster_Gemeente",
        "GIS2BIM_Kadaster_Sectie",
        "GIS2BIM_Kadaster_Perceel",
    ):
        assert waarden[veld] == ONBEKEND, (veld, waarden[veld])
        assert veld in onbekend, veld
    assert waarden["GIS2BIM_Straat"] == "Zeekant"
    assert waarden["GIS2BIM_RD_X"] == "78653"
    assert waarden["GIS2BIM_RD_Y"] == "458298"


def test_none_wordt_onbekend_niet_tekst_none():
    data = dict(MINIMAAL, windgebied=None, kadaster_perceel=None)
    waarden, _ = build_project_info_values(data)
    assert waarden["GIS2BIM_Windgebied"] == ONBEKEND
    assert waarden["GIS2BIM_Kadaster_Perceel"] == ONBEKEND


def test_geslaagde_bevraging_heeft_geen_onbekend():
    data = dict(
        MINIMAAL,
        gemeente="'s-Gravenhage",
        provincie="Zuid-Holland",
        windgebied=2,
        kadaster_gemeente="GVH23",
        kadaster_sectie="AF",
        kadaster_perceel="2487",
    )
    waarden, onbekend = build_project_info_values(data, "Zeekant", "37")
    assert onbekend == [], onbekend
    assert waarden["GIS2BIM_Provincie"] == "Zuid-Holland"
    assert waarden["GIS2BIM_Windgebied"] == "2"


if __name__ == "__main__":
    test_rd_x_is_eastwest_rd_y_is_northsouth()
    test_elevation_and_angle()
    test_mislukte_bevraging_markeert_in_plaats_van_overslaan()
    test_none_wordt_onbekend_niet_tekst_none()
    test_geslaagde_bevraging_heeft_geen_onbekend()
    print("OK")
