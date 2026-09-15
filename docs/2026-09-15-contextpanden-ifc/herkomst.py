# -*- coding: utf-8 -*-
"""Tel per pand waar de invoer vandaan kwam: gemeten, geschat of aangenomen."""
import json
import collections

from naar_ifc import KENTAL, STANDAARD, periode

panden = json.load(open("blok.json"))

t = collections.Counter()
kental_bron = collections.Counter()

for p in panden:
    t["panden"] += 1
    t["maaiveld_uit_ahn"] += 1 if p.get("maaiveld") else 0
    t["hoogte_uit_ahn"] += 1 if (p.get("h50") or p.get("nok")) else 0
    t["bouwjaar_uit_bag"] += 1 if p.get("bouwjaar") else 0

    if p.get("lagen"):
        t["lagen_geschat_door_3dbag"] += 1
    else:
        t["lagen_gegokt_door_mij"] += 1

    sleutel = (p["type"], periode(p.get("bouwjaar")))
    if sleutel in KENTAL:
        kental_bron["exacte RVO-match type+periode"] += 1
    elif (p["type"], "tot46") in KENTAL:
        kental_bron["RVO-match op type, ANDERE periode"] += 1
    else:
        kental_bron["geen match -> mijn standaard %.0f%%" % (100 * STANDAARD[0])] += 1

print("--- invoer per pand (n=%d) ---" % t["panden"])
for k in ("maaiveld_uit_ahn", "hoogte_uit_ahn", "bouwjaar_uit_bag",
          "lagen_geschat_door_3dbag", "lagen_gegokt_door_mij"):
    print("  %-28s %d" % (k, t[k]))

print("")
print("--- herkomst van het glaspercentage ---")
for k, v in kental_bron.most_common():
    print("  %-40s %d" % (k, v))

print("")
print("--- welke periodes komen voor vs. welke ik in KENTAL heb ---")
voor = collections.Counter((p["type"], periode(p.get("bouwjaar")))
                           for p in panden)
for (typ, per), n in sorted(voor.items(), key=lambda x: -x[1]):
    status = "RVO" if (typ, per) in KENTAL else (
        "terugval op tot46" if (typ, "tot46") in KENTAL else "STANDAARD")
    print("  %-12s %-7s n=%-3d  -> %s" % (typ, per, n, status))
