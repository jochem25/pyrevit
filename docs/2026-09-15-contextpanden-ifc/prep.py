# -*- coding: utf-8 -*-
"""Haal BAG-contouren + 3DBAG-hoogtes op en classificeer de panden."""
import json, urllib.request, collections
from shapely.geometry import shape

BBOX = (78620, 458180, 78760, 458300)   # klein blok bij Zeekant, Den Haag


def haal(url, naam):
    req = urllib.request.Request(url, headers={"User-Agent": "GIS2BIM-proef/1.0"})
    d = json.loads(urllib.request.urlopen(req, timeout=60).read().decode("utf-8"))
    print("%-8s %d features" % (naam, len(d["features"])))
    return d


bag = haal(
    "https://service.pdok.nl/lv/bag/wfs/v2_0?service=WFS&version=2.0.0"
    "&request=GetFeature&typeName=bag:pand&bbox=%s,%s,%s,%s,EPSG:28992"
    "&count=1000&outputFormat=application/json" % BBOX, "BAG")

d3 = haal(
    "https://data.3dbag.nl/api/BAG3D/wfs?SERVICE=WFS&VERSION=2.0.0"
    "&REQUEST=GetFeature&typeName=BAG3D:lod22&bbox=%s,%s,%s,%s"
    "&count=2000&outputFormat=application/json" % BBOX, "3DBAG")

hoogte = {}
for f in d3["features"]:
    p = f["properties"]
    hoogte.setdefault(p["identificatie"].replace("NL.IMBAG.Pand.", ""), p)

panden = []
for f in bag["features"]:
    p = f["properties"]
    h = hoogte.get(p["identificatie"])
    if not h:
        continue
    geom = shape(f["geometry"])
    if geom.geom_type == "MultiPolygon":
        geom = max(geom.geoms, key=lambda g: g.area)
    panden.append({"id": p["identificatie"], "geom": geom,
                   "bouwjaar": p.get("bouwjaar"),
                   "gebruiksdoel": p.get("gebruiksdoel"),
                   "vbo": p.get("aantal_verblijfsobjecten") or 0,
                   "maaiveld": h["b3_h_maaiveld"], "nok": h["b3_h_nok"],
                   "h50": h["b3_h_50p"], "lagen": h.get("b3_bouwlagen"),
                   "daktype": h.get("b3_dak_type")})

print("\npanden met 3D-hoogte: %d" % len(panden))

# Bouwmuren: segmenten die een buurpand raken
for a in panden:
    a["buren"] = sum(1 for b in panden
                     if b is not a and a["geom"].buffer(0.3).intersects(b["geom"]))

for a in panden:
    if a["vbo"] > 1:
        a["type"] = "gestapeld"
    elif a["buren"] >= 2:
        a["type"] = "rij tuss"
    elif a["buren"] == 1:
        a["type"] = "rij hoek"
    else:
        a["type"] = "vrij"

print("\n%-20s %-10s %4s %5s %6s %6s %6s %s" % (
    "id", "type", "vbo", "jaar", "maaiv", "nok", "lagen", "dak"))
for a in sorted(panden, key=lambda x: x["id"]):
    print("%-20s %-10s %4d %5s %6.2f %6.2f %6s %s" % (
        a["id"], a["type"], a["vbo"], a["bouwjaar"] or 0, a["maaiveld"] or 0,
        a["nok"] or 0, a["lagen"], a["daktype"]))

print("\nverdeling:", dict(collections.Counter(a["type"] for a in panden)))
json.dump([{k: (v.wkt if k == "geom" else v) for k, v in a.items()}
           for a in panden], open("blok.json", "w"), indent=1)
print("-> blok.json")
