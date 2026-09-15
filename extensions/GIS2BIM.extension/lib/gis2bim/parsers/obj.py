# -*- coding: utf-8 -*-
"""
Wavefront OBJ Parser
=====================

Simpele parser voor Wavefront OBJ bestanden.
Ondersteunt vertices, faces, en object/group namen.

Gebruik:
    from gis2bim.parsers.obj import OBJReader

    reader = OBJReader()
    meshes = reader.read("gebouwen.obj")
    # [{"name": "building_1", "vertices": [(x,y,z),...], "faces": [(0,1,2),...]}, ...]

    mesh = reader.read_as_single_mesh("gebouwen.obj")
    # {"name": "merged", "vertices": [(x,y,z),...], "faces": [(0,1,2),...]}
"""


class OBJError(Exception):
    """Fout bij het parsen van een OBJ bestand."""
    pass


class OBJReader(object):
    """Parser voor Wavefront OBJ bestanden.

    Ondersteunt:
    - v x y z: vertex posities
    - f v1 v2 v3 ...: face indices (1-based, negatieve indices)
    - f v1/vt1/vn1 ...: face indices met texture/normal (alleen vertex index gebruikt)
    - o/g: object/group namen
    - mtllib: verwijzing naar materiaalbibliotheek (.mtl bestand)
    - usemtl: materiaal toewijzing per face groep

    Optioneel (sanitize=True) worden corrupte vertices opgevangen; zie
    _saneer_vertices. Standaard UIT: een parser hoort brondata niet
    ongevraagd te herschrijven.
    """

    def __init__(self, sanitize=False, max_span=10000.0, max_z=1000.0,
                 log=None):
        """
        Args:
            sanitize: Corrupte vertices opsporen en herstellen/isoleren
            max_span: Max. afstand (in broneenheden) van de mediaan in X/Y
                      waarbinnen een vertex geldig heet
            max_z: Max. absolute Z waarbinnen een vertex geldig heet
            log: Optionele logfunctie
        """
        self.sanitize = sanitize
        self.max_span = max_span
        self.max_z = max_z
        self._log = log or (lambda msg: None)

    # ------------------------------------------------------------------
    # Sanitatie
    # ------------------------------------------------------------------
    @staticmethod
    def _mediaan(waarden):
        s = sorted(waarden)
        n = len(s)
        if n == 0:
            return 0.0
        if n % 2:
            return s[n // 2]
        return (s[n // 2 - 1] + s[n // 2]) / 2.0

    def _saneer_vertices(self, vertices):
        """Vang vertices op die ver buiten de rest van het bestand liggen.

        Geoflow (de generator achter 3DBAG) schrijft zelden een vertex weg
        met de drie waarden EEN POSITIE GEDRAAID. Voorbeeld uit tile
        10/260/604 LoD22, regel 45954:

            v 3.66300 78877.88281 458360.87500     <- fout
            v 78877.88281 458360.87500 3.66300     <- bedoeld

        Onbehandeld levert dat een gebouw met een spike van honderden
        kilometers op. Eerst wordt de draaiing geprobeerd; alleen als het
        resultaat binnen de wolk valt wordt hij geaccepteerd. Lukt dat
        niet, dan blijft de vertex staan (indices mogen NIET verschuiven)
        en worden de faces die hem gebruiken overgeslagen.

        Returns:
            (vertices, verdachte_indices) - indices zijn 0-based
        """
        if not vertices:
            return vertices, set()

        mid_x = self._mediaan([v[0] for v in vertices])
        mid_y = self._mediaan([v[1] for v in vertices])

        def binnen(p):
            return (abs(p[0] - mid_x) <= self.max_span
                    and abs(p[1] - mid_y) <= self.max_span
                    and abs(p[2]) <= self.max_z)

        hersteld = 0
        onbruikbaar = set()

        for i, v in enumerate(vertices):
            if binnen(v):
                continue

            # Beide draairichtingen proberen
            for kandidaat in ((v[1], v[2], v[0]), (v[2], v[0], v[1])):
                if binnen(kandidaat):
                    vertices[i] = kandidaat
                    hersteld += 1
                    self._log(
                        "OBJ: vertex {0} hersteld {1} -> {2}".format(
                            i + 1, v, kandidaat))
                    break
            else:
                onbruikbaar.add(i)
                self._log(
                    "OBJ: vertex {0} onherstelbaar {1} - faces worden "
                    "overgeslagen".format(i + 1, v))

        if hersteld or onbruikbaar:
            self._log("OBJ sanitatie: {0} hersteld, {1} onbruikbaar "
                      "(van {2} vertices)".format(
                          hersteld, len(onbruikbaar), len(vertices)))

        return vertices, onbruikbaar

    @staticmethod
    def _zonder_kapotte_faces(faces, onbruikbaar):
        """Laat faces weg die naar een onbruikbare vertex verwijzen."""
        if not onbruikbaar:
            return faces
        return [f for f in faces
                if not any(idx in onbruikbaar for idx in f)]

    def read(self, filepath):
        """Parse OBJ bestand naar lijst van mesh dicts per object/group.

        Args:
            filepath: Pad naar .obj bestand

        Returns:
            Lijst van mesh dicts:
            [{"name": str, "vertices": [(x,y,z),...], "faces": [(i0,i1,i2,...),...]
              "material": str of None, "mtllib": str of None}, ...]

            Vertices zijn float tuples in de originele coordinaten.
            Face indices zijn 0-based.

        Raises:
            OBJError: Bij lees- of parse fouten
        """
        try:
            with open(filepath, "r") as f:
                lines = f.readlines()
        except IOError as e:
            raise OBJError("Kan OBJ bestand niet lezen: {0}".format(e))

        # Globale vertex lijst (faces refereren naar globale indices)
        all_vertices = []
        meshes = []
        current_name = "default"
        current_faces = []
        current_material = None
        mtllib = None

        for line_num, raw_line in enumerate(lines, 1):
            line = raw_line.strip()

            # Skip lege regels en comments
            if not line or line[0] == "#":
                continue

            parts = line.split()
            keyword = parts[0]

            if keyword == "v" and len(parts) >= 4:
                # Vertex: v x y z [w]
                try:
                    x = float(parts[1])
                    y = float(parts[2])
                    z = float(parts[3])
                    all_vertices.append((x, y, z))
                except (ValueError, IndexError):
                    pass  # Skip ongeldige vertices

            elif keyword == "f" and len(parts) >= 4:
                # Face: f v1 v2 v3 ... of f v1/vt1/vn1 ...
                face_indices = []
                valid = True
                for token in parts[1:]:
                    idx = self._parse_face_index(token, len(all_vertices))
                    if idx is None:
                        valid = False
                        break
                    face_indices.append(idx)

                if valid and len(face_indices) >= 3:
                    current_faces.append(tuple(face_indices))

            elif keyword in ("o", "g"):
                # Object/Group: sla huidige mesh op en start nieuwe
                if current_faces:
                    meshes.append({
                        "name": current_name,
                        "faces": current_faces,
                        "material": current_material,
                    })
                    current_faces = []

                if len(parts) > 1:
                    current_name = " ".join(parts[1:])
                else:
                    current_name = "unnamed"

            elif keyword == "mtllib" and len(parts) >= 2:
                mtllib = " ".join(parts[1:])

            elif keyword == "usemtl" and len(parts) >= 2:
                mat_name = " ".join(parts[1:])
                # Als er al faces zijn met een ander materiaal, splits
                if current_faces and current_material != mat_name:
                    meshes.append({
                        "name": current_name,
                        "faces": current_faces,
                        "material": current_material,
                    })
                    current_faces = []
                current_material = mat_name

        # Laatste mesh opslaan
        if current_faces:
            meshes.append({
                "name": current_name,
                "faces": current_faces,
                "material": current_material,
            })

        if not meshes:
            raise OBJError("Geen geldige mesh data gevonden in OBJ bestand")

        if not all_vertices:
            raise OBJError("Geen vertices gevonden in OBJ bestand")

        onbruikbaar = set()
        if self.sanitize:
            all_vertices, onbruikbaar = self._saneer_vertices(all_vertices)

        # Voeg vertices en mtllib toe aan elke mesh
        # Alle meshes delen dezelfde globale vertex lijst
        for mesh in meshes:
            mesh["vertices"] = list(all_vertices)
            mesh["mtllib"] = mtllib
            mesh["faces"] = self._zonder_kapotte_faces(
                mesh["faces"], onbruikbaar)

        return meshes

    def read_as_single_mesh(self, filepath):
        """Parse OBJ bestand naar een enkele samengevoegde mesh.

        Alle object/group grenzen worden genegeerd; alle faces
        worden samengevoegd tot een enkel mesh object.

        Args:
            filepath: Pad naar .obj bestand

        Returns:
            Mesh dict:
            {"name": "merged", "vertices": [(x,y,z),...], "faces": [(i0,i1,i2,...),...]

        Raises:
            OBJError: Bij lees- of parse fouten
        """
        try:
            with open(filepath, "r") as f:
                lines = f.readlines()
        except IOError as e:
            raise OBJError("Kan OBJ bestand niet lezen: {0}".format(e))

        vertices = []
        faces = []
        mtllib = None

        for line in lines:
            line = line.strip()

            if not line or line[0] == "#":
                continue

            parts = line.split()
            keyword = parts[0]

            if keyword == "v" and len(parts) >= 4:
                try:
                    x = float(parts[1])
                    y = float(parts[2])
                    z = float(parts[3])
                    vertices.append((x, y, z))
                except (ValueError, IndexError):
                    pass

            elif keyword == "mtllib" and len(parts) >= 2:
                mtllib = " ".join(parts[1:])

            elif keyword == "f" and len(parts) >= 4:
                face_indices = []
                valid = True
                for token in parts[1:]:
                    idx = self._parse_face_index(token, len(vertices))
                    if idx is None:
                        valid = False
                        break
                    face_indices.append(idx)

                if valid and len(face_indices) >= 3:
                    faces.append(tuple(face_indices))

        if not vertices:
            raise OBJError("Geen vertices gevonden in OBJ bestand")

        if not faces:
            raise OBJError("Geen faces gevonden in OBJ bestand")

        if self.sanitize:
            vertices, onbruikbaar = self._saneer_vertices(vertices)
            faces = self._zonder_kapotte_faces(faces, onbruikbaar)

        return {
            "name": "merged",
            "vertices": vertices,
            "faces": faces,
            "material": None,
            "mtllib": mtllib,
        }

    def _parse_face_index(self, token, vertex_count):
        """Parse een face index token.

        Ondersteunt formaten:
        - "v"           -> vertex index
        - "v/vt"        -> vertex/texture index
        - "v/vt/vn"     -> vertex/texture/normal index
        - "v//vn"       -> vertex//normal index

        Indices zijn 1-based in OBJ, worden geconverteerd naar 0-based.
        Negatieve indices worden relatief aan huidige vertex count geinterpreteerd.

        Returns:
            0-based vertex index, of None bij ongeldige input
        """
        try:
            # Split op '/' en pak eerste getal (vertex index)
            idx_str = token.split("/")[0]
            idx = int(idx_str)

            if idx > 0:
                return idx - 1  # 1-based naar 0-based
            elif idx < 0:
                return vertex_count + idx  # Negatieve index
            else:
                return None  # 0 is ongeldig in OBJ
        except (ValueError, IndexError):
            return None
