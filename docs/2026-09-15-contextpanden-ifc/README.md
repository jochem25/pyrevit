# Contextpanden naar IFC — proef 15 september 2026

Doel: contextgebouwen met **echte** ramen en deuren, zonder gevelfoto's en zonder Revit.
Uitkomst: het werkt, maar wat je krijgt is een *plausibele reconstructie binnen een
gemeten envelop* — geen waarneming. Zie "Herkomst" hieronder; dat is het belangrijkste
deel van dit document.

## Draaien

```
python -m venv venv && venv/Scripts/pip install ifcopenshell shapely numpy openpyxl pillow
python prep.py        # BAG + 3DBAG ophalen, panden classificeren  -> blok.json
python naar_ifc.py    # IFC4 met wanden, ramen, deuren             -> contextpanden.ifc
python render.py      # visuele controle zonder viewer             -> *.png
python herkomst.py    # telt waar de invoer vandaan kwam
```

CPython 3.11, **niet** IronPython — dit draait bewust buiten Revit. `prep.py` bevat de
bbox (nu een blok bij Zeekant, Den Haag).

## Resultaat van de proef

| | |
|---|---|
| Panden | 65 (alle met 3DBAG-hoogte) |
| Wanden | 789, waarvan 137 blinde bouwmuren |
| Ramen / deuren | 1979 / 49 |
| Gevel / glas | 19.388 m² / 3.882 m² = 20,0% (RVO-doel 27,0%) |
| Bestand | 4,9 MB IFC4, 86.439 entiteiten, opent in 0,2 s |
| Generatietijd | 17 s |

De openingen snijden echt door de wanden (`IfcOpeningElement` + `IfcRelVoidsElement`),
de ramen vullen ze (`IfcRelFillsElement`). Zie `blok_detail.png` voor de dagkanten.

## Herkomst — wat is gemeten, wat is verzonnen

| Eigenschap | Bron | Status |
|---|---|---|
| Pandcontour x,y | BAG `bag:pand` | **Gemeten** (kadastraal) |
| Maaiveldhoogte | 3DBAG `b3_h_maaiveld` ← AHN | **Gemeten**, 65/65 |
| Gebouwhoogte | 3DBAG `b3_h_50p` ← AHN | **Gemeten**, 65/65 |
| Bouwjaar | BAG | **Gemeten**, 65/65 |
| Welke gevel blind is | raakt een buurcontour | **Afgeleid**, geometrisch hard |
| Aantal bouwlagen | 3DBAG `b3_bouwlagen` | **Geschat** (52/65) · door ons gegokt `h/3` (13/65) |
| Woningtype | buurpanden + `aantal_verblijfsobjecten` | **Afgeleid** |
| Glaspercentage | RVO Voorbeeldwoningen 2022, mediaan type × periode | **Statistisch**, niet dit pand |
| Aantal ramen | doeloppervlak ÷ (b × h) | **Verzonnen** |
| Raampositie | gelijkmatig, marge 0,3 m | **Verzonnen** |
| Borstwering 0,90 m | aanname | **Verzonnen** |
| Raamhoogte | `min(1,60; laaghoogte − 1,30)` | **Verzonnen** |
| Deurpositie | eerste raam van de langste open gevel | **Verzonnen** |

Alleen de *hoeveelheid* glas heeft een onderbouwing. Waar het zit, hoeveel het er zijn
en hoe groot: dat is de regel in `naar_ifc.py`, en die weet niets van het gebouw.

**Bruikbaar voor** bezonning, schaduw, silhouet, beeldkwaliteit van context.
**Niet bruikbaar voor** alles waar iemand een raam op natelt.

## Bronnen

- **BAG WFS** `service.pdok.nl/lv/bag/wfs/v2_0`, laag `bag:pand` — contour,
  `bouwjaar`, `gebruiksdoel`, `aantal_verblijfsobjecten`. Kapt af op 1000 per
  request, pagineert met `startIndex`.
- **3DBAG WFS** `data.3dbag.nl/api/BAG3D/wfs`, laag `BAG3D:lod22` — koppelt op
  `identificatie` (3DBAG prefixt met `NL.IMBAG.Pand.`). Gemeten dekking op een
  bbox van 500×500 m: 567 van 711 BAG-panden (80%).
- **RVO Voorbeeldwoningen 2022** —
  `rvo.nl/sites/default/files/2023-01/data-voorbeeldwoningen-2022.xlsx`.
  Per tabblad `(med) <type> <periode>`: rij 33 dichte gevel, rij 43 ramen,
  rij 52 deuren, kolom G = oppervlakte in m². 51 woningtypes × 7 bouwperioden.
  De uitgelezen waarden staan in `rvo_kentallen.json`.

## Bekende gebreken

1. **Hoeken lopen door elkaar.** Elk gevelsegment is een losse balk van hoekpunt tot
   hoekpunt; op elke hoek overlappen er twee. Oplossing: per pand één wandsolide van
   een ringprofiel (buitencontour min naar binnen verschoven contour). Lost ook
   gebrek 2 grotendeels op en is minder code dan wat er nu staat.
2. **Elk segment begint zijn eigen vensterritme**, dus bij een hoek kunnen twee ramen
   dicht op elkaar eindigen.
3. **20% glas waar 27% het doel was.** Ramen die niet passen worden overgeslagen in
   plaats van smaller gemaakt — systematische onderschatting.
4. **Geen daken.** Wandtop ligt op `b3_h_50p`, plat afgetopt. 91% van de proefbbox is
   `b3_dak_type = slanted`.
5. **Vlakke gevels** — geen plint, daklijst of nis. Dit maakt het beeld meer "karton"
   dan de ramen dat doen.
6. **KENTAL dekt 9 van de 51 RVO-types.** 23 van de 65 panden vielen terug op het
   percentage van een ander tijdvak; een flat uit 2002 kreeg het raamaandeel van een
   vooroorlogs portiek. Zie de uitvoer van `herkomst.py`.
7. **Geen georeferentie.** Het model staat om zijn eigen zwaartepunt. RD-oorsprong van
   deze proef: **78713,66 / 458239,81**. `IfcMapConversion` ontbreekt nog.
