# -*- coding: utf-8 -*-
"""Tessellate de IFC en render een plaatje - visuele controle zonder viewer."""
import math
import sys

import numpy as np
from PIL import Image

import ifcopenshell
import ifcopenshell.geom

KLEUR = {
    "IfcWall": (188, 178, 166),
    "IfcWindow": (70, 110, 150),
    "IfcDoor": (120, 80, 60),
}


def tessellate(pad):
    f = ifcopenshell.open(pad)
    inst = ifcopenshell.geom.settings()
    inst.set("use-world-coords", True)
    driehoeken = []
    mislukt = 0
    for soort in ("IfcWall", "IfcWindow", "IfcDoor"):
        for el in f.by_type(soort):
            try:
                vorm = ifcopenshell.geom.create_shape(inst, el)
            except Exception:
                mislukt += 1
                continue
            v = np.array(vorm.geometry.verts, dtype=float).reshape(-1, 3)
            idx = np.array(vorm.geometry.faces, dtype=int).reshape(-1, 3)
            for tri in idx:
                driehoeken.append((v[tri], KLEUR[soort]))
    print("driehoeken: %d   mislukte shapes: %d" % (len(driehoeken), mislukt))
    return driehoeken


def render(driehoeken, camera, doel, uitvoer, breedte=1400, hoogte=900,
           fov=45.0, lucht=(232, 236, 240)):
    cam = np.array(camera, dtype=float)
    tgt = np.array(doel, dtype=float)
    fwd = tgt - cam
    fwd /= np.linalg.norm(fwd)
    rechts = np.cross(fwd, np.array([0.0, 0.0, 1.0]))
    rechts /= np.linalg.norm(rechts)
    op = np.cross(rechts, fwd)
    schaal = (breedte / 2.0) / math.tan(math.radians(fov) / 2.0)
    zon = np.array([0.4, -0.7, 0.6])
    zon /= np.linalg.norm(zon)

    beeld = Image.new("RGB", (breedte, hoogte), lucht)
    px = beeld.load()
    diepte = np.full((hoogte, breedte), 1e30)

    for verts, basiskleur in driehoeken:
        rel = verts - cam
        cz = rel @ fwd
        if np.any(cz < 0.4):
            continue
        cx = rel @ rechts
        cy = rel @ op
        sx = breedte / 2.0 + schaal * cx / cz
        sy = hoogte / 2.0 - schaal * cy / cz
        xs, ys = sx, sy

        n = np.cross(verts[1] - verts[0], verts[2] - verts[0])
        nl = np.linalg.norm(n)
        if nl < 1e-9:
            continue
        n /= nl
        lic = 0.45 + 0.55 * abs(float(n @ zon))
        kleur = tuple(min(255, int(c * lic)) for c in basiskleur)

        x0 = max(0, int(np.floor(xs.min())))
        x1 = min(breedte - 1, int(np.ceil(xs.max())))
        y0 = max(0, int(np.floor(ys.min())))
        y1 = min(hoogte - 1, int(np.ceil(ys.max())))
        if x1 < x0 or y1 < y0 or (x1 - x0) * (y1 - y0) > 4_000_000:
            continue

        ax, ay = xs[0], ys[0]
        bx, by = xs[1], ys[1]
        cx2, cy2 = xs[2], ys[2]
        det = (by - cy2) * (ax - cx2) + (cx2 - bx) * (ay - cy2)
        if abs(det) < 1e-9:
            continue

        for y in range(y0, y1 + 1):
            for x in range(x0, x1 + 1):
                l1 = ((by - cy2) * (x - cx2) + (cx2 - bx) * (y - cy2)) / det
                l2 = ((cy2 - ay) * (x - cx2) + (ax - cx2) * (y - cy2)) / det
                l3 = 1.0 - l1 - l2
                if l1 < -0.001 or l2 < -0.001 or l3 < -0.001:
                    continue
                z = l1 * cz[0] + l2 * cz[1] + l3 * cz[2]
                if z < diepte[y, x]:
                    diepte[y, x] = z
                    px[x, y] = kleur

    beeld.save(uitvoer)
    print("-> %s" % uitvoer)


if __name__ == "__main__":
    tris = tessellate("contextpanden.ifc")
    alle = np.vstack([t[0] for t in tris])
    lo, hi = alle.min(axis=0), alle.max(axis=0)
    mid = (lo + hi) / 2.0
    print("model bbox: X %.1f..%.1f  Y %.1f..%.1f  Z %.1f..%.1f"
          % (lo[0], hi[0], lo[1], hi[1], lo[2], hi[2]))

    straal = max(hi[0] - lo[0], hi[1] - lo[1])
    # ooghoogte, schuin van voren
    render(tris,
           camera=(mid[0] - straal * 0.75, mid[1] - straal * 0.8, lo[2] + 22.0),
           doel=(mid[0], mid[1], mid[2]),
           uitvoer="blok_overzicht.png")
    # detail: dicht op een gevel
    render(tris,
           camera=(mid[0] - straal * 0.22, mid[1] - straal * 0.30, lo[2] + 6.0),
           doel=(mid[0] - straal * 0.05, mid[1], lo[2] + 9.0),
           uitvoer="blok_detail.png", fov=55.0)
