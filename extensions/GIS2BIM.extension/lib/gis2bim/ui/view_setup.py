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


def find_view_by_name(doc, view_name):
    """Zoek een niet-template view op exacte naam.

    Args:
        doc: Revit Document
        view_name: Exacte viewnaam

    Returns:
        View element, of None
    """
    for view in DB.FilteredElementCollector(doc).OfClass(DB.View):
        if view.IsTemplate:
            continue
        try:
            if view.Name == view_name:
                return view
        except Exception:
            pass
    return None


def get_or_create_plan_view(doc, view_name, log=None):
    """Haal een plattegrond met deze naam op, of maak hem aan.

    Voor tools die altijd in hun eigen vaste view tekenen. De aanroeper
    moet ZELF een transactie open hebben staan wanneer de view mogelijk
    aangemaakt moet worden.

    Args:
        doc: Revit Document
        view_name: Naam van de view (bijv. "GIS2BIM_OSM")
        log: Optionele logfunctie

    Returns:
        View element, of None als aanmaken niet lukte
    """
    if log is None:
        log = lambda msg: None

    view = find_view_by_name(doc, view_name)
    if view is not None:
        log("View gevonden: {0}".format(view_name))
        return view

    try:
        vft = None
        for kandidaat in DB.FilteredElementCollector(doc).OfClass(
                DB.ViewFamilyType):
            if kandidaat.ViewFamily == DB.ViewFamily.FloorPlan:
                vft = kandidaat
                break
        if vft is None:
            log("Geen FloorPlan ViewFamilyType in dit project")
            return None

        levels = list(DB.FilteredElementCollector(doc).OfClass(DB.Level))
        if not levels:
            log("Geen Level in dit project - view kan niet aangemaakt worden")
            return None

        # Laagste level: daar ligt het maaiveld/GIS-materiaal
        levels.sort(key=lambda lv: lv.Elevation)

        view = DB.ViewPlan.Create(doc, vft.Id, levels[0].Id)
        view.Name = view_name
        log("View aangemaakt: {0} (level '{1}')".format(
            view_name, levels[0].Name))
        return view

    except Exception as e:
        log("Aanmaken view '{0}' mislukt: {1}".format(view_name, e))
        return None
