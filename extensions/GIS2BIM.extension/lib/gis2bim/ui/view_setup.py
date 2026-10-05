# -*- coding: utf-8 -*-
"""
View Setup - GIS2BIM
====================

Gedeelde view dropdown populatie voor GIS2BIM pyRevit tools.

Gebruik:
    from gis2bim.ui.view_setup import populate_view_dropdown

    populate_view_dropdown(self.cmb_view, doc)
"""

from pyrevit import DB
from System.Windows.Controls import ComboBoxItem


# Standaard view types voor 2D tools (BGT, WFS)
VIEW_TYPES_2D = [
    DB.ViewType.FloorPlan,
    DB.ViewType.CeilingPlan,
    DB.ViewType.AreaPlan,
    DB.ViewType.DraftingView,
]

# View types inclusief 3D (AHN)
VIEW_TYPES_3D = [
    DB.ViewType.ThreeD,
    DB.ViewType.FloorPlan,
    DB.ViewType.CeilingPlan,
    DB.ViewType.AreaPlan,
]


def populate_view_dropdown(combo, doc, view_types=None, select_active=True,
                           log=None):
    """Vul een ComboBox met beschikbare Revit views.

    Args:
        combo: WPF ComboBox element
        doc: Revit Document
        view_types: Lijst van DB.ViewType waarden om te filteren.
                    Default: VIEW_TYPES_2D
        select_active: Als True, selecteer de actieve view (default True)
        log: Optionele log functie

    Returns:
        De geselecteerde view, of None
    """
    if log is None:
        log = lambda msg: None

    if view_types is None:
        view_types = VIEW_TYPES_2D

    try:
        collector = DB.FilteredElementCollector(doc)
        views = collector.OfClass(DB.View).ToElements()

        suitable_views = []
        for view in views:
            if view.IsTemplate:
                continue
            if view.ViewType in view_types:
                suitable_views.append(view)

        suitable_views.sort(key=lambda v: v.Name)

        combo.Items.Clear()
        active_view_id = doc.ActiveView.Id

        for view in suitable_views:
            item = ComboBoxItem()
            item.Content = view.Name
            item.Tag = view.Id
            combo.Items.Add(item)

            if select_active and view.Id == active_view_id:
                combo.SelectedItem = item

        if combo.SelectedItem is None and combo.Items.Count > 0:
            combo.SelectedIndex = 0

    except Exception as e:
        log("Error loading views: {0}".format(e))


def get_selected_view(combo, doc):
    """Haal de geselecteerde view op uit een ComboBox.

    Args:
        combo: WPF ComboBox element (gevuld door populate_view_dropdown)
        doc: Revit Document

    Returns:
        View element, of None
    """
    item = combo.SelectedItem
    if item and hasattr(item, 'Tag'):
        return doc.GetElement(item.Tag)
    return None


# Standaard voor alle vaste GIS2BIM-views: naam in kleine letters, view
# template MLA_SITUATIE_01_1/1000 en dezelfde crop als de bestaande
# GIS2BIM-views in de template (A3-vlak op 1:1000).
GIS2BIM_VIEW_PREFIX = "gis2bim"
GIS2BIM_VIEW_TEMPLATE = "MLA_SITUATIE_01_1/1000"


def _view_name(view):
    try:
        return view.Name
    except Exception:
        return None


def find_view_by_name(doc, view_name, log=None):
    """Zoek een niet-template view op naam, hoofdletterongevoelig.

    Een exacte match wint. Anders telt een match zonder hoofdlettergebruik
    (GIS2BIM_luchtfoto == gis2bim_luchtfoto). Zijn er meerdere, dan wint de
    view met een view template en wordt de dubbeling gelogd.

    Args:
        doc: Revit Document
        view_name: Viewnaam
        log: Optionele logfunctie

    Returns:
        View element, of None
    """
    if log is None:
        log = lambda msg: None

    gezocht = view_name.lower()
    exact = None
    kandidaten = []
    for view in DB.FilteredElementCollector(doc).OfClass(DB.View):
        if view.IsTemplate:
            continue
        naam = _view_name(view)
        if naam is None:
            continue
        if naam == view_name:
            exact = view
        elif naam.lower() == gezocht:
            kandidaten.append(view)

    if exact is not None:
        return exact
    if not kandidaten:
        return None
    if len(kandidaten) > 1:
        log("Meerdere views heten '{0}' (hoofdletters genegeerd): {1}".format(
            view_name, ", ".join(_view_name(v) for v in kandidaten)))
        met_template = [v for v in kandidaten
                        if v.ViewTemplateId != DB.ElementId.InvalidElementId]
        if met_template:
            return met_template[0]
    return kandidaten[0]


def find_view_template(doc, template_name):
    """Zoek een view template op exacte naam. Returns View of None."""
    for view in DB.FilteredElementCollector(doc).OfClass(DB.View):
        if view.IsTemplate and _view_name(view) == template_name:
            return view
    return None


def _find_reference_view(doc, template):
    """Bestaande GIS2BIM-plattegrond waarvan crop en level overgenomen worden.

    Kiest uit de plattegronden met 'gis2bim' in de naam, actieve crop en een
    niet-gedraaide cropbox (bij voorkeur met dezelfde view template) de
    cropmaat die het vaakst voorkomt.
    """
    kandidaten = []
    for view in DB.FilteredElementCollector(doc).OfClass(DB.ViewPlan):
        if view.IsTemplate or not view.CropBoxActive:
            continue
        naam = _view_name(view)
        if not naam or GIS2BIM_VIEW_PREFIX not in naam.lower():
            continue
        try:
            if not view.CropBox.Transform.IsIdentity:
                continue
        except Exception:
            continue
        kandidaten.append(view)

    if template is not None:
        met_template = [v for v in kandidaten
                        if v.ViewTemplateId == template.Id]
        if met_template:
            kandidaten = met_template
    if not kandidaten:
        return None

    def sleutel(view):
        box = view.CropBox
        return (round(box.Min.X, 2), round(box.Min.Y, 2),
                round(box.Max.X, 2), round(box.Max.Y, 2))

    telling = {}
    for view in kandidaten:
        k = sleutel(view)
        telling[k] = telling.get(k, 0) + 1
    beste = max(telling, key=lambda k: telling[k])
    for view in kandidaten:
        if sleutel(view) == beste:
            return view
    return None


def _lowest_level(doc):
    levels = list(DB.FilteredElementCollector(doc).OfClass(DB.Level))
    if not levels:
        return None
    levels.sort(key=lambda lv: lv.Elevation)
    return levels[0]


def _floor_plan_type(doc):
    for vft in DB.FilteredElementCollector(doc).OfClass(DB.ViewFamilyType):
        if vft.ViewFamily == DB.ViewFamily.FloorPlan:
            return vft
    return None


def create_gis2bim_plan_view(doc, view_name, log=None, warnings=None):
    """Maak een GIS2BIM-plattegrond volgens de standaard.

    Naam in kleine letters, level en crop van een bestaande GIS2BIM-view,
    view template GIS2BIM_VIEW_TEMPLATE. Wat niet lukt komt in `warnings`
    (en in de log), zodat de tool het aan de gebruiker kan melden.

    De aanroeper moet ZELF een transactie open hebben staan.

    Returns:
        View element, of None als aanmaken niet lukte
    """
    if log is None:
        log = lambda msg: None
    if warnings is None:
        warnings = []

    def meld(msg):
        log(msg)
        warnings.append(msg)

    view_name = view_name.lower()

    vft = _floor_plan_type(doc)
    if vft is None:
        meld("Geen FloorPlan ViewFamilyType - view '{0}' niet "
             "aangemaakt".format(view_name))
        return None

    template = find_view_template(doc, GIS2BIM_VIEW_TEMPLATE)
    referentie = _find_reference_view(doc, template)

    level = None
    if referentie is not None and referentie.GenLevel is not None:
        level = referentie.GenLevel
    if level is None:
        level = _lowest_level(doc)
    if level is None:
        meld("Geen Level in dit project - view '{0}' niet "
             "aangemaakt".format(view_name))
        return None

    try:
        view = DB.ViewPlan.Create(doc, vft.Id, level.Id)
        view.Name = view_name
    except Exception as e:
        meld("Aanmaken view '{0}' mislukt: {1}".format(view_name, e))
        return None

    if template is not None:
        try:
            view.ViewTemplateId = template.Id
        except Exception as e:
            meld("View template '{0}' niet toe te passen op '{1}': "
                 "{2}".format(GIS2BIM_VIEW_TEMPLATE, view_name, e))
    else:
        meld("View template '{0}' bestaat niet - '{1}' heeft geen "
             "template".format(GIS2BIM_VIEW_TEMPLATE, view_name))
        if referentie is not None:
            try:
                view.Scale = referentie.Scale
            except Exception:
                pass

    if referentie is not None:
        try:
            view.CropBoxActive = True
            view.CropBox = referentie.CropBox
        except Exception as e:
            meld("Crop van '{0}' niet over te nemen op '{1}': {2}".format(
                _view_name(referentie), view_name, e))
    else:
        meld("Geen bestaande GIS2BIM-view met crop gevonden - '{0}' "
             "heeft geen standaard-crop".format(view_name))

    log("View aangemaakt: {0} (level '{1}', template {2}, crop van {3})".format(
        view_name, level.Name,
        GIS2BIM_VIEW_TEMPLATE if template is not None else "-",
        _view_name(referentie) if referentie is not None else "-"))
    return view


def get_or_create_plan_view(doc, view_name, log=None, warnings=None):
    """Haal een GIS2BIM-view op (hoofdletterongevoelig) of maak hem aan.

    Voor tools die altijd in hun eigen vaste view tekenen. De aanroeper
    moet ZELF een transactie open hebben staan wanneer de view mogelijk
    aangemaakt moet worden.

    Args:
        doc: Revit Document
        view_name: Naam van de view, kleine letters (bijv. "gis2bim_osm")
        log: Optionele logfunctie
        warnings: Optionele lijst waar meldingen aan toegevoegd worden

    Returns:
        View element, of None als aanmaken niet lukte
    """
    if log is None:
        log = lambda msg: None

    view = find_view_by_name(doc, view_name, log=log)
    if view is not None:
        log("View gevonden: {0}".format(_view_name(view)))
        return view
    return create_gis2bim_plan_view(doc, view_name, log=log,
                                    warnings=warnings)


def select_view_in_dropdown(combo, view_name):
    """Selecteer een view in een gevulde dropdown, hoofdletterongevoelig.

    Returns:
        True bij een match
    """
    gezocht = view_name.lower()
    for i in range(combo.Items.Count):
        content = combo.Items[i].Content
        if content is not None and str(content).lower() == gezocht:
            combo.SelectedIndex = i
            return True
    return False
