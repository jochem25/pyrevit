# -*- coding: utf-8 -*-
"""
IFC-GUID's per project
======================

Revit exporteert IfcProject/IfcSite/IfcBuilding met de waarde uit de
gelijknamige ProjectInformation-velden. Zijn die leeg, dan leidt de exporter
de GUID af van de UniqueId van ProjectInformation - en die is in elke kopie
van een template gelijk. Elk project krijgt daarom eenmalig eigen GUID's.

Gebruikt door GIS2BIM Locatie Instellen en door de bouwkunde-knop
"Nieuw project: IFC-GUID's". Geen afhankelijkheden buiten de standaardlib;
de Revit-API wordt alleen in set_project_ifc_guids geladen.
"""

import binascii
import os

IFC_GUID_TEKENS = (
    "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz_$"
)
IFC_GUID_LENGTE = 22

# Volgorde: (parameternaam, naam van de BuiltInParameter)
IFC_GUID_PARAMS = (
    ("IfcProject GUID", "IFC_PROJECT_GUID"),
    ("IfcSite GUID", "IFC_SITE_GUID"),
    ("IfcBuilding GUID", "IFC_BUILDING_GUID"),
)

# Waarden die uit een template komen en dus in meerdere projecten voorkomen.
# Per template: opgeslagen waarden en de door de exporter afgeleide waarden.
TEMPLATE_IFC_GUIDS = frozenset([
    # GIS2BIM-master (GIS2BIM_25_v1 / GIS2BIM_25_template_v2), opgeslagen
    "1s2q5WbVH6KxJD2DJqjDgl",
    "1s2q5WbVH6KxJD2DJqjDgj",
    "1s2q5WbVH6KxJD2DJqjDgk",
    # idem, afgeleid (ProjectInformation 68bb1eb9-...-00000555)
    "2EqZq4nlnFB9HcNIZeBgbW",
    "2EqZq4nlnFB9HcNIZeBgbY",
    "2EqZq4nlnFB9HcNIZeBgbX",
    # KBA-template 2025_model (ook 2786-modellen), opgeslagen; gemeten 06-10-2026
    "3PZ8Su8NTEdQsuM17MItvm",
    "0zA0X56L966w8JjEBLepAr",
    "3PZ8Su8NTEdQsuM17MItvn",
    # idem, afgeleid (ProjectInformation 5ccf6065-...-0000045e); Site = opgeslagen
    "0zA0X56L966w8JjEBLepAt",
    "0zA0X56L966w8JjEBLepAs",
])


def ifc_guid_uit_getal(getal):
    """128-bits getal -> IFC-GUID (22 tekens, buildingSMART-base64)."""
    tekens = []
    for _ in range(IFC_GUID_LENGTE):
        tekens.append(IFC_GUID_TEKENS[getal & 0x3F])
        getal >>= 6
    return "".join(reversed(tekens))


def nieuwe_ifc_guid():
    """Willekeurige IFC-GUID (eerste teken 0-3, zoals de standaard eist)."""
    getal = int(binascii.hexlify(os.urandom(16)), 16)
    return ifc_guid_uit_getal(getal)


def is_ifc_guid(waarde):
    """Geldige IFC-GUID: 22 tekens uit de IFC-tekenset, eerste teken 0-3."""
    return (
        bool(waarde)
        and len(waarde) == IFC_GUID_LENGTE
        and waarde[0] in "0123"
        and all(t in IFC_GUID_TEKENS for t in waarde)
    )


def is_eigen_guid(waarde):
    """Heeft het veld al een eigen (niet-lege, niet-template) waarde?"""
    waarde = (waarde or "").strip()
    return bool(waarde) and waarde not in TEMPLATE_IFC_GUIDS


def bepaal_ifc_guids(huidig):
    """
    Welke IFC-GUID-velden een nieuwe waarde krijgen.

    Alleen lege velden en velden met een template-waarde; een eigen waarde
    van het project wordt nooit overschreven.

    Args:
        huidig: dict parameternaam -> huidige waarde (None of "" = leeg)

    Returns:
        dict parameternaam -> nieuwe GUID (leeg als er niets te doen is)
    """
    nieuw = {}
    for naam, _ in IFC_GUID_PARAMS:
        if is_eigen_guid(huidig.get(naam)):
            continue
        guid = nieuwe_ifc_guid()
        while guid in nieuw.values() or guid in TEMPLATE_IFC_GUIDS:
            guid = nieuwe_ifc_guid()
        nieuw[naam] = guid
    return nieuw


def lees_ifc_guid_params(project_info):
    """dict parameternaam -> beschrijfbare Parameter van ProjectInformation."""
    from Autodesk.Revit.DB import BuiltInParameter

    params = {}
    for naam, bip_naam in IFC_GUID_PARAMS:
        param = project_info.get_Parameter(getattr(BuiltInParameter, bip_naam))
        if param is None:
            param = project_info.LookupParameter(naam)
        if param is not None and not param.IsReadOnly:
            params[naam] = param
    return params


def huidige_ifc_guids(project_info):
    """dict parameternaam -> huidige waarde."""
    params = lees_ifc_guid_params(project_info)
    return dict((naam, p.AsString()) for naam, p in params.items())


def set_project_ifc_guids(project_info):
    """
    Zet eigen IFC-GUID's waar nodig. Binnen een lopende transactie aanroepen.

    Returns:
        dict parameternaam -> gezette GUID (leeg = niets gedaan)
    """
    params = lees_ifc_guid_params(project_info)
    huidig = dict((naam, p.AsString()) for naam, p in params.items())
    gezet = {}
    for naam, guid in bepaal_ifc_guids(huidig).items():
        if naam in params:
            params[naam].Set(guid)
            gezet[naam] = guid
    return gezet
