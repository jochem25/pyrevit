# -*- coding: utf-8 -*-
"""WipeConstraints - Constraints wissen, ook binnen een geopende group.

Nabouw van pyRevit's "Wipe Attached Constraints", aangepast op gegroepeerde
modellen. Verschillen met het origineel:

1. Een geselecteerde Group wordt uitgeklapt naar zijn members (recursief).
   pyRevit zoekt constraints op het id van de group zelf en vindt daarom
   niets - gemeten op PVG_TO_BWK_OXS: 0 tegen 50 op group 04_stramienen.
2. Detectie via Element.GetDependentElements() in plaats van een
   OST_Constraints-collector over het hele document. Dat vindt ook de
   automatische sketch-dimensions (OST_WeakDims) die groups op slot zetten,
   en het is niet afhankelijk van de documentgrootte. Zelfde methode als de
   bridge-tool unblock_group.
3. Classificatie voor het wissen:
      OST_WeakDims    - automatische sketch-dims, onzichtbare boekhouding
      OST_Constraints - locks en uitlijnbeperkingen
      overig          - ZICHTBARE user-maatvoering; wordt NIET aangeraakt
                        tenzij expliciet aangevinkt in de bevestiging
4. Werkt binnen Edit Group-modus: Revit heeft daar al een transactie open,
   dus valt het script terug op SubTransaction.
5. Bounding-box-bewaking: verschuift er iets meer dan 0,1 mm, dan rollback.

LET OP: constraints die member zijn van een group kunnen door Revit alleen
worden verwijderd terwijl die group in Edit Group open staat. Buiten die
modus geeft Revit "ElementId cannot be deleted". Het script meldt dat
expliciet in plaats van stil te falen.

Auteur: 3BM Bouwkunde
Versie: 1.1.0
"""

import os
import sys

SCRIPT_DIR = os.path.dirname(__file__)
EXTENSION_DIR = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.dirname(SCRIPT_DIR)))
)
LIB_DIR = os.path.join(EXTENSION_DIR, 'lib')
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

from bm_logger import get_logger

from Autodesk.Revit.DB import (
    BuiltInCategory,
    Dimension,
    ElementClassFilter,
    ElementId,
    Group,
    SubTransaction,
    Transaction,
    TransactionStatus,
)

from pyrevit import forms, revit, script

log = get_logger("WipeConstraints")
output = script.get_output()

FT_TO_MM = 304.8
VERPLAATSING_TOLERANTIE_MM = 0.1

CAT_WEAKDIMS = int(BuiltInCategory.OST_WeakDims)
CAT_CONSTRAINTS = int(BuiltInCategory.OST_Constraints)

HINT_GROUP = ("kan alleen binnen Edit Group worden verwijderd "
              "(constraint is group-member)")


# =============================================================================
# HELPERS
# =============================================================================
def eid_value(element_id):
    """Numerieke waarde van een ElementId.

    Revit 2024+ heeft .Value, oudere versies alleen .IntegerValue. In 2026 is
    .IntegerValue afgeschreven, dus .Value krijgt voorrang.
    """
    try:
        return element_id.Value
    except AttributeError:
        return element_id.IntegerValue


def expand_selection(doc, element_ids):
    """Klap groups uit naar hun members, nested groups inbegrepen.

    Retourneert een dict van id-waarde naar Element.
    """
    resultaat = {}
    te_bekijken = list(element_ids)

    while te_bekijken:
        eid = te_bekijken.pop()
        waarde = eid_value(eid)
        if waarde in resultaat:
            continue

        element = doc.GetElement(eid)
        if element is None:
            continue

        resultaat[waarde] = element

        if isinstance(element, Group):
            for member_id in element.GetMemberIds():
                te_bekijken.append(member_id)

    return resultaat


def find_dimensions(doc, doelen):
    """Alle Dimension-elementen die van de doelen afhangen, geclassificeerd.

    Retourneert (auto_dims, constraints, user_dims).
    """
    dim_filter = ElementClassFilter(Dimension)
    gezien = set()
    auto_dims = []
    constraints = []
    user_dims = []

    for element in doelen.values():
        try:
            afhankelijk = element.GetDependentElements(dim_filter)
        except Exception:
            continue

        for dep_id in afhankelijk:
            waarde = eid_value(dep_id)
            if waarde in gezien:
                continue
            gezien.add(waarde)

            dim = doc.GetElement(dep_id)
            if dim is None:
                continue

            categorie = eid_value(dim.Category.Id) if dim.Category else 0
            if categorie == CAT_WEAKDIMS:
                auto_dims.append(dim)
            elif categorie == CAT_CONSTRAINTS:
                constraints.append(dim)
            else:
                user_dims.append(dim)

    log.info("Gevonden: {} auto-dims, {} constraints, {} user-maatvoering"
             .format(len(auto_dims), len(constraints), len(user_dims)))
    return auto_dims, constraints, user_dims


def group_impact(doc, elementen):
    """Welke group-types worden geraakt en hoeveel instanties hangen eraan.

    Een dim die zelf group-member is zit in de group-DEFINITIE; wissen raakt
    alle instanties van dat type, niet alleen de aangewezen instantie.
    """
    impact = {}
    vrij = 0

    for element in elementen:
        group_id = element.GroupId
        if group_id is None or group_id == ElementId.InvalidElementId:
            vrij += 1
            continue

        group = doc.GetElement(group_id)
        if group is None:
            vrij += 1
            continue

        try:
            group_type = group.GroupType
            naam = group_type.Name
            instanties = group_type.Groups.Size
        except Exception:
            naam = "onbekend group-type"
            instanties = 0

        if naam not in impact:
            impact[naam] = {'aantal': 0, 'instanties': instanties}
        impact[naam]['aantal'] += 1

    return impact, vrij


def snapshot_bboxes(doc, elementen):
    """Bounding boxes vastleggen om verschuiving te kunnen detecteren."""
    snapshot = {}
    for waarde, element in elementen.items():
        try:
            bbox = element.get_BoundingBox(None)
        except Exception:
            bbox = None
        if bbox is None:
            continue
        snapshot[waarde] = (
            bbox.Min.X, bbox.Min.Y, bbox.Min.Z,
            bbox.Max.X, bbox.Max.Y, bbox.Max.Z,
        )
    return snapshot


def grootste_verschuiving(doc, snapshot):
    """Grootste bbox-afwijking in mm. (0.0, None) als er niets bewoog."""
    grootste = 0.0
    schuldige = None

    for waarde, oud in snapshot.items():
        element = doc.GetElement(ElementId(waarde))
        if element is None:
            continue
        try:
            bbox = element.get_BoundingBox(None)
        except Exception:
            bbox = None
        if bbox is None:
            continue

        nieuw = (
            bbox.Min.X, bbox.Min.Y, bbox.Min.Z,
            bbox.Max.X, bbox.Max.Y, bbox.Max.Z,
        )
        for index in range(6):
            delta = abs(nieuw[index] - oud[index]) * FT_TO_MM
            if delta > grootste:
                grootste = delta
                schuldige = waarde

    return grootste, schuldige


def open_scope(doc, naam):
    """Transaction, of SubTransaction als er al een transactie open staat.

    In Edit Group-modus houdt Revit zelf een transactie open; Transaction
    .Start() gooit dan InvalidOperationException. Beide routes worden
    geprobeerd zodat het script niet afhangt van een aanname over de modus.
    """
    if doc.IsModifiable:
        scope = SubTransaction(doc)
        try:
            scope.Start()
            return scope, 'SubTransaction (group-/sketch-edit actief)'
        except Exception:
            pass

    scope = Transaction(doc, naam)
    try:
        scope.Start()
        return scope, 'Transaction'
    except Exception:
        scope = SubTransaction(doc)
        scope.Start()
        return scope, 'SubTransaction (fallback)'


# =============================================================================
# MAIN
# =============================================================================
def main():
    doc = revit.doc
    uidoc = revit.uidoc

    if not doc:
        forms.alert("Open eerst een Revit project.", title="Constraints Wissen")
        return

    log.log_revit_info()
    log.section("Main")

    selectie_ids = list(uidoc.Selection.GetElementIds())
    if not selectie_ids:
        forms.alert(
            "Selecteer eerst een of meer elementen.\n\n"
            "Een group mag je als geheel selecteren - die wordt uitgeklapt "
            "naar zijn members.",
            title="Constraints Wissen"
        )
        return

    doelen = expand_selection(doc, selectie_ids)
    log.info("Selectie: {} element(en) -> {} na uitklappen van groups"
             .format(len(selectie_ids), len(doelen)))

    auto_dims, constraints, user_dims = find_dimensions(doc, doelen)

    if not auto_dims and not constraints:
        melding = ("Geen constraints of automatische sketch-dims gevonden op "
                   "deze selectie.\n\nDoorzocht: {} element(en) na uitklappen "
                   "van groups.".format(len(doelen)))
        if user_dims:
            melding += ("\n\nWel gevonden: {} zichtbare maatlijn(en). Die "
                        "raakt deze tool bewust niet aan."
                        .format(len(user_dims)))
        forms.alert(melding, title="Constraints Wissen")
        log.info("Niets te wissen")
        return

    doelwit = auto_dims + constraints
    impact, vrij = group_impact(doc, doelwit)

    # --- bevestiging ---------------------------------------------------------
    regels = [
        "Gevonden op {} element(en):".format(len(doelen)),
        "",
        "  Automatische sketch-dims : {}".format(len(auto_dims)),
        "  Constraints              : {}".format(len(constraints)),
        "  Zichtbare maatvoering    : {}  (wordt NIET gewist)"
        .format(len(user_dims)),
        "",
        "Vrij (niet in een group): {}".format(vrij),
    ]

    if impact:
        regels.append("")
        regels.append("LET OP - deze zitten in een group-definitie. Wissen "
                      "raakt ALLE instanties van dat type:")
        for naam in sorted(impact):
            regels.append("  - {}: {} stuk(s), {} instantie(s)"
                          .format(naam,
                                  impact[naam]['aantal'],
                                  impact[naam]['instanties']))
        if not doc.IsModifiable:
            regels.append("")
            regels.append("Je staat NIET in Edit Group. Revit weigert dan het "
                          "verwijderen van group-members; die worden "
                          "overgeslagen en gerapporteerd.")

    regels.append("")
    regels.append("Doorgaan?")

    if not forms.alert("\n".join(regels), title="Constraints Wissen",
                       yes=True, no=True):
        log.info("Afgebroken door gebruiker")
        return

    # --- wissen --------------------------------------------------------------
    snapshot = snapshot_bboxes(doc, doelen)
    log.info("Bbox-snapshot van {} element(en)".format(len(snapshot)))

    scope, modus = open_scope(doc, "Constraints wissen")
    log.info("Transactiemodus: {}".format(modus))

    verwijderd = []
    mislukt = []

    try:
        for element in doelwit:
            element_id = eid_value(element.Id)
            type_naam = element.GetType().Name

            # Al mee-verwijderd met een eerdere dim? Dan is er niets te doen.
            if doc.GetElement(element.Id) is None:
                verwijderd.append((element_id, type_naam))
                continue

            try:
                # Een pinned element weigert Revit te verwijderen. Op
                # group-members is Pinned read-only ("Element cannot be
                # pinned or unpinned") - dan is de delete sowieso geblokkeerd
                # en vangt het except-blok hieronder dat af.
                if element.Pinned:
                    try:
                        element.Pinned = False
                    except Exception:
                        pass
                doc.Delete(element.Id)
                verwijderd.append((element_id, type_naam))
            except Exception as fout:
                reden = str(fout)
                if 'cannot be deleted' in reden and element.GroupId \
                        and element.GroupId != ElementId.InvalidElementId:
                    reden = HINT_GROUP
                mislukt.append((element_id, type_naam, reden))
                log.warning("{} niet verwijderd: {}".format(element_id, fout))

        doc.Regenerate()
        afwijking, schuldige = grootste_verschuiving(doc, snapshot)

        if afwijking > VERPLAATSING_TOLERANTIE_MM:
            scope.RollBack()
            log.error("Teruggerold: element {} verschoof {:.3f} mm"
                      .format(schuldige, afwijking))
            forms.alert(
                "Teruggerold - er verschoof geometrie.\n\n"
                "Element {} bewoog {:.3f} mm (tolerantie {:.1f} mm).\n"
                "Er is niets gewijzigd."
                .format(schuldige, afwijking, VERPLAATSING_TOLERANTIE_MM),
                title="Constraints Wissen"
            )
            return

        scope.Commit()
        log.info("Gecommit: {} verwijderd, {} mislukt, max verschuiving "
                 "{:.3f} mm".format(len(verwijderd), len(mislukt), afwijking))

    except Exception as fout:
        try:
            if scope.GetStatus() == TransactionStatus.Started:
                scope.RollBack()
        except Exception:
            pass
        log.exception("Onverwachte fout")
        forms.alert("Onverwachte fout, niets gewijzigd:\n\n{}".format(fout),
                    title="Constraints Wissen")
        return

    # --- rapport -------------------------------------------------------------
    output.print_md("## Constraints wissen")
    output.print_md("Modus: **{}** | verwijderd: **{}** | mislukt: **{}** | "
                    "max verschuiving: **{:.3f} mm**"
                    .format(modus, len(verwijderd), len(mislukt), afwijking))

    if verwijderd:
        output.print_table(
            table_data=[[str(eid), naam] for eid, naam in verwijderd],
            title="Verwijderd",
            columns=["Id", "Type"]
        )

    if mislukt:
        output.print_table(
            table_data=[[str(eid), naam, reden]
                        for eid, naam, reden in mislukt],
            title="Niet verwijderd",
            columns=["Id", "Type", "Reden"]
        )
        if not doc.IsModifiable:
            output.print_md(
                "> Alles wat op `{}` staat: open de group met **Edit Group** "
                "en draai de tool opnieuw.".format(HINT_GROUP)
            )

    if user_dims:
        output.print_md(
            "> {} zichtbare maatlijn(en) hangen ook aan deze selectie. Die "
            "zijn bewust niet aangeraakt - wissen betekent annotatieverlies."
            .format(len(user_dims))
        )


try:
    main()
except Exception as onverwacht:
    log.exception("Fatale fout")
    forms.alert("Fout in WipeConstraints:\n\n{}".format(onverwacht),
                title="Constraints Wissen")
