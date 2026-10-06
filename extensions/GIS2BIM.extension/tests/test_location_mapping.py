# -*- coding: utf-8 -*-
"""Mapping RD -> ProjectPosition (draait buiten Revit, CPython of IronPython).

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


if __name__ == "__main__":
    test_rd_x_is_eastwest_rd_y_is_northsouth()
    test_elevation_and_angle()
    print("OK")
