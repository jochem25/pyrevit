# -*- coding: utf-8 -*-
"""Proef: BAG-contour + 3DBAG-hoogte + RVO-kentallen -> IFC4 met echte ramen.

Geen Revit nodig. Uitvoer is te linken in Revit of te openen in elke viewer.
"""
import json
import math

import numpy as np
from shapely import wkt
from shapely.geometry import Point

import ifcopenshell
import ifcopenshell.api.root
import ifcopenshell.api.unit
import ifcopenshell.api.context
import ifcopenshell.api.spatial
import ifcopenshell.api.aggregate
import ifcopenshell.api.geometry
import ifcopenshell.api.feature

run = ifcopenshell.api.run

# --- RVO-kentallen: aandeel raam / deur van het buitengeveloppervlak ---------
# Uitgelezen uit data-voorbeeldwoningen-2022.xlsx, tabblad "(med) <type> <periode>",
# rijen 33 (dichte gevel) / 43 (ramen) / 52 (deuren), kolom G.
KENTAL = {
    ("rij tuss", "tot46"): (0.287, 0.094),
    ("rij tuss", "46-64"): (0.308, 0.075),
    ("rij tuss", "65-74"): (0.371, 0.068),
    ("rij tuss", "75-91"): (0.300, 0.075),
    ("rij hoek", "tot46"): (0.184, 0.067),
    ("vrij", "tot46"): (0.169, 0.048),
    ("gestapeld", "tot46"): (0.293, 0.161),
    ("gestapeld", "65-74"): (0.443, 0.139),
}
STANDAARD = (0.25, 0.07)

WAND_DIKTE = 0.25
RAAM_BREEDTE = 1.30
BORSTWERING = 0.90
DEUR_B = 1.00
DEUR_H = 2.30


def periode(jaar):
    j = jaar or 1930
    for grens, naam in ((1946, "tot46"), (1965, "46-64"), (1975, "65-74"),
                        (1992, "75-91"), (2006, "92-05"), (2015, "06-14")):
        if j < grens:
            return naam
    return "15-18"


def kental(typ, jaar):
    return (KENTAL.get((typ, periode(jaar)))
            or KENTAL.get((typ, "tot46"))
            or STANDAARD)


def matrix(oorsprong, hoek):
    """4x4 plaatsingsmatrix: X langs de wand, Z omhoog."""
    c, s = math.cos(hoek), math.sin(hoek)
    return np.array([[c, -s, 0.0, oorsprong[0]],
                     [s, c, 0.0, oorsprong[1]],
                     [0.0, 0.0, 1.0, oorsprong[2]],
                     [0.0, 0.0, 0.0, 1.0]], dtype=float)


def is_bouwmuur(p0, p1, eigen, buren):
    """Segment dat tegen een buurpand aan ligt -> blinde bouwmuur."""
    mid = ((p0[0] + p1[0]) / 2.0, (p0[1] + p1[1]) / 2.0)
    dx, dy = p1[0] - p0[0], p1[1] - p0[1]
    lang = math.hypot(dx, dy) or 1.0
    nx, ny = dy / lang, -dx / lang
    for teken in (1, -1):
        pt = Point(mid[0] + teken * 0.4 * nx, mid[1] + teken * 0.4 * ny)
        if eigen.contains(pt):
            continue
        for b in buren:
            if b.distance(pt) < 0.01:
                return True
    return False


def bouw():
    f = ifcopenshell.file(schema="IFC4")
    run("root.create_entity", f, ifc_class="IfcProject",
        name="GIS2BIM contextpanden")
    run("unit.assign_unit", f, length={"is_metric": True, "raw": "METERS"})
    ctx = run("context.add_context", f, context_type="Model")
    body = run("context.add_context", f, context_type="Model",
               context_identifier="Body", target_view="MODEL_VIEW", parent=ctx)

    site = run("root.create_entity", f, ifc_class="IfcSite", name="Zeekant e.o.")
    gebouw = run("root.create_entity", f, ifc_class="IfcBuilding",
                 name="Bouwblok")
    laag = run("root.create_entity", f, ifc_class="IfcBuildingStorey",
               name="Maaiveld")
    run("aggregate.assign_object", f, products=[site],
        relating_object=f.by_type("IfcProject")[0])
    run("aggregate.assign_object", f, products=[gebouw], relating_object=site)
    run("aggregate.assign_object", f, products=[laag], relating_object=gebouw)

    panden = json.load(open("blok.json"))
    for p in panden:
        p["geom"] = wkt.loads(p["geom"])

    ox = sum(p["geom"].centroid.x for p in panden) / len(panden)
    oy = sum(p["geom"].centroid.y for p in panden) / len(panden)
    print("lokale oorsprong RD: %.2f, %.2f" % (ox, oy))

    stat = {"panden": 0, "wanden": 0, "bouwmuren": 0, "ramen": 0, "deuren": 0,
            "glas_m2": 0.0, "gevel_m2": 0.0, "doel_glas_m2": 0.0,
            "overgeslagen": 0}

    for p in panden:
        geom = p["geom"]
        basis = p["maaiveld"]
        kandidaten = [v for v in (p.get("h50"), p.get("nok"))
                      if v and v > basis + 2]
        if not kandidaten:
            stat["overgeslagen"] += 1
            continue
        top = min(kandidaten)
        hoogte = top - basis
        lagen = p.get("lagen") or max(1, int(round(hoogte / 3.0)))
        laaghoogte = hoogte / lagen
        raam_pct, _deur_pct = kental(p["type"], p["bouwjaar"])

        buren = [q["geom"] for q in panden if q is not p]
        ring = list(geom.exterior.coords)[:-1]
        segmenten = [(ring[i], ring[(i + 1) % len(ring)])
                     for i in range(len(ring))]

        buiten = []
        for p0, p1 in segmenten:
            lang = math.hypot(p1[0] - p0[0], p1[1] - p0[1])
            if lang < 0.5:
                continue
            buiten.append((p0, p1, lang,
                           is_bouwmuur(p0, p1, geom, buren)))

        open_segmenten = [s for s in buiten if not s[3]]
        voorgevel = max(open_segmenten, key=lambda s: s[2], default=None)
        stat["panden"] += 1

        for p0, p1, lang, blind in buiten:
            hoek = math.atan2(p1[1] - p0[1], p1[0] - p0[0])
            plaats = matrix((p0[0] - ox, p0[1] - oy, basis), hoek)

            wand = run("root.create_entity", f, ifc_class="IfcWall",
                       name="%s %s" % (p["id"],
                                       "bouwmuur" if blind else "gevel"))
            rep = run("geometry.add_wall_representation", f, context=body,
                      length=lang, height=hoogte, thickness=WAND_DIKTE)
            run("geometry.assign_representation", f, product=wand,
                representation=rep)
            run("geometry.edit_object_placement", f, product=wand,
                matrix=plaats)
            run("spatial.assign_container", f, products=[wand],
                relating_structure=laag)
            stat["wanden"] += 1

            if blind:
                stat["bouwmuren"] += 1
                continue

            stat["gevel_m2"] += lang * hoogte
            doel = lang * hoogte * raam_pct
            stat["doel_glas_m2"] += doel

            raam_h = min(1.60, laaghoogte - 1.30)
            if raam_h < 0.6:
                continue

            per_laag = doel / lagen
            aantal = max(1, int(round(per_laag / (RAAM_BREEDTE * raam_h))))
            breedte = per_laag / (aantal * raam_h)
            max_breedte = (lang - 0.6) / aantal - 0.4
            if breedte > max_breedte:
                aantal = max(1, int((lang - 0.6) / (RAAM_BREEDTE + 0.4)))
                breedte = min(per_laag / (aantal * raam_h),
                              (lang - 0.6) / aantal - 0.4)
            if breedte < 0.6:
                continue
            steek = lang / aantal

            for n in range(lagen):
                vloer = n * laaghoogte
                for i in range(aantal):
                    x = steek * (i + 0.5) - breedte / 2.0
                    if x < 0.3 or x + breedte > lang - 0.3:
                        continue
                    deur = (n == 0 and voorgevel is not None
                            and p0 == voorgevel[0] and p1 == voorgevel[1]
                            and i == 0)
                    b = DEUR_B if deur else breedte
                    h = DEUR_H if deur else raam_h
                    z = vloer + (0.02 if deur else BORSTWERING)
                    pos = matrix((p0[0] - ox + math.cos(hoek) * x,
                                  p0[1] - oy + math.sin(hoek) * x,
                                  basis + z), hoek)

                    gat = run("root.create_entity", f,
                              ifc_class="IfcOpeningElement", name="opening")
                    grep = run("geometry.add_wall_representation", f,
                               context=body, length=b, height=h,
                               thickness=WAND_DIKTE + 0.4, offset=-0.2)
                    run("geometry.assign_representation", f, product=gat,
                        representation=grep)
                    run("geometry.edit_object_placement", f, product=gat,
                        matrix=pos)
                    run("feature.add_feature", f, feature=gat, element=wand)

                    vul = run("root.create_entity", f,
                              ifc_class="IfcDoor" if deur else "IfcWindow",
                              name="deur" if deur else "raam")
                    vrep = run("geometry.add_wall_representation", f,
                               context=body, length=b, height=h,
                               thickness=0.06, offset=0.09)
                    run("geometry.assign_representation", f, product=vul,
                        representation=vrep)
                    run("geometry.edit_object_placement", f, product=vul,
                        matrix=pos)
                    run("feature.add_filling", f, opening=gat, element=vul)
                    run("spatial.assign_container", f, products=[vul],
                        relating_structure=laag)

                    if deur:
                        stat["deuren"] += 1
                    else:
                        stat["ramen"] += 1
                        stat["glas_m2"] += b * h

    f.write("contextpanden.ifc")

    print("")
    print("--- resultaat ---")
    for k in ("panden", "overgeslagen", "wanden", "bouwmuren", "ramen",
              "deuren"):
        print("  %-14s %s" % (k, stat[k]))
    print("  %-14s %.0f m2" % ("gevel", stat["gevel_m2"]))
    print("  %-14s %.0f m2 (doel %.0f)" % ("glas", stat["glas_m2"],
                                           stat["doel_glas_m2"]))
    if stat["gevel_m2"]:
        print("  %-14s %.1f%% (doel %.1f%%)" % (
            "glasaandeel", 100 * stat["glas_m2"] / stat["gevel_m2"],
            100 * stat["doel_glas_m2"] / stat["gevel_m2"]))


if __name__ == "__main__":
    bouw()
