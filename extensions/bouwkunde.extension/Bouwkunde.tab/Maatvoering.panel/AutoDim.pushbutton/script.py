# -*- coding: utf-8 -*-
"""AutoDim - Automatische Maatvoering

Plaats automatisch maatvoering langs een getekende Detail Line.
Detecteert wanden, grids, kolommen en totaalmaten.

Auteur: 3BM Bouwkunde
Versie: 2.1.0 - Linked model ondersteuning + meerdere maatlijn types
"""

import clr
import sys
import os
import math

SCRIPT_DIR = os.path.dirname(__file__)
EXTENSION_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(SCRIPT_DIR))))
LIB_DIR = os.path.join(EXTENSION_DIR, 'lib')
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

from bm_logger import get_logger
log = get_logger("AutoDim")
log.info("AutoDim v2.0")

from wpf_template import WPFWindow, Huisstijl

try:
    clr.AddReference('RevitAPI')
    clr.AddReference('RevitAPIUI')
except:
    pass

from Autodesk.Revit.DB import *
from Autodesk.Revit.DB.Structure import StructuralType
from Autodesk.Revit.UI.Selection import ObjectType, ISelectionFilter
from Autodesk.Revit.Exceptions import OperationCanceledException

from System.Collections.Generic import List

from pyrevit import revit, forms

# GEEN doc/uidoc/active_view = revit.doc hier!
# Wordt in main() gedaan om startup-vertraging te voorkomen
doc = None
uidoc = None
active_view = None

MM_PER_FOOT = 304.8

# Deuren: hoe ver naast de getekende lijn een wand nog meetelt. De lijn wordt
# bij deuren NAAST de wand getekend, niet er dwars doorheen.
DOOR_WALL_SEARCH_MM = 3000.0

# Een wand telt als evenwijdig aan de maatlijn vanaf deze |cos|-waarde (~18 graden)
DOOR_WALL_PARALLEL_MIN = 0.95

# Een WANDRIJ moet minstens dit deel van de getekende lijn beslaan. Let op:
# per rij, niet per losse wand - een rij bestaat vaak uit meerdere
# segmenten die samen de lijn afdekken. Gemeten geval: twee wanden van
# 1315 en 895 mm haalden los 49% en 33% (beide afgekeurd) maar samen 82%.
WALL_OVERLAP_MIN_FRACTION = 0.5

# Haalt geen enkele rij die drempel, dan wordt alsnog de best dekkende rij
# gebruikt mits die hier boven zit. Anders liever geen maatlijn dan een
# verkeerde.
WALL_OVERLAP_FALLBACK_FRACTION = 0.25

# Wanden binnen deze marge van de dichtstbijzijnde wand horen tot dezelfde
# wandrij (een rij bestaat vaak uit segmenten van 70 en 100 mm).
WALL_RUN_BAND_MM = 150.0

# Twee references binnen deze afstand zijn hetzelfde maatpunt. Gejoinde
# wanden delen hun kopse vlak exact, dus 0.001 ft (0,3 mm) is ruim genoeg.
REFERENCE_DEDUPE_FT = 0.001

# Zoekvenster waarbinnen een wandvlak bij het snijpunt hoort, bij een
# loodrechte kruising. Wordt gedeeld door sin(kruisingshoek), zodat schuine
# wanden niet wegvallen; MIN_SIN begrenst dat op ~2,3 graden.
FACE_WINDOW_BASE_FT = 2.0
FACE_WINDOW_MAX_FT = 50.0
FACE_WINDOW_MIN_SIN = 0.04

# Afstand tussen opeenvolgende maatlijnnummers; instelbaar in de UI
DEFAULT_LINE_OFFSET_MM = 500.0

# Een segment onder deze lengte telt als nulsegment en wordt weggewerkt.
# Bewust piepklein: een wand van 15 mm hoort WEL bemaat te worden, dus dit
# mag nooit een filter op kleine maten worden.
ZERO_SEGMENT_TOLERANCE_FT = 0.0003  # ~0,09 mm

# Hoe vaak een maatlijn opnieuw wordt opgebouwd om nulsegmenten te wissen
MAX_ZERO_SEGMENT_RETRIES = 12

# Lijnstijl waarmee de hulplijnen in de tekening zijn getekend. Staat die
# optie aan, dan hoeft er niets aangewezen te worden: alle lijnen met deze
# stijl in de actieve view worden achter elkaar bemaat.
DEFAULT_MEASURE_LINE_STYLE = '00_maatvoering_plattegronden'

# Een ruimtescheidingslijn die vrijwel evenwijdig aan de maatlijn loopt geeft
# alleen een scheerlijns snijpunt en dus geen bruikbaar maatpunt.
ROOM_LINE_PARALLEL_MAX = 0.95

# Categorieen die een eigen maatlijnnummer kunnen krijgen
LINE_NUMBER_KEYS = ('grids', 'walls', 'columns', 'floors', 'rooms', 'doors',
                    'total')



# =============================================================================
# SELECTION FILTER
# =============================================================================
class DetailLineFilter(ISelectionFilter):
    def AllowElement(self, element):
        if isinstance(element, DetailLine):
            return True
        if hasattr(element, 'Category') and element.Category:
            return element.Category.Id.IntegerValue == int(BuiltInCategory.OST_Lines)
        return False
    
    def AllowReference(self, ref, pos):
        return True


# =============================================================================
# HELPERS
# =============================================================================
def get_line_from_element(element):
    if hasattr(element, 'GeometryCurve'):
        curve = element.GeometryCurve
        if isinstance(curve, Line):
            return curve
    if hasattr(element, 'Location') and hasattr(element.Location, 'Curve'):
        curve = element.Location.Curve
        if isinstance(curve, Line):
            return curve
    return None


def find_lines_by_style(view, style_name):
    """Alle lijnen in de view die met deze lijnstijl getekend zijn.

    Lijnstijlen zijn subcategorieen van Lines; zowel detail- als modellijnen
    dragen ze. Vandaar de categorie-filter en niet een class-filter.
    """
    resultaat = []
    if not style_name:
        return resultaat

    gezocht = style_name.strip().lower()

    elementen = FilteredElementCollector(doc, view.Id)         .OfCategory(BuiltInCategory.OST_Lines)         .WhereElementIsNotElementType()         .ToElements()

    gevonden_stijlen = set()
    for element in elementen:
        if not isinstance(element, CurveElement):
            continue

        try:
            stijl = element.LineStyle
        except:
            continue

        if stijl is None:
            continue

        gevonden_stijlen.add(stijl.Name)
        if stijl.Name.strip().lower() == gezocht:
            resultaat.append(element)

    if not resultaat and gevonden_stijlen:
        log.info("Lijnstijlen in deze view: {}".format(
            ", ".join(sorted(gevonden_stijlen))
        ))

    return resultaat



def curves_intersect_2d(l1s, l1e, l2s, l2e):
    """Snelle 2D lijn intersectie."""
    x1, y1, x2, y2 = l1s.X, l1s.Y, l1e.X, l1e.Y
    x3, y3, x4, y4 = l2s.X, l2s.Y, l2e.X, l2e.Y
    
    denom = (x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4)
    if abs(denom) < 1e-10:
        return False, 0, 0
    
    t = ((x1 - x3) * (y3 - y4) - (y1 - y3) * (x3 - x4)) / denom
    u = -((x1 - x2) * (y1 - y3) - (y1 - y2) * (x1 - x3)) / denom
    
    if 0 <= t <= 1 and 0 <= u <= 1:
        return True, x1 + t * (x2 - x1), y1 + t * (y2 - y1)
    return False, 0, 0


def point_to_line_distance_2d(px, py, l1x, l1y, l2x, l2y):
    """Afstand van punt tot lijn segment in 2D."""
    dx = l2x - l1x
    dy = l2y - l1y
    length_sq = dx * dx + dy * dy
    
    if length_sq < 1e-10:
        return math.sqrt((px - l1x)**2 + (py - l1y)**2)
    
    t = max(0, min(1, ((px - l1x) * dx + (py - l1y) * dy) / length_sq))
    proj_x = l1x + t * dx
    proj_y = l1y + t * dy
    
    return math.sqrt((px - proj_x)**2 + (py - proj_y)**2)


def line_plane_crossing_t(line_start, line_dir, plane_origin, plane_normal):
    """Positie langs de maatlijn waar die een verticaal vlak snijdt.

    Puur in XY gerekend; alle vlakken waar dit voor gebruikt wordt staan
    verticaal.

    Dit vervangt het projecteren van face.Origin op de maatlijn. Die projectie
    klopt alleen bij een loodrechte kruising: dan projecteren alle punten van
    een wandvlak op dezelfde positie. Bij een schuine kruising is dat niet zo,
    en face.Origin ligt doorgaans in een hoek van het vlak in plaats van bij
    het snijpunt - waardoor het vlak buiten het zoekvenster viel en de wand
    onbemaat bleef.

    Returns:
        float, of None als lijn en vlak (vrijwel) evenwijdig lopen.
    """
    denom = plane_normal.X * line_dir.X + plane_normal.Y * line_dir.Y
    if abs(denom) < 1e-9:
        return None

    return (plane_normal.X * (plane_origin.X - line_start.X)
            + plane_normal.Y * (plane_origin.Y - line_start.Y)) / denom


def get_dimension_types():
    """Haal alle dimension types op."""
    collector = FilteredElementCollector(doc).OfClass(DimensionType)
    types = []
    for dt in collector:
        try:
            if dt.StyleType == DimensionStyleType.Linear:
                name = dt.get_Parameter(BuiltInParameter.SYMBOL_NAME_PARAM)
                if name:
                    types.append((dt.Id, name.AsString()))
        except:
            pass
    types.sort(key=lambda x: x[1])
    return types


def find_default_dim_type_index(dim_types):
    """Zoek index van eerste type met '1.8' in de naam."""
    for i, (dt_id, dt_name) in enumerate(dim_types):
        if "1.8" in dt_name:
            return i
    return 0 if dim_types else -1


def get_view_cut_plane_height(view):
    """Haal de cutplane hoogte van een ViewPlan op."""
    try:
        if not isinstance(view, ViewPlan):
            return None, None
        
        view_range = view.GetViewRange()
        cut_plane_offset = view_range.GetOffset(PlanViewPlane.CutPlane)
        bottom_offset = view_range.GetOffset(PlanViewPlane.BottomClipPlane)
        top_offset = view_range.GetOffset(PlanViewPlane.TopClipPlane)
        
        base_elevation = 0
        if view.GenLevel:
            base_elevation = view.GenLevel.Elevation
        
        bottom_height = base_elevation + bottom_offset
        top_height = base_elevation + top_offset
        
        return bottom_height, top_height
    except:
        return None, None


def element_visible_at_cut_plane(element, bottom_height, top_height):
    """Check of een element zichtbaar is op de cutplane hoogte."""
    if bottom_height is None or top_height is None:
        return True
    
    try:
        bbox = element.get_BoundingBox(None)
        if not bbox:
            return True
        
        elem_bottom = bbox.Min.Z
        elem_top = bbox.Max.Z
        
        return elem_bottom < top_height and elem_top > bottom_height
    except:
        return True


# =============================================================================
# REFERENCE GETTERS
# =============================================================================
def get_wall_face_references(wall, measure_line, view):
    """Haal references naar de buitenste faces van een wand."""
    references = []
    
    line_start = measure_line.GetEndPoint(0)
    line_end = measure_line.GetEndPoint(1)
    line_dir = (line_end - line_start).Normalize()
    
    wall_loc = wall.Location
    if not isinstance(wall_loc, LocationCurve):
        return []
    
    wall_curve = wall_loc.Curve
    ws = wall_curve.GetEndPoint(0)
    we = wall_curve.GetEndPoint(1)
    
    intersects, ix, iy = curves_intersect_2d(line_start, line_end, ws, we)
    if not intersects:
        return []
    
    t_intersect = (ix - line_start.X) * line_dir.X + (iy - line_start.Y) * line_dir.Y
    
    wall_dir = (we - ws).Normalize()
    wall_normal = XYZ(-wall_dir.Y, wall_dir.X, 0)

    # Hoe ver een wandvlak langs de maatlijn van het snijpunt mag liggen.
    # Bij een schuine kruising is die afstand wanddikte / sin(hoek), dus
    # een vaste drempel liet diagonale wanden wegvallen.
    sin_angle = abs(line_dir.X * wall_dir.Y - line_dir.Y * wall_dir.X)
    face_window = min(
        FACE_WINDOW_MAX_FT,
        FACE_WINDOW_BASE_FT / max(sin_angle, FACE_WINDOW_MIN_SIN)
    )
    
    options = Options()
    options.ComputeReferences = True
    options.View = view
    
    geom = wall.get_Geometry(options)
    if not geom:
        return []
    
    for geom_obj in geom:
        if not isinstance(geom_obj, Solid) or geom_obj.Volume <= 0:
            continue
        
        for face in geom_obj.Faces:
            if not isinstance(face, PlanarFace):
                continue
            
            fn = face.FaceNormal
            if abs(fn.Z) > 0.1:
                continue
            
            dot = abs(fn.X * wall_normal.X + fn.Y * wall_normal.Y)
            if dot < 0.9:
                continue
            
            ref = face.Reference
            if not ref:
                continue
            
            ft = line_plane_crossing_t(
                line_start, line_dir, face.Origin, fn
            )
            if ft is None:
                continue

            if abs(ft - t_intersect) < face_window:
                references.append((ref, ft))
    
    if len(references) >= 2:
        references.sort(key=lambda x: x[1])
        return [references[0], references[-1]]
    
    return references


def get_grid_references(measure_line, view):
    """Haal grid references."""
    references = []
    
    grids = FilteredElementCollector(doc, view.Id).OfClass(Grid).ToElements()
    
    line_start = measure_line.GetEndPoint(0)
    line_end = measure_line.GetEndPoint(1)
    line_dir = (line_end - line_start).Normalize()
    
    for grid in grids:
        gc = grid.Curve
        if not gc:
            continue
        
        gs, ge = gc.GetEndPoint(0), gc.GetEndPoint(1)
        intersects, ix, iy = curves_intersect_2d(line_start, line_end, gs, ge)
        
        if intersects:
            t = (ix - line_start.X) * line_dir.X + (iy - line_start.Y) * line_dir.Y
            ref = Reference(grid)
            references.append((ref, t))
    
    references.sort(key=lambda x: x[1])
    return references


def get_room_boundary_references(measure_line, view):
    """References naar de ruimtescheidingslijnen die de maatlijn kruisen.

    Dit zijn de <Room Separation> lijnen: daar waar een ruimte begrensd is
    zonder dat er een wand staat. Zonder deze optie is zo'n grens niet te
    bematen - er is geen wandvlak om naar te wijzen.

    Anders dan bij wanden is de reference de curve zelf, niet een vlak.
    Dat is dezelfde reference die Revit pakt als je de lijn met de hand
    aanwijst.
    """
    references = []

    line_start = measure_line.GetEndPoint(0)
    line_end = measure_line.GetEndPoint(1)
    line_dir = (line_end - line_start).Normalize()

    curve_elements = FilteredElementCollector(doc, view.Id)         .OfCategory(BuiltInCategory.OST_RoomSeparationLines)         .WhereElementIsNotElementType()         .ToElements()

    for curve_element in curve_elements:
        curve = getattr(curve_element, 'GeometryCurve', None)
        if not curve:
            continue

        # Gebogen scheidingslijnen bestaan; tesselleren zodat die ook een
        # begrensd snijpunt opleveren in plaats van te worden overgeslagen.
        if isinstance(curve, Line):
            punten = [curve.GetEndPoint(0), curve.GetEndPoint(1)]
        else:
            try:
                punten = list(curve.Tessellate())
            except:
                continue

        hit_t = None
        for i in range(len(punten) - 1):
            p1 = punten[i]
            p2 = punten[i + 1]

            segment = p2 - p1
            if segment.GetLength() < 1e-9:
                continue
            segment_dir = segment.Normalize()

            cos_hoek = (segment_dir.X * line_dir.X
                        + segment_dir.Y * line_dir.Y)
            if abs(cos_hoek) > ROOM_LINE_PARALLEL_MAX:
                continue

            intersects, ix, iy = curves_intersect_2d(
                line_start, line_end, p1, p2
            )
            if not intersects:
                continue

            hit_t = ((ix - line_start.X) * line_dir.X
                     + (iy - line_start.Y) * line_dir.Y)
            break

        if hit_t is None:
            continue

        ref = None
        try:
            ref = curve.Reference
        except:
            ref = None
        if ref is None:
            try:
                ref = Reference(curve_element)
            except:
                continue

        references.append((ref, hit_t))

    references.sort(key=lambda x: x[1])
    return references



def get_column_face_references(column, measure_line, view):
    """Haal references naar de buitenste faces van een kolom."""
    references = []
    
    line_start = measure_line.GetEndPoint(0)
    line_end = measure_line.GetEndPoint(1)
    line_dir = (line_end - line_start).Normalize()
    
    # Kolom locatie
    col_loc = column.Location
    if not col_loc:
        return []
    
    if hasattr(col_loc, 'Point'):
        col_point = col_loc.Point
    else:
        return []
    
    # Check of kolom dichtbij de lijn ligt
    dist = point_to_line_distance_2d(
        col_point.X, col_point.Y,
        line_start.X, line_start.Y,
        line_end.X, line_end.Y
    )
    
    # Kolom moet binnen ~1m van de lijn liggen
    if dist > 3.28:  # ~1m in feet
        return []
    
    # T-positie van kolom langs de lijn
    t_col = (col_point.X - line_start.X) * line_dir.X + (col_point.Y - line_start.Y) * line_dir.Y
    
    options = Options()
    options.ComputeReferences = True
    options.View = view
    
    geom = column.get_Geometry(options)
    if not geom:
        return []
    
    for geom_obj in geom:
        if isinstance(geom_obj, GeometryInstance):
            geom_obj = geom_obj.GetInstanceGeometry()
        
        solids = []
        if isinstance(geom_obj, Solid) and geom_obj.Volume > 0:
            solids.append(geom_obj)
        elif hasattr(geom_obj, 'GetEnumerator'):
            for g in geom_obj:
                if isinstance(g, Solid) and g.Volume > 0:
                    solids.append(g)
        
        for solid in solids:
            for face in solid.Faces:
                if not isinstance(face, PlanarFace):
                    continue
                
                fn = face.FaceNormal
                if abs(fn.Z) > 0.1:
                    continue
                
                # Face moet loodrecht op de maatlijn staan
                dot = abs(fn.X * line_dir.X + fn.Y * line_dir.Y)
                if dot < 0.7:
                    continue
                
                ref = face.Reference
                if not ref:
                    continue
                
                fo = face.Origin
                ft = (fo.X - line_start.X) * line_dir.X + (fo.Y - line_start.Y) * line_dir.Y
                
                references.append((ref, ft))
    
    if len(references) >= 2:
        references.sort(key=lambda x: x[1])
        return [references[0], references[-1]]
    
    return references


def get_horizontal_edge_segments(edge, transform=None):
    """Horizontale segmenten van een edge, desgewenst naar host-space.

    Alleen horizontale randen zijn bruikbaar: dat zijn de boven- en onderrand
    van een verticaal zijvlak, en die vormen in plattegrond de vloerrand.
    Gebogen randen worden getesselleerd zodat ronde vloeren ook werken.
    """
    try:
        curve = edge.AsCurve()
    except:
        return []

    if not curve:
        return []

    if isinstance(curve, Line):
        points = [curve.GetEndPoint(0), curve.GetEndPoint(1)]
    else:
        try:
            points = list(curve.Tessellate())
        except:
            return []

    if transform is not None:
        points = [transform.OfPoint(p) for p in points]

    segments = []
    for i in range(len(points) - 1):
        p1 = points[i]
        p2 = points[i + 1]
        if abs(p1.Z - p2.Z) > 0.01:  # ~3 mm
            continue
        segments.append((p1, p2))

    return segments


def get_floor_edge_references(floor, measure_line, view,
                              transform=None, link_doc=None,
                              link_instance=None):
    """Haal references naar de vloerranden die de maatlijn kruist.

    Een vloerrand is het verticale zijvlak aan de omtrek van de plaat. Vlakken
    rond sparingen zitten in dezelfde solid en komen dus vanzelf mee.

    Anders dan bij wanden wordt hier niet op vlak-tegen-vlak gesneden maar op
    de horizontale randen van elk zijvlak: dat is een begrensde snede, zodat
    een vloerrand die de maatlijn niet echt raakt ook niet meetelt.

    transform/link_doc/link_instance vullen voor vloeren uit een linked model.
    """
    references = []

    line_start = measure_line.GetEndPoint(0)
    line_end = measure_line.GetEndPoint(1)
    line_dir = (line_end - line_start).Normalize()

    options = Options()
    options.ComputeReferences = True
    if link_doc is None:
        options.View = view
    else:
        # View-geometrie van een linked element is niet opvraagbaar met de
        # host-view; detailniveau expliciet zetten.
        options.DetailLevel = ViewDetailLevel.Fine

    try:
        geom = floor.get_Geometry(options)
    except:
        return []

    if not geom:
        return []

    solids = []
    for geom_obj in geom:
        if isinstance(geom_obj, GeometryInstance):
            geom_obj = geom_obj.GetInstanceGeometry()

        if isinstance(geom_obj, Solid) and geom_obj.Volume > 0:
            solids.append(geom_obj)
        elif hasattr(geom_obj, 'GetEnumerator'):
            for g in geom_obj:
                if isinstance(g, Solid) and g.Volume > 0:
                    solids.append(g)

    for solid in solids:
        for face in solid.Faces:
            if not isinstance(face, PlanarFace):
                continue

            fn = face.FaceNormal
            if abs(fn.Z) > 0.1:  # alleen verticale zijvlakken
                continue

            # Een rand die vrijwel evenwijdig aan de maatlijn loopt levert
            # geen bruikbaar maatpunt op.
            if abs(fn.X * line_dir.X + fn.Y * line_dir.Y) < 0.3:
                continue

            ref = face.Reference
            if not ref:
                continue

            if link_doc is not None:
                ref = convert_linked_reference(ref, link_doc, link_instance)
                if not ref:
                    continue

            hit_t = None
            for edge_loop in face.EdgeLoops:
                for edge in edge_loop:
                    for p1, p2 in get_horizontal_edge_segments(edge, transform):
                        intersects, ix, iy = curves_intersect_2d(
                            line_start, line_end, p1, p2
                        )
                        if not intersects:
                            continue
                        hit_t = ((ix - line_start.X) * line_dir.X
                                 + (iy - line_start.Y) * line_dir.Y)
                        break
                    if hit_t is not None:
                        break
                if hit_t is not None:
                    break

            if hit_t is not None:
                references.append((ref, hit_t))

    # Boven- en onderrand van hetzelfde zijvlak leveren dezelfde t op, en
    # opeenvolgende coplanaire vlakken ook. Ontdubbelen op ~0,3 mm.
    unique = {}
    for ref, t in references:
        key = round(t, 3)
        if key not in unique:
            unique[key] = (ref, t)

    return sorted(unique.values(), key=lambda x: x[1])


def get_wall_opening_references(wall, measure_line, view):
    """References naar alle wandvlakken loodrecht op de wandrichting.

    Dat zijn de twee kopse einden EN de dagkanten van elke sparing - deuren,
    ramen, doorgangen. Precies de vlakken die je met de hand ook aanwijst.

    Gemeten op wand 25985384 (PVG_TO_BWK_OXS): posities 0 / 80 / 1005 / 1145,
    oftewel 80 / 925 / 140. Identiek aan de handmatig gezette maatlijn.

    Bewust NIET FamilyInstance.GetReferences(Left/Right) van de deur: die
    referentievlakken liggen op de nominale deurmaat, niet op de dagkant van
    het kozijn, en leveren dus een andere maat. De deurfamilie-geometrie zelf
    is nog onbruikbaarder - die bestaat uit tientallen vlakken op millimeters
    van elkaar (sponning, aanslag, opdek).
    """
    wall_loc = wall.Location
    if not isinstance(wall_loc, LocationCurve):
        return []

    wall_curve = wall_loc.Curve
    if not isinstance(wall_curve, Line):
        return []

    line_start = measure_line.GetEndPoint(0)
    line_end = measure_line.GetEndPoint(1)
    line_dir = (line_end - line_start).Normalize()

    ws = wall_curve.GetEndPoint(0)
    we = wall_curve.GetEndPoint(1)
    wall_dir = (we - ws).Normalize()

    options = Options()
    options.ComputeReferences = True
    options.View = view

    try:
        geom = wall.get_Geometry(options)
    except:
        return []

    if not geom:
        return []

    references = []
    for geom_obj in geom:
        if not isinstance(geom_obj, Solid) or geom_obj.Volume <= 0:
            continue

        for face in geom_obj.Faces:
            if not isinstance(face, PlanarFace):
                continue

            fn = face.FaceNormal
            if abs(fn.Z) > 0.1:
                continue

            # Loodrecht op de wand: kopse einden en sparingsdagkanten.
            # 0.7 laat verstek-aansluitingen van gejoinde wanden nog toe.
            if abs(fn.X * wall_dir.X + fn.Y * wall_dir.Y) < 0.7:
                continue

            ref = face.Reference
            if not ref:
                continue

            ft = line_plane_crossing_t(
                line_start, line_dir, face.Origin, fn
            )
            if ft is None:
                continue

            references.append((ref, ft))

    return dedupe_references(references)


# =============================================================================
# FIND CROSSING ELEMENTS
# =============================================================================
def find_crossing_walls(measure_line, walls, bottom_height, top_height, min_thickness):
    """Vind kruisende wanden binnen view range."""
    crossing = []
    
    line_start = measure_line.GetEndPoint(0)
    line_end = measure_line.GetEndPoint(1)
    
    for wall in walls:
        if not element_visible_at_cut_plane(wall, bottom_height, top_height):
            continue
        
        wl = wall.Location
        if not isinstance(wl, LocationCurve):
            continue
        
        wc = wl.Curve
        ws, we = wc.GetEndPoint(0), wc.GetEndPoint(1)
        
        intersects, ix, iy = curves_intersect_2d(line_start, line_end, ws, we)
        
        if intersects:
            wt = doc.GetElement(wall.GetTypeId())
            thickness = 0
            
            if wt:
                p = wt.get_Parameter(BuiltInParameter.WALL_ATTR_WIDTH_PARAM)
                if p:
                    thickness = p.AsDouble() * 304.8
            
            if thickness <= 0:
                # Gestapelde en gordijnwanden geven geen type-breedte terug
                try:
                    thickness = wall.Width * 304.8
                except:
                    thickness = 0

            if thickness > min_thickness:
                crossing.append(wall)
    
    return crossing


def wall_thickness_mm(wall):
    """Wanddikte in mm, met terugval voor gestapelde en gordijnwanden."""
    wall_type = doc.GetElement(wall.GetTypeId())
    if wall_type:
        param = wall_type.get_Parameter(BuiltInParameter.WALL_ATTR_WIDTH_PARAM)
        if param:
            dikte = param.AsDouble() * MM_PER_FOOT
            if dikte > 0:
                return dikte

    try:
        return wall.Width * MM_PER_FOOT
    except:
        return 0.0


def merged_coverage(intervals):
    """Totale bedekking van overlappende intervallen, zonder dubbeltelling."""
    if not intervals:
        return 0.0

    geordend = sorted(intervals)
    totaal = 0.0
    lo, hi = geordend[0]

    for start, eind in geordend[1:]:
        if start > hi:
            totaal += hi - lo
            lo, hi = start, eind
        else:
            hi = max(hi, eind)

    return totaal + (hi - lo)


def group_into_runs(kandidaten):
    """Groepeer wanden op afstand tot de maatlijn: een groep is een wandrij.

    kandidaten: lijst van (afstand, wand, overlap_interval).
    """
    band = WALL_RUN_BAND_MM / MM_PER_FOOT
    rijen = []

    for afstand, wall, interval in sorted(kandidaten, key=lambda k: k[0]):
        if rijen and afstand - rijen[-1]['max_afstand'] <= band:
            rijen[-1]['walls'].append(wall)
            rijen[-1]['intervals'].append(interval)
            rijen[-1]['max_afstand'] = afstand
        else:
            rijen.append({
                'afstand': afstand,
                'max_afstand': afstand,
                'walls': [wall],
                'intervals': [interval],
            })

    return rijen


def find_walls_along_line(measure_line, walls, min_thickness=0.0,
                          preselected_walls=None):
    """Vind de wandrij die EVENWIJDIG NAAST de maatlijn ligt.

    Dit is de omgekeerde logica van find_crossing_walls(): bij sparingen teken
    je de lijn niet dwars door de wand maar ernaast, op de plek waar de
    maatlijn moet komen.

    Zijn er wanden voorgeselecteerd, dan zijn die leidend - de gebruiker weet
    beter welke wand hij bedoelt dan welke drempel dan ook. Dat blijft de
    aangeraden werkwijze zodra er meerdere evenwijdige wanden dicht bij elkaar
    liggen.

    Zonder voorselectie:
      1. evenwijdig aan de maatlijn
      2. dikker dan min_thickness (anders komen afwerkwanden mee)
      3. wanden groeperen tot RIJEN op gelijke afstand
      4. de dichtstbijzijnde rij nemen die genoeg van de lijn afdekt

    De dekkingseis geldt per rij, niet per wand: een rij bestaat vaak uit
    meerdere segmenten die pas samen de lijn afdekken.
    """
    if preselected_walls:
        return list(preselected_walls)

    line_start = measure_line.GetEndPoint(0)
    line_end = measure_line.GetEndPoint(1)
    line_dir = (line_end - line_start).Normalize()
    line_length = measure_line.Length

    if line_length <= 0:
        return []

    max_distance = DOOR_WALL_SEARCH_MM / MM_PER_FOOT
    kandidaten = []

    for wall in walls:
        wall_loc = wall.Location
        if not isinstance(wall_loc, LocationCurve):
            continue

        wall_curve = wall_loc.Curve
        if not isinstance(wall_curve, Line):
            continue

        ws = wall_curve.GetEndPoint(0)
        we = wall_curve.GetEndPoint(1)
        wall_dir = (we - ws).Normalize()

        if abs(wall_dir.X * line_dir.X
               + wall_dir.Y * line_dir.Y) < DOOR_WALL_PARALLEL_MIN:
            continue

        thickness = wall_thickness_mm(wall)
        if not thickness > min_thickness:
            continue

        t_start = ((ws.X - line_start.X) * line_dir.X
                   + (ws.Y - line_start.Y) * line_dir.Y)
        t_end = ((we.X - line_start.X) * line_dir.X
                 + (we.Y - line_start.Y) * line_dir.Y)
        lo = max(min(t_start, t_end), 0.0)
        hi = min(max(t_start, t_end), line_length)

        if hi <= lo:
            continue

        mid_x = (ws.X + we.X) / 2.0
        mid_y = (ws.Y + we.Y) / 2.0
        hart = point_to_line_distance_2d(
            mid_x, mid_y,
            line_start.X, line_start.Y, line_end.X, line_end.Y
        )
        zijde = abs(hart - (thickness / MM_PER_FOOT) / 2.0)

        if zijde > max_distance:
            continue

        kandidaten.append((zijde, wall, (lo, hi)))

    if not kandidaten:
        log.warning("Sparingen: geen enkele evenwijdige wand naast de lijn")
        return []

    rijen = group_into_runs(kandidaten)
    for rij in rijen:
        rij['dekking'] = merged_coverage(rij['intervals']) / line_length

    goed = [r for r in rijen
            if r['dekking'] >= WALL_OVERLAP_MIN_FRACTION]

    if goed:
        gekozen = goed[0]           # rijen staan al op afstand gesorteerd
    else:
        beste = max(rijen, key=lambda r: r['dekking'])
        if beste['dekking'] < WALL_OVERLAP_FALLBACK_FRACTION:
            log.warning(
                "Sparingen: geen wandrij dekt genoeg van de lijn af "
                "(beste {:.0f}%). Selecteer de wanden vooraf."
                .format(beste['dekking'] * 100)
            )
            return []
        log.warning("Sparingen: geen rij haalt {:.0f}%, terugval op de best "
                    "dekkende rij ({:.0f}% op {:.0f} mm)"
                    .format(WALL_OVERLAP_MIN_FRACTION * 100,
                            beste['dekking'] * 100,
                            beste['afstand'] * MM_PER_FOOT))
        gekozen = beste

    log.info("Sparingen: {} wanden in {} rij(en) -> rij op {:.0f} mm met "
             "{} wand(en), dekking {:.0f}%"
             .format(len(kandidaten), len(rijen),
                     gekozen['afstand'] * MM_PER_FOOT,
                     len(gekozen['walls']), gekozen['dekking'] * 100))

    return gekozen['walls']


def dedupe_references(references):
    """Ontdubbel references die op dezelfde positie langs de maatlijn liggen.

    Gejoinde wanden delen hun kopse vlak; zonder ontdubbeling levert dat een
    maatsegment van 0 mm op.

    Vergelijkt opeenvolgend tegen de laatst bewaarde waarde in plaats van te
    bucketen op een raster - bij bucketen kunnen twee punten die 0,01 mm uit
    elkaar liggen net aan weerszijden van een grens vallen en toch allebei
    blijven staan.
    """
    unique = []
    for ref, t in sorted(references, key=lambda x: x[1]):
        if unique and abs(t - unique[-1][1]) < REFERENCE_DEDUPE_FT:
            continue
        unique.append((ref, t))

    return unique


def find_crossing_floors(measure_line, floors, transform=None):
    """Vind vloeren waarvan de omhullende de maatlijn in plattegrond kruist.

    Bewust GEEN cutplane-filter: een vloer ligt vrijwel altijd onder het
    snijvlak van de plattegrond maar wordt daar wel getekend. De echte
    selectie gebeurt per rand in get_floor_edge_references().
    """
    crossing = []

    line_start = measure_line.GetEndPoint(0)
    line_end = measure_line.GetEndPoint(1)

    min_x = min(line_start.X, line_end.X)
    max_x = max(line_start.X, line_end.X)
    min_y = min(line_start.Y, line_end.Y)
    max_y = max(line_start.Y, line_end.Y)

    for floor in floors:
        try:
            bbox = floor.get_BoundingBox(None)
        except:
            bbox = None

        if not bbox:
            continue

        b_min = bbox.Min
        b_max = bbox.Max
        if transform is not None:
            b_min = transform.OfPoint(bbox.Min)
            b_max = transform.OfPoint(bbox.Max)

        # Een transform kan min/max omdraaien (rotatie/spiegeling)
        lo_x, hi_x = min(b_min.X, b_max.X), max(b_min.X, b_max.X)
        lo_y, hi_y = min(b_min.Y, b_max.Y), max(b_min.Y, b_max.Y)

        if hi_x < min_x or lo_x > max_x or hi_y < min_y or lo_y > max_y:
            continue

        crossing.append(floor)

    return crossing


def find_crossing_linked_floors(measure_line, link_info):
    """Vind vloeren in een linked model die de maatlijn kruisen.

    Returns:
        list of tuples: (floor, link_info)
    """
    link_doc = link_info['doc']

    floors = list(
        FilteredElementCollector(link_doc)
        .OfCategory(BuiltInCategory.OST_Floors)
        .WhereElementIsNotElementType()
        .ToElements()
    )

    return [
        (floor, link_info)
        for floor in find_crossing_floors(
            measure_line, floors, link_info['transform']
        )
    ]


def find_crossing_columns(measure_line, columns, bottom_height, top_height):
    """Vind kolommen nabij de maatlijn."""
    crossing = []
    
    line_start = measure_line.GetEndPoint(0)
    line_end = measure_line.GetEndPoint(1)
    
    for column in columns:
        if not element_visible_at_cut_plane(column, bottom_height, top_height):
            continue
        
        col_loc = column.Location
        if not col_loc or not hasattr(col_loc, 'Point'):
            continue
        
        col_point = col_loc.Point
        
        dist = point_to_line_distance_2d(
            col_point.X, col_point.Y,
            line_start.X, line_start.Y,
            line_end.X, line_end.Y
        )
        
        if dist < 3.28:  # ~1m
            crossing.append(column)
    
    return crossing


# =============================================================================
# LINKED MODELS
# =============================================================================
def get_linked_models(view):
    """Verzamel alle geladen RevitLinkInstances met document + transform.

    Returns:
        list of dict: Elk met 'instance', 'doc', 'transform' keys.
    """
    links = []
    link_instances = (
        FilteredElementCollector(doc, view.Id)
        .OfClass(RevitLinkInstance)
        .ToElements()
    )

    for link_inst in link_instances:
        link_doc = link_inst.GetLinkDocument()
        if not link_doc:
            continue
        transform = link_inst.GetTotalTransform()
        links.append({
            'instance': link_inst,
            'doc': link_doc,
            'transform': transform,
        })
        log.info("Linked model: {} ({} elements)".format(
            link_doc.Title,
            FilteredElementCollector(link_doc).WhereElementIsNotElementType()
                .GetElementCount()
        ))
    return links


def convert_linked_reference(face_ref, link_doc, link_instance):
    """Converteer een face reference uit een linked doc naar host-compatible.

    Args:
        face_ref: Reference naar een face in het linked document.
        link_doc: Het linked Document object.
        link_instance: De RevitLinkInstance.

    Returns:
        Reference: Host-compatible reference, of None bij falen.
    """
    try:
        stable = face_ref.ConvertToStableRepresentation(link_doc)
        host_stable = "{}:{}".format(link_instance.UniqueId, stable)
        return Reference.ParseFromStableRepresentation(doc, host_stable)
    except Exception as ex:
        log.debug("convert_linked_reference failed: {}".format(ex))
        return None


def get_linked_wall_face_references(
    wall, measure_line, view, link_doc, link_instance, transform
):
    """Variant van get_wall_face_references() voor linked wanden.

    Transformeert linked coördinaten naar host space en converteert
    face references via stable representation.
    """
    references = []

    line_start = measure_line.GetEndPoint(0)
    line_end = measure_line.GetEndPoint(1)
    line_dir = (line_end - line_start).Normalize()

    wall_loc = wall.Location
    if not isinstance(wall_loc, LocationCurve):
        return []

    wall_curve = wall_loc.Curve
    ws = transform.OfPoint(wall_curve.GetEndPoint(0))
    we = transform.OfPoint(wall_curve.GetEndPoint(1))

    intersects, ix, iy = curves_intersect_2d(line_start, line_end, ws, we)
    if not intersects:
        return []

    t_intersect = (
        (ix - line_start.X) * line_dir.X
        + (iy - line_start.Y) * line_dir.Y
    )

    wall_dir = (we - ws).Normalize()
    wall_normal = XYZ(-wall_dir.Y, wall_dir.X, 0)

    # Hoe ver een wandvlak langs de maatlijn van het snijpunt mag liggen.
    # Bij een schuine kruising is die afstand wanddikte / sin(hoek), dus
    # een vaste drempel liet diagonale wanden wegvallen.
    sin_angle = abs(line_dir.X * wall_dir.Y - line_dir.Y * wall_dir.X)
    face_window = min(
        FACE_WINDOW_MAX_FT,
        FACE_WINDOW_BASE_FT / max(sin_angle, FACE_WINDOW_MIN_SIN)
    )

    options = Options()
    options.ComputeReferences = True
    options.View = view

    geom = wall.get_Geometry(options)
    if not geom:
        return []

    for geom_obj in geom:
        if not isinstance(geom_obj, Solid) or geom_obj.Volume <= 0:
            continue

        for face in geom_obj.Faces:
            if not isinstance(face, PlanarFace):
                continue

            fn_local = face.FaceNormal
            fn = transform.OfVector(fn_local)

            if abs(fn.Z) > 0.1:
                continue

            dot = abs(fn.X * wall_normal.X + fn.Y * wall_normal.Y)
            if dot < 0.9:
                continue

            ref = face.Reference
            if not ref:
                continue

            host_ref = convert_linked_reference(ref, link_doc, link_instance)
            if not host_ref:
                continue

            ft = line_plane_crossing_t(
                line_start, line_dir, transform.OfPoint(face.Origin), fn
            )
            if ft is None:
                continue

            if abs(ft - t_intersect) < face_window:
                references.append((host_ref, ft))

    if len(references) >= 2:
        references.sort(key=lambda x: x[1])
        return [references[0], references[-1]]

    return references


def get_linked_column_face_references(
    column, measure_line, view, link_doc, link_instance, transform
):
    """Variant van get_column_face_references() voor linked kolommen."""
    references = []

    line_start = measure_line.GetEndPoint(0)
    line_end = measure_line.GetEndPoint(1)
    line_dir = (line_end - line_start).Normalize()

    col_loc = column.Location
    if not col_loc or not hasattr(col_loc, 'Point'):
        return []

    col_point = transform.OfPoint(col_loc.Point)

    dist = point_to_line_distance_2d(
        col_point.X, col_point.Y,
        line_start.X, line_start.Y,
        line_end.X, line_end.Y
    )
    if dist > 3.28:
        return []

    options = Options()
    options.ComputeReferences = True
    options.View = view

    geom = column.get_Geometry(options)
    if not geom:
        return []

    for geom_obj in geom:
        if isinstance(geom_obj, GeometryInstance):
            geom_obj = geom_obj.GetInstanceGeometry()

        solids = []
        if isinstance(geom_obj, Solid) and geom_obj.Volume > 0:
            solids.append(geom_obj)
        elif hasattr(geom_obj, 'GetEnumerator'):
            for g in geom_obj:
                if isinstance(g, Solid) and g.Volume > 0:
                    solids.append(g)

        for solid in solids:
            for face in solid.Faces:
                if not isinstance(face, PlanarFace):
                    continue

                fn_local = face.FaceNormal
                fn = transform.OfVector(fn_local)

                if abs(fn.Z) > 0.1:
                    continue

                dot = abs(fn.X * line_dir.X + fn.Y * line_dir.Y)
                if dot < 0.7:
                    continue

                ref = face.Reference
                if not ref:
                    continue

                host_ref = convert_linked_reference(
                    ref, link_doc, link_instance
                )
                if not host_ref:
                    continue

                fo = transform.OfPoint(face.Origin)
                ft = (
                    (fo.X - line_start.X) * line_dir.X
                    + (fo.Y - line_start.Y) * line_dir.Y
                )

                references.append((host_ref, ft))

    if len(references) >= 2:
        references.sort(key=lambda x: x[1])
        return [references[0], references[-1]]

    return references


def find_crossing_linked_walls(
    measure_line, link_info, bottom_height, top_height, min_thickness
):
    """Vind kruisende wanden in een linked model.

    Args:
        measure_line: De maatlijn.
        link_info: Dict met 'instance', 'doc', 'transform'.
        bottom_height: Onderkant view range (host coords).
        top_height: Bovenkant view range (host coords).
        min_thickness: Minimum wanddikte in mm.

    Returns:
        list of tuples: (wall, link_info) voor elke kruisende wand.
    """
    crossing = []
    link_doc = link_info['doc']
    transform = link_info['transform']

    line_start = measure_line.GetEndPoint(0)
    line_end = measure_line.GetEndPoint(1)

    walls = (
        FilteredElementCollector(link_doc)
        .OfClass(Wall)
        .WhereElementIsNotElementType()
        .ToElements()
    )

    for wall in walls:
        # View range check met transform offset
        if bottom_height is not None and top_height is not None:
            try:
                bbox = wall.get_BoundingBox(None)
                if bbox:
                    elem_bottom = transform.OfPoint(bbox.Min).Z
                    elem_top = transform.OfPoint(bbox.Max).Z
                    if not (elem_bottom < top_height
                            and elem_top > bottom_height):
                        continue
            except:
                pass

        wl = wall.Location
        if not isinstance(wl, LocationCurve):
            continue

        wc = wl.Curve
        ws = transform.OfPoint(wc.GetEndPoint(0))
        we = transform.OfPoint(wc.GetEndPoint(1))

        intersects, ix, iy = curves_intersect_2d(
            line_start, line_end, ws, we
        )

        if intersects:
            wt = link_doc.GetElement(wall.GetTypeId())
            thickness = 0
            if wt:
                p = wt.get_Parameter(BuiltInParameter.WALL_ATTR_WIDTH_PARAM)
                if p:
                    thickness = p.AsDouble() * 304.8

            if thickness <= 0:
                # Gestapelde en gordijnwanden geven geen type-breedte terug
                try:
                    thickness = wall.Width * 304.8
                except:
                    thickness = 0

            if thickness > min_thickness:
                crossing.append((wall, link_info))

    return crossing


def find_crossing_linked_columns(
    measure_line, link_info, bottom_height, top_height
):
    """Vind kolommen nabij de maatlijn in een linked model.

    Returns:
        list of tuples: (column, link_info) voor elke nabije kolom.
    """
    crossing = []
    link_doc = link_info['doc']
    transform = link_info['transform']

    line_start = measure_line.GetEndPoint(0)
    line_end = measure_line.GetEndPoint(1)

    columns = list(
        FilteredElementCollector(link_doc)
        .OfCategory(BuiltInCategory.OST_StructuralColumns)
        .WhereElementIsNotElementType()
        .ToElements()
    )
    columns.extend(list(
        FilteredElementCollector(link_doc)
        .OfCategory(BuiltInCategory.OST_Columns)
        .WhereElementIsNotElementType()
        .ToElements()
    ))

    for column in columns:
        if bottom_height is not None and top_height is not None:
            try:
                bbox = column.get_BoundingBox(None)
                if bbox:
                    elem_bottom = transform.OfPoint(bbox.Min).Z
                    elem_top = transform.OfPoint(bbox.Max).Z
                    if not (elem_bottom < top_height
                            and elem_top > bottom_height):
                        continue
            except:
                pass

        col_loc = column.Location
        if not col_loc or not hasattr(col_loc, 'Point'):
            continue

        col_point = transform.OfPoint(col_loc.Point)
        dist = point_to_line_distance_2d(
            col_point.X, col_point.Y,
            line_start.X, line_start.Y,
            line_end.X, line_end.Y
        )

        if dist < 3.28:
            crossing.append((column, link_info))

    return crossing


# =============================================================================
# DIMENSION CREATION
# =============================================================================
def create_dimension_from_refs(refs, measure_line, view, dim_type_id,
                              offset_ft=0.0):
    """Maak een dimension van een lijst references, offset loodrecht in feet."""
    if len(refs) < 2:
        return None

    refs.sort(key=lambda x: x[1])

    ref_array = ReferenceArray()
    for ref, t in refs:
        ref_array.Append(ref)

    line_start = measure_line.GetEndPoint(0)
    line_end = measure_line.GetEndPoint(1)
    line_dir = (line_end - line_start).Normalize()

    # Loodrechte richting (90 graden in XY vlak)
    perp_dir = XYZ(-line_dir.Y, line_dir.X, 0)

    new_start = XYZ(
        line_start.X + perp_dir.X * offset_ft,
        line_start.Y + perp_dir.Y * offset_ft,
        line_start.Z
    )
    new_end = XYZ(
        line_end.X + perp_dir.X * offset_ft,
        line_end.Y + perp_dir.Y * offset_ft,
        line_end.Z
    )

    dim_line = Line.CreateBound(new_start, new_end)

    dim = None
    try:
        if dim_type_id:
            dim_type = doc.GetElement(dim_type_id)
            dim = doc.Create.NewDimension(view, dim_line, ref_array, dim_type)
        else:
            dim = doc.Create.NewDimension(view, dim_line, ref_array)
    except:
        return None

    if dim is None:
        return None

    # Een maatlijn met twee maatpunten heeft EEN segment, en dat rapporteert
    # Revit als NumberOfSegments 0 met een LEGE Segments-collectie; de maat
    # staat dan op Dimension.Value. Testen op Segments.Size keurde die
    # maatlijnen dus af terwijl ze gewoon klopten - gemeten op 2026-09-09:
    # drie vloerrandmaten van 3845, 5150 en 1300 mm bleven als wees in het
    # model staan terwijl de hulplijn niet werd opgeruimd.
    bruikbaar = False
    try:
        if dim.NumberOfSegments > 0:
            bruikbaar = True
        else:
            bruikbaar = dim.Value is not None
    except:
        bruikbaar = False

    if bruikbaar:
        return dim

    # Echt onbruikbaar: opruimen, anders blijft er een lege maatlijn achter.
    try:
        doc.Delete(dim.Id)
    except Exception as del_ex:
        log.warning("Onbruikbare maatlijn niet verwijderd: {}".format(del_ex))

    return None


def create_dimension_guarded(refs, measure_line, view, dim_type_id, offset_ft):
    """Maak een maatlijn en garandeer dat er geen segment van 0 mm in staat.

    Ontdubbelen op de vooraf berekende posities vangt niet alles: twee
    references kunnen op precies dezelfde plek uitkomen zonder dat die
    posities dat verraden - bij deurstijlen zijn ze zelfs bewust fictief en
    bepalen ze alleen de volgorde. Of er echt een nulsegment ontstaat is pas
    na het aanmaken te zien. Daarom hier meten en zo nodig opnieuw opbouwen
    zonder het dubbele punt.

    Let op: dit filtert op SAMENVALLENDE punten, niet op kleine maten. Een
    wand van 15 mm hoort gewoon bemaat te worden.
    """
    werk = sorted(refs, key=lambda x: x[1])

    for poging in range(MAX_ZERO_SEGMENT_RETRIES):
        dim = create_dimension_from_refs(
            werk, measure_line, view, dim_type_id, offset_ft
        )
        if dim is None:
            return None

        schuldig = None
        try:
            if dim.NumberOfSegments == 0:
                # Enkelsegment: de maat staat op de dimension zelf. Valt die
                # samen, dan valt er niets weg te laten - er zijn maar twee
                # maatpunten.
                waarde = dim.Value
                if (waarde is not None
                        and abs(waarde) < ZERO_SEGMENT_TOLERANCE_FT):
                    log.warning("Maatlijn van 0 mm - twee samenvallende "
                                "maatpunten, verwijderd")
                    doc.Delete(dim.Id)
                    return None
                return dim

            if dim.NumberOfSegments > 1:
                for index, segment in enumerate(dim.Segments):
                    if abs(segment.Value) < ZERO_SEGMENT_TOLERANCE_FT:
                        # Segment index ligt tussen maatpunt index en index+1;
                        # het tweede punt is de dubbele.
                        schuldig = index + 1
                        break
        except:
            return dim

        if schuldig is None:
            return dim

        log.warning("Nulsegment op positie {} - maatpunt verwijderd en "
                    "opnieuw opgebouwd (poging {})"
                    .format(schuldig, poging + 1))
        doc.Delete(dim.Id)
        werk.pop(schuldig)

        if len(werk) < 2:
            return None

    log.error("Nulsegment blijft terugkomen na {} pogingen - geen maatlijn"
              .format(MAX_ZERO_SEGMENT_RETRIES))
    return None


def collect_references(measure_line, options, view, walls, columns,
                       bottom_height, top_height, linked_models, floors):
    """Verzamel maatpunten per categorie.

    Returns:
        dict: categorie -> lijst van (Reference, positie langs de maatlijn)
    """
    include_linked = options.get('include_linked', False)
    per_categorie = {}

    # GRIDS
    if options['include_grids']:
        per_categorie['grids'] = get_grid_references(measure_line, view)

    # WANDEN - min_thickness is een DIKTE-filter. Korte wanden worden nooit
    # weggefilterd, ook niet als ze maar 15 mm lang zijn.
    if options['include_walls']:
        wall_refs = []
        crossing_walls = find_crossing_walls(
            measure_line, walls, bottom_height, top_height,
            options['min_thickness']
        )
        log.info("Crossing walls (host): {}".format(len(crossing_walls)))

        for wall in crossing_walls:
            wall_refs.extend(get_wall_face_references(wall, measure_line, view))

        if include_linked and linked_models:
            for link_info in linked_models:
                linked_walls = find_crossing_linked_walls(
                    measure_line, link_info,
                    bottom_height, top_height, options['min_thickness']
                )
                log.info("Crossing walls (linked {}): {}".format(
                    link_info['doc'].Title, len(linked_walls)
                ))
                for wall, li in linked_walls:
                    wall_refs.extend(get_linked_wall_face_references(
                        wall, measure_line, view,
                        li['doc'], li['instance'], li['transform']
                    ))

        per_categorie['walls'] = wall_refs

    # KOLOMMEN
    if options['include_columns']:
        column_refs = []
        crossing_cols = find_crossing_columns(
            measure_line, columns, bottom_height, top_height
        )
        log.info("Crossing columns (host): {}".format(len(crossing_cols)))

        for col in crossing_cols:
            column_refs.extend(
                get_column_face_references(col, measure_line, view)
            )

        if include_linked and linked_models:
            for link_info in linked_models:
                linked_cols = find_crossing_linked_columns(
                    measure_line, link_info, bottom_height, top_height
                )
                for col, li in linked_cols:
                    column_refs.extend(get_linked_column_face_references(
                        col, measure_line, view,
                        li['doc'], li['instance'], li['transform']
                    ))

        per_categorie['columns'] = column_refs

    # VLOERANDEN
    if options.get('include_floors', False):
        floor_refs = []
        crossing_floors = find_crossing_floors(measure_line, floors or [])
        log.info("Crossing floors (host): {}".format(len(crossing_floors)))

        for floor in crossing_floors:
            floor_refs.extend(
                get_floor_edge_references(floor, measure_line, view)
            )

        if include_linked and linked_models:
            for link_info in linked_models:
                for floor, li in find_crossing_linked_floors(
                        measure_line, link_info):
                    floor_refs.extend(get_floor_edge_references(
                        floor, measure_line, view,
                        li['transform'], li['doc'], li['instance']
                    ))

        per_categorie['floors'] = floor_refs

    # RUIMTESCHEIDINGSLIJNEN
    if options.get('include_rooms', False):
        room_refs = get_room_boundary_references(measure_line, view)
        log.info("Ruimtescheidingslijnen (host): {}".format(len(room_refs)))

        if include_linked and linked_models:
            log.warning("Ruimtescheidingslijnen in linked models worden niet "
                        "bemaat - curve-references zijn daar niet bruikbaar")

        per_categorie['rooms'] = room_refs

    # DEUREN - wandeinden + kozijnstijlen
    if options.get('include_doors', False):
        door_refs = []
        door_walls = find_walls_along_line(
            measure_line, walls, options['min_thickness'],
            options.get('preselected_walls')
        )
        log.info("Wanden voor deurmaatvoering: {}".format(len(door_walls)))

        if include_linked and linked_models:
            log.warning("Deuren in linked models worden niet bemaat - "
                        "referenties van inserts zijn daar niet bruikbaar")

        for wall in door_walls:
            door_refs.extend(
                get_wall_opening_references(wall, measure_line, view)
            )

        per_categorie['doors'] = door_refs

    return dict((k, v) for k, v in per_categorie.items() if v)


def create_dimensions(
    measure_line, options, view, walls, columns,
    bottom_height, top_height, linked_models=None, floors=None
):
    """Maak per maatlijnnummer een maatlijn.

    Achter elke categorie staat een nummer. Categorieen met hetzelfde nummer
    komen samen in dezelfde maatlijn. Nummer 1 ligt exact op de getekende
    lijn, elk volgend nummer een instelbare afstand verder.

    Returns:
        tuple: (lijst van (nummer, dimension), dict maatpunten per categorie)
    """
    dim_type_id = options.get('dim_type_id')
    offset_ft = options.get('offset_mm', DEFAULT_LINE_OFFSET_MM) / MM_PER_FOOT
    nummers = options.get('line_numbers') or {}

    per_categorie = collect_references(
        measure_line, options, view, walls, columns,
        bottom_height, top_height, linked_models, floors
    )

    herkomst = {}
    for sleutel, refs in per_categorie.items():
        herkomst[sleutel] = len(refs)

    groepen = {}
    for sleutel, refs in per_categorie.items():
        nummer = nummers.get(sleutel, 1)
        if nummer not in groepen:
            groepen[nummer] = []
        groepen[nummer].extend(refs)

    # TOTAAL is geen eigen bron maar de buitenste twee punten van alles wat
    # verzameld is. Staat het op hetzelfde nummer als een andere categorie,
    # dan ontdubbelt het vanzelf weg - die punten zaten er al in.
    if options['include_total']:
        alles = dedupe_references(
            [r for refs in per_categorie.values() for r in refs]
        )
        if len(alles) >= 2:
            nummer = nummers.get('total', 1)
            if nummer not in groepen:
                groepen[nummer] = []
            groepen[nummer].extend([alles[0], alles[-1]])
            herkomst['total'] = 2

    resultaat = []
    for nummer in sorted(groepen):
        refs = dedupe_references(groepen[nummer])
        if len(refs) < 2:
            log.warning("Maatlijn {}: te weinig maatpunten ({})"
                        .format(nummer, len(refs)))
            continue

        dim = create_dimension_guarded(
            refs, measure_line, view, dim_type_id, (nummer - 1) * offset_ft
        )
        if dim:
            resultaat.append((nummer, dim))
            log.info("Maatlijn {} geplaatst met {} maatpunten"
                     .format(nummer, len(refs)))

    return resultaat, herkomst



def process_measure_line(element, options, view, walls, columns,
                         bottom_height, top_height, linked_models, floors):
    """Bemaat een enkele hulplijn.

    Eigen transactie per lijn: bij bulkverwerking mag een lijn die niets
    oplevert de rest niet meeslepen, en blijft elke lijn los terug te draaien.

    Returns:
        tuple: (aantal maatlijnen, maatpunten per categorie, hulplijn opgeruimd)
    """
    measure_line = get_line_from_element(element)
    if not measure_line:
        return 0, {}, False

    log.info("Lijn: {:.2f}m".format(measure_line.Length * 0.3048))

    geplaatst = []
    herkomst = {}
    lijn_weg = False

    with revit.Transaction("AutoDim"):
        geplaatst, herkomst = create_dimensions(
            measure_line, options, view, walls, columns,
            bottom_height, top_height, linked_models, floors
        )

        created = len(geplaatst) > 0
        if created:
            log.info("Maatlijnen {} geplaatst: {}".format(
                [n for n, _ in geplaatst], herkomst
            ))
        else:
            # Bewust laten staan: zonder maatlijn is de lijn het enige spoor
            # naar wat er mis ging. Wordt na afloop geselecteerd.
            log.warning("Geen maatpunten gevonden - hulplijn {} blijft staan"
                        .format(element.Id))

        # Hulplijn opruimen - alleen als er ook echt iets geplaatst is,
        # anders raakt de gebruiker zijn lijn kwijt zonder resultaat. De
        # dimension refereert wandvlakken, niet de lijn, dus verwijderen
        # is veilig.
        if created and options.get('delete_line', True):
            try:
                doc.Delete(element.Id)
                lijn_weg = True
                log.info("Hulplijn verwijderd")
            except Exception as del_ex:
                log.warning("Hulplijn niet verwijderd: {}".format(del_ex))

    return len(geplaatst), herkomst, lijn_weg



# =============================================================================
# UI
# =============================================================================
class AutoDimWindow(WPFWindow):
    def __init__(self):
        xaml_file = os.path.join(os.path.dirname(__file__), 'UI.xaml')
        super(AutoDimWindow, self).__init__(xaml_file, "AutoDim", width=450, height=None)
        self.options = None
        self.dim_types = get_dimension_types()
        self._populate_dim_types()
        self._bind_events()

    def _populate_dim_types(self):
        """Vul dimension type combobox"""
        for dt_id, dt_name in self.dim_types:
            self.cmb_type.Items.Add(dt_name)

        default_idx = find_default_dim_type_index(self.dim_types)
        if default_idx >= 0:
            self.cmb_type.SelectedIndex = default_idx

    def _bind_events(self):
        """Bind button events"""
        if self.btn_start:
            self.btn_start.Click += self._on_run

    def _on_run(self, sender, args):
        dim_type_id = None
        if self.cmb_type.SelectedIndex >= 0 and self.cmb_type.SelectedIndex < len(self.dim_types):
            dim_type_id = self.dim_types[self.cmb_type.SelectedIndex][0]

        try:
            min_thickness = float(self.txt_thickness.Text)
        except (ValueError, TypeError):
            min_thickness = 20.0

        try:
            offset_mm = float(self.txt_offset.Text)
        except (ValueError, TypeError):
            offset_mm = DEFAULT_LINE_OFFSET_MM

        self.options = {
            'include_grids': self.chk_grids.IsChecked == True,
            'include_total': self.chk_total.IsChecked == True,
            'include_walls': self.chk_walls.IsChecked == True,
            'include_columns': self.chk_columns.IsChecked == True,
            'include_floors': self.chk_floors.IsChecked == True,
            'include_rooms': self.chk_rooms.IsChecked == True,
            'include_doors': self.chk_doors.IsChecked == True,
            'delete_line': self.chk_delete_line.IsChecked == True,
            'use_line_style': self.chk_line_style.IsChecked == True,
            'line_style_name': (self.txt_line_style.Text or '').strip(),
            'include_linked': self.chk_linked.IsChecked == True,
            'min_thickness': min_thickness,
            'offset_mm': offset_mm,
            'line_numbers': self._read_line_numbers(),
            'dim_type_id': dim_type_id
        }
        self.close_ok()

    def _read_line_numbers(self):
        """Lees het maatlijnnummer achter elke categorie.

        Onleesbaar of leeg valt terug op 1: dan komt alles alsnog op de
        getekende lijn terecht in plaats van dat er niets gebeurt.
        """
        nummers = {}
        for sleutel in LINE_NUMBER_KEYS:
            vak = getattr(self, 'txt_{}_line'.format(sleutel), None)
            nummer = 1
            if vak is not None:
                try:
                    nummer = int(float(vak.Text))
                except (ValueError, TypeError):
                    nummer = 1
            nummers[sleutel] = max(1, nummer)

        return nummers


# =============================================================================
# MAIN
# =============================================================================
def main():
    global doc, uidoc, active_view
    
    # Document check - hier, niet op module-niveau!
    doc = revit.doc
    uidoc = revit.uidoc
    
    if not doc:
        forms.alert("Open eerst een Revit project.", title="AutoDim")
        return
    
    active_view = doc.ActiveView

    log.log_revit_info()
    log.section("Main")

    # Voorselectie vastleggen VOOR de UI: PickObject wist de selectie later.
    # Alleen wanden zijn bruikbaar (deurmaatvoering).
    preselected_walls = []
    try:
        for sel_id in uidoc.Selection.GetElementIds():
            sel_el = doc.GetElement(sel_id)
            if isinstance(sel_el, Wall):
                preselected_walls.append(sel_el)
    except:
        pass

    if preselected_walls:
        log.info("Voorgeselecteerde wanden: {}".format(len(preselected_walls)))

    if not isinstance(active_view, ViewPlan):
        forms.alert("Alleen in plattegronden.", title="AutoDim")
        return
    
    # Haal view range voor cutplane filter
    bottom_height, top_height = get_view_cut_plane_height(active_view)
    # float() is essentieel: IronPython 2.7 weigert "{:.2f}" op een int
    # ("Precision not allowed in integer format specifier"). `x or 0` levert
    # een int 0 op zodra x None of 0.0 is (peil 0 met bottom offset 0).
    log.info("View range: {:.2f} - {:.2f}".format(
        float(bottom_height or 0), float(top_height or 0)
    ))
    
    # Toon UI
    window = AutoDimWindow()
    if not window.show_dialog():
        return

    options = window.options
    options['preselected_walls'] = preselected_walls
    log.log_options(options)
    
    # Check of er iets geselecteerd is
    if not any([options['include_grids'], options['include_total'],
                options['include_walls'], options['include_columns'],
                options.get('include_floors', False),
                options.get('include_rooms', False),
                options.get('include_doors', False)]):
        forms.alert("Selecteer minimaal één optie.", title="AutoDim")
        return
    
    # Haal elementen in view — host document
    walls = list(
        FilteredElementCollector(doc, active_view.Id)
        .OfClass(Wall).ToElements()
    )
    log.info("Walls in view (host): {}".format(len(walls)))

    columns = list(
        FilteredElementCollector(doc, active_view.Id)
        .OfCategory(BuiltInCategory.OST_StructuralColumns)
        .WhereElementIsNotElementType()
        .ToElements()
    )
    columns.extend(list(
        FilteredElementCollector(doc, active_view.Id)
        .OfCategory(BuiltInCategory.OST_Columns)
        .WhereElementIsNotElementType()
        .ToElements()
    ))
    log.info("Columns in view (host): {}".format(len(columns)))

    floors = []
    if options.get('include_floors', False):
        floors = list(
            FilteredElementCollector(doc, active_view.Id)
            .OfCategory(BuiltInCategory.OST_Floors)
            .WhereElementIsNotElementType()
            .ToElements()
        )
        log.info("Floors in view (host): {}".format(len(floors)))

    # Linked models (alleen laden als checkbox actief)
    linked_models = []
    if options.get('include_linked', False):
        linked_models = get_linked_models(active_view)
        log.info("Linked models loaded: {}".format(len(linked_models)))
    
    dim_count = 0
    line_count = 0
    per_type = {}
    lege_lijnen = []

    def verwerk(element):
        """Bemaat een lijn en tel het resultaat mee in de totalen."""
        aantal, herkomst, lijn_weg = process_measure_line(
            element, options, active_view, walls, columns,
            bottom_height, top_height, linked_models, floors
        )
        if aantal:
            for sleutel, punten in herkomst.items():
                per_type[sleutel] = per_type.get(sleutel, 0) + punten
        else:
            lege_lijnen.append(element.Id)
        return aantal, lijn_weg

    # Automatisch: alle lijnen met de ingestelde lijnstijl in deze view.
    # Niets aanwijzen, alles in een keer.
    handmatig = True
    if options.get('use_line_style', False):
        stijl_naam = options.get('line_style_name') or DEFAULT_MEASURE_LINE_STYLE
        auto_lijnen = find_lines_by_style(active_view, stijl_naam)
        log.info("Lijnen met stijl '{}': {}".format(stijl_naam, len(auto_lijnen)))

        if auto_lijnen:
            handmatig = False
            for element in auto_lijnen:
                try:
                    aantal, lijn_weg = verwerk(element)
                except Exception:
                    log.exception("Lijn overgeslagen")
                    lege_lijnen.append(element.Id)
                    continue
                dim_count += aantal
                if lijn_weg:
                    line_count += 1
        else:
            # Geen enkele lijn met die stijl: dan liever alsnog handmatig
            # aanwijzen dan de gebruiker met lege handen wegsturen.
            forms.alert(
                "Geen lijnen gevonden met lijnstijl '{}' in deze view.\n\n"
                "Wijs de lijnen handmatig aan (ESC om te stoppen)."
                .format(stijl_naam),
                title="AutoDim"
            )

    # Handmatig: loop tot ESC
    if handmatig:
        line_filter = DetailLineFilter()

        while True:
            try:
                ref = uidoc.Selection.PickObject(
                    ObjectType.Element,
                    line_filter,
                    "Selecteer Detail Line (ESC om te stoppen)"
                )

                element = doc.GetElement(ref.ElementId)
                aantal, lijn_weg = verwerk(element)
                dim_count += aantal
                if lijn_weg:
                    line_count += 1

            except OperationCanceledException:
                log.info("Selection cancelled by user")
                break
            except Exception as ex:
                if "cancel" in str(ex).lower() or "aborted" in str(ex).lower():
                    break
                log.exception("Error during selection")
                break

    # Klaar
    if dim_count > 0:
        # Per type benoemen: anders lijkt een maatlijn uit een aangevinkte
        # optie "zomaar" te verschijnen.
        labels = {
            'grids': 'Grids',
            'walls': 'Wanden',
            'columns': 'Kolommen',
            'floors': 'Vloeranden',
            'rooms': 'Ruimtescheiding',
            'doors': 'Deuren',
            'total': 'Totaal',
        }
        regels = ["{} maatlijn(en) geplaatst.".format(dim_count), "",
                  "Maatpunten per bron:"]
        for sleutel in ('grids', 'walls', 'columns', 'floors', 'rooms',
                        'doors', 'total'):
            if per_type.get(sleutel):
                regels.append("  {} : {}".format(
                    labels[sleutel].ljust(14), per_type[sleutel]
                ))
        if line_count:
            regels.append("")
            regels.append("{} hulplijn(en) opgeruimd.".format(line_count))

        if lege_lijnen:
            regels.append("")
            regels.append("{} hulplijn(en) leverden geen maatpunten op en "
                          "zijn blijven staan.".format(len(lege_lijnen)))
            regels.append("Die staan nu geselecteerd - ZS om erheen te zoomen.")

        forms.alert("\n".join(regels), title="AutoDim")

    elif lege_lijnen:
        # Niets geplaatst: dan is het nuttiger om te zeggen WELKE lijnen
        # niets opleverden dan om zwijgend af te sluiten.
        forms.alert(
            "Geen maatlijnen geplaatst.\n\n"
            "{} hulplijn(en) leverden geen maatpunten op; die staan nu "
            "geselecteerd (ZS om erheen te zoomen).\n\n"
            "Meestal betekent dit dat de lijn geen wand kruist, of dat de "
            "wanden in een linked model zitten terwijl die optie uit staat."
            .format(len(lege_lijnen)),
            title="AutoDim"
        )

    # De niet-bemaatte lijnen selecteren: zo is in een oogopslag te zien
    # welke aandacht nodig hebben, zonder ze te hoeven zoeken.
    if lege_lijnen:
        try:
            selectie = List[ElementId]()
            for lijn_id in lege_lijnen:
                selectie.Add(lijn_id)
            uidoc.Selection.SetElementIds(selectie)
        except Exception as sel_ex:
            log.warning("Selectie van lege hulplijnen mislukt: {}".format(sel_ex))

    log.finalize(True, "{} dimensions created, {} lijnen zonder resultaat"
                 .format(dim_count, len(lege_lijnen)))


if __name__ == "__main__":
    try:
        main()
    except Exception as ex:
        log.exception("Error")
        log.finalize(False)
        raise
