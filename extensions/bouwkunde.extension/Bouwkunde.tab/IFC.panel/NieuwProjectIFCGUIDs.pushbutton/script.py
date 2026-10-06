# -*- coding: utf-8 -*-
"""Geeft een NIEUW project eigen IFC-GUID's (IfcProject, IfcSite, IfcBuilding).

Elke kopie van een template deelt de UniqueId van ProjectInformation en
exporteert daardoor dezelfde IFC-GUID's. Deze knop zet eenmalig eigen
waarden, alleen in velden die leeg zijn of een bekende template-waarde
hebben. De logica staat in GIS2BIM.extension/lib/gis2bim/revit/ifc_guid.py.
"""
__title__ = "Nieuw project:\nIFC-GUID's"
__author__ = "3BM Bouwkunde"

import imp
import os

from pyrevit import revit, forms, script

TITEL = "Nieuw project: IFC-GUID's"

IFC_GUID_MODULE = os.path.normpath(os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "..",
    "GIS2BIM.extension", "lib", "gis2bim", "revit", "ifc_guid.py",
))

WAARSCHUWING = (
    "Alleen bij een NIEUW project.\n\n"
    "Een project dat al in Dalux/BIMcollab of bij derden staat, krijgt "
    "anders een andere identiteit.\n\n"
    "Doorgaan?"
)


def main():
    if not os.path.exists(IFC_GUID_MODULE):
        forms.alert(
            "Module niet gevonden:\n{0}".format(IFC_GUID_MODULE), title=TITEL
        )
        return
    ifc_guid = imp.load_source("gis2bim_ifc_guid", IFC_GUID_MODULE)

    doc = revit.doc
    project_info = doc.ProjectInformation
    huidig = ifc_guid.huidige_ifc_guids(project_info)
    te_zetten = ifc_guid.bepaal_ifc_guids(huidig)

    if not te_zetten:
        regels = ["Dit project heeft al eigen IFC-GUID's. Er is niets gewijzigd.", ""]
        for naam, _ in ifc_guid.IFC_GUID_PARAMS:
            regels.append("{0}: {1}".format(naam, huidig.get(naam)))
        forms.alert("\n".join(regels), title=TITEL)
        return

    if not forms.alert(WAARSCHUWING, title=TITEL, ok=True, cancel=True):
        return

    with revit.Transaction(TITEL, doc=doc):
        gezet = ifc_guid.set_project_ifc_guids(project_info)

    output = script.get_output()
    output.print_md("## {0}".format(TITEL))
    output.print_md("Document: **{0}**".format(doc.Title))
    for naam, _ in ifc_guid.IFC_GUID_PARAMS:
        if naam in gezet:
            output.print_md("- {0}: `{1}` -> `{2}`".format(
                naam, huidig.get(naam) or "(leeg)", gezet[naam]
            ))
        else:
            output.print_md("- {0}: `{1}` (eigen waarde, ongewijzigd)".format(
                naam, huidig.get(naam)
            ))


try:
    main()
except Exception as e:
    forms.alert("Fout: {0}".format(e), title=TITEL)
