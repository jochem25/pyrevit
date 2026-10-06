# -*- coding: utf-8 -*-
"""Perceelkeuze bij een adres (CPython of IronPython).

    python extensions/GIS2BIM.extension/tests/test_perceelkeuze.py         # offline
    python extensions/GIS2BIM.extension/tests/test_perceelkeuze.py --live  # + PDOK
"""
import os
import sys
import types

_LIB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lib")

# Package-stubs: het echte gis2bim-__init__ importeert pyrevit
for _naam, _pad in (
    ("gis2bim", os.path.join(_LIB, "gis2bim")),
    ("gis2bim.api", os.path.join(_LIB, "gis2bim", "api")),
):
    _mod = types.ModuleType(_naam)
    _mod.__path__ = [_pad]
    sys.modules[_naam] = _mod

import importlib  # noqa: E402

pdok = importlib.import_module("gis2bim.api.pdok")

VIERKANT = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0), (0.0, 0.0)]
GEKOPPELD_5008 = ["GVH23-AF-3974", "GVH23-AF-2487"]


def _perceel(nummer, ring, gemeente="GVH23", sectie="AF"):
    return pdok.PerceelData(
        geometry=ring, perceelnummer=nummer, sectie=sectie, gemeentecode=gemeente
    )


def test_punt_in_polygoon():
    assert pdok.punt_in_polygoon(5.0, 5.0, VIERKANT)
    assert not pdok.punt_in_polygoon(15.0, 5.0, VIERKANT)
    assert not pdok.punt_in_polygoon(5.0, -0.1, VIERKANT)


def test_perceel_onder_punt_wint_van_gekoppeld():
    naast = [(20.0, 0.0), (30.0, 0.0), (30.0, 10.0), (20.0, 10.0), (20.0, 0.0)]
    percelen = [_perceel(3974, naast), _perceel(2487, VIERKANT)]
    keuze = pdok.kies_perceel(5.0, 5.0, percelen, GEKOPPELD_5008)
    assert keuze == ("GVH23", "AF", "2487", pdok.PERCEEL_BRON_WFS), keuze


def test_tegenproef_zonder_wfs_terugval_met_bron():
    keuze = pdok.kies_perceel(5.0, 5.0, [], GEKOPPELD_5008)
    assert keuze == ("GVH23", "AF", "3974", pdok.PERCEEL_BRON_GEKOPPELD), keuze


def test_niets_bekend():
    assert pdok.kies_perceel(5.0, 5.0, [], []) == ("", "", "", "")


def live_5008():
    """Zeekant 37 Den Haag: verwacht AF 2487 via de kadastrale kaart."""
    r = pdok.PDOKLocatie().search_postcode("2586AB", "37")
    keuze = (r.kadaster_gemeente, r.kadaster_sectie, r.kadaster_perceel)
    assert keuze == ("GVH23", "AF", "2487"), keuze
    assert r.perceel_bron == pdok.PERCEEL_BRON_WFS, r.perceel_bron
    print("live 5008: {0} ({1})".format(keuze, r.perceel_bron))


if __name__ == "__main__":
    test_punt_in_polygoon()
    test_perceel_onder_punt_wint_van_gekoppeld()
    test_tegenproef_zonder_wfs_terugval_met_bron()
    test_niets_bekend()
    if "--live" in sys.argv:
        live_5008()
    print("OK")
