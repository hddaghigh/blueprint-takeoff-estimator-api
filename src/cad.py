"""DXF (and converted DWG) takeoff: quantities from layers, blocks and closed shapes.

CAD is the most reliable source: layer names and named blocks carry meaning a
PDF drawing has lost. Geometry is converted to feet from the drawing's own
units ($INSUNITS); unitless drawings are rejected unless the caller says which
units they use.
"""

import fnmatch
import json
import math
import os
import shlex
import subprocess
import tempfile
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional

import ezdxf
from ezdxf import recover, units as dxf_units
from ezdxf.math import area as shoelace_area
from ezdxf.path import from_hatch, make_path
from fastapi import HTTPException

from src.config import LAYER_RULES_PATH
from src.extraction import extract_matches
from src.findings import Finding, build_items
from src.geometry.semantics import POINTS_PER_FOOT, paired_wall_length
from src.schemas import TakeoffResponse

UNIT_NAMES = {
    "in": dxf_units.IN,
    "ft": dxf_units.FT,
    "mm": dxf_units.MM,
    "cm": dxf_units.CM,
    "m": dxf_units.M,
}
FLATTEN_DISTANCE = 0.01  # drawing units; curve segmentation tolerance
DOOR_SWING_SWEEP_DEG = (80.0, 100.0)


@lru_cache(maxsize=1)
def layer_rules() -> dict:
    with open(LAYER_RULES_PATH, "r", encoding="utf-8") as fh:
        return {k: v for k, v in json.load(fh).items() if not k.startswith("_")}


def _matches(
    name: str, patterns: List[str], excludes: Optional[List[str]] = None
) -> bool:
    upper = name.upper()
    if any(fnmatch.fnmatchcase(upper, p.upper()) for p in excludes or []):
        return False
    return any(fnmatch.fnmatchcase(upper, p.upper()) for p in patterns)


def _convert_dwg(raw: bytes) -> bytes:
    """DWG -> DXF through an operator-configured converter, else a clear 422.

    Set DWG_CONVERTER_CMD to a command template using {input} and {output},
    e.g. `dwg2dxf -y -o {output} {input}`. It is run without a shell.
    """
    template = os.environ.get("DWG_CONVERTER_CMD")
    if not template:
        raise HTTPException(
            status_code=422,
            detail=(
                "DWG files are not read directly. Export to DXF from your CAD tool, or "
                "set DWG_CONVERTER_CMD (e.g. 'dwg2dxf -y -o {output} {input}') on the server."
            ),
        )
    with tempfile.TemporaryDirectory() as tmp:
        src, dst = Path(tmp) / "in.dwg", Path(tmp) / "out.dxf"
        src.write_bytes(raw)
        # posix=False keeps Windows backslashes intact but also keeps the quote
        # characters, so strip one surrounding pair from each token.
        tokens = [
            t[1:-1] if len(t) >= 2 and t[0] == t[-1] and t[0] in "\"'" else t
            for t in shlex.split(template, posix=os.name != "nt")
        ]
        args = [
            t.replace("{input}", str(src)).replace("{output}", str(dst)) for t in tokens
        ]
        try:
            subprocess.run(args, check=True, capture_output=True, timeout=120)
        except (OSError, subprocess.SubprocessError) as exc:
            raise HTTPException(status_code=422, detail=f"DWG conversion failed: {exc}")
        if not dst.exists():
            raise HTTPException(status_code=422, detail="DWG converter produced no DXF")
        return dst.read_bytes()


def _read_document(raw: bytes):
    if raw[:2] == b"AC" and raw[:6].startswith(b"AC10"):
        raw = _convert_dwg(raw)
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "drawing.dxf"
        path.write_bytes(raw)
        try:
            return ezdxf.readfile(str(path))
        except ezdxf.DXFStructureError:
            try:
                doc, _ = recover.readfile(str(path))
                return doc
            except Exception as exc:
                raise HTTPException(
                    status_code=422, detail=f"Not a readable DXF file: {exc}"
                )
        except (ezdxf.DXFError, OSError, UnicodeDecodeError) as exc:
            raise HTTPException(
                status_code=422, detail=f"Not a readable DXF file: {exc}"
            )


def _feet_per_unit(doc, override: Optional[str]) -> float:
    if override:
        code = UNIT_NAMES.get(override.lower())
        if code is None:
            raise HTTPException(
                status_code=422,
                detail=f"Unknown units '{override}'; use one of {sorted(UNIT_NAMES)}",
            )
    else:
        code = doc.units
        if code == 0:
            raise HTTPException(
                status_code=422,
                detail="The drawing has no units ($INSUNITS). Pass units=in|ft|mm|cm|m.",
            )
    try:
        return dxf_units.conversion_factor(code, dxf_units.FT)
    except Exception:
        raise HTTPException(
            status_code=422, detail=f"Unsupported drawing units code {code}"
        )


def _vertices(entity) -> List[tuple]:
    return [(v.x, v.y) for v in make_path(entity).flattening(FLATTEN_DISTANCE)]


def _length(vertices: List[tuple], closed: bool = False) -> float:
    total = sum(math.dist(a, b) for a, b in zip(vertices, vertices[1:]))
    if closed and vertices and vertices[0] != vertices[-1]:
        total += math.dist(vertices[-1], vertices[0])
    return total


def process_cad(
    raw: bytes,
    source: str,
    units: Optional[str] = None,
    wall_representation: str = "centerline",
) -> TakeoffResponse:
    if wall_representation not in ("centerline", "double_line"):
        raise HTTPException(
            status_code=422,
            detail="wall_representation must be 'centerline' or 'double_line'",
        )
    doc = _read_document(raw)
    ft = _feet_per_unit(doc, units)
    rules = layer_rules()
    msp = doc.modelspace()
    warnings: List[str] = []
    counts = {"wall_entities": 0, "door_blocks": 0, "door_arcs": 0, "floor_shapes": 0}

    # ---- walls
    wall_segments, wall_length_units = [], 0.0
    # ---- doors
    door_blocks = door_arcs = 0
    # ---- flooring
    floor_area_units = 0.0
    # ---- text notes
    notes: List[str] = []
    layers_seen = set()

    wr, dr, fr = rules["walls"], rules["doors"], rules["flooring"]
    for e in msp:
        kind, layer = e.dxftype(), e.dxf.layer
        layers_seen.add(layer)
        try:
            if kind in ("TEXT", "MTEXT"):
                notes.append(e.dxf.text if kind == "TEXT" else e.plain_text())
            elif kind == "INSERT":
                if _matches(layer, dr["layers"], dr.get("exclude_layers")) or _matches(
                    e.dxf.name, dr["blocks"]
                ):
                    door_blocks += 1
            elif _matches(layer, wr["layers"], wr.get("exclude_layers")):
                if kind == "LINE":
                    a, b = e.dxf.start, e.dxf.end
                    wall_segments.append((a.x, a.y, b.x, b.y))
                    wall_length_units += math.dist((a.x, a.y), (b.x, b.y))
                    counts["wall_entities"] += 1
                elif kind in ("LWPOLYLINE", "POLYLINE", "ARC"):
                    v = _vertices(e)
                    if kind == "LWPOLYLINE":
                        straight = all(p[4] == 0 for p in e.get_points("xyseb"))
                    elif kind == "POLYLINE":
                        straight = all(vtx.dxf.bulge == 0 for vtx in e.vertices)
                    else:  # ARC
                        straight = False
                    closed = bool(
                        getattr(e, "is_closed", False) or getattr(e, "closed", False)
                    )
                    wall_length_units += _length(v, closed)
                    if straight:
                        pts = v + ([v[0]] if closed and v[0] != v[-1] else [])
                        wall_segments += [
                            (a[0], a[1], b[0], b[1]) for a, b in zip(pts, pts[1:])
                        ]
                    counts["wall_entities"] += 1
            elif (
                _matches(layer, dr["layers"], dr.get("exclude_layers"))
                and kind == "ARC"
            ):
                sweep = (e.dxf.end_angle - e.dxf.start_angle) % 360
                if DOOR_SWING_SWEEP_DEG[0] <= sweep <= DOOR_SWING_SWEEP_DEG[1]:
                    door_arcs += 1
            elif _matches(layer, fr["layers"], fr.get("exclude_layers")):
                if kind in ("LWPOLYLINE", "POLYLINE") and (
                    getattr(e, "is_closed", False) or getattr(e, "closed", False)
                ):
                    floor_area_units += shoelace_area(_vertices(e))
                    counts["floor_shapes"] += 1
                elif kind == "HATCH":
                    for path, boundary in zip(from_hatch(e), e.paths):
                        a = shoelace_area(
                            [(v.x, v.y) for v in path.flattening(FLATTEN_DISTANCE)]
                        )
                        floor_area_units += a if boundary.path_type_flags & 1 else -a
                    counts["floor_shapes"] += 1
        except Exception:
            warnings.append(f"Skipped an unreadable {kind} entity on layer '{layer}'")

    counts["door_blocks"], counts["door_arcs"] = door_blocks, door_arcs

    findings: Dict[str, Finding] = {}
    if counts["wall_entities"]:
        if wall_representation == "double_line":
            # work in points-at-1:1 so the PDF wall-pairing logic can be reused
            k = ft * POINTS_PER_FOOT
            scaled = [(a * k, b * k, c * k, d * k) for a, b, c, d in wall_segments]
            paired_pt, pairs = paired_wall_length(scaled, 1.0)
            if pairs:
                findings["walls"] = Finding(
                    "walls",
                    paired_pt / POINTS_PER_FOOT,
                    "cad",
                    "high",
                    f"{pairs} double-line wall pair(s) on wall layers",
                )
            else:
                warnings.append(
                    "wall_representation=double_line found no parallel wall pairs"
                )
        else:
            findings["walls"] = Finding(
                "walls",
                wall_length_units * ft,
                "cad",
                "high",
                f"{counts['wall_entities']} entities on wall layers (centerline length)",
            )
    if door_blocks:
        findings["doors"] = Finding(
            "doors", float(door_blocks), "cad", "high", f"{door_blocks} door block(s)"
        )
    elif door_arcs:
        findings["doors"] = Finding(
            "doors",
            float(door_arcs),
            "cad",
            "medium",
            f"{door_arcs} door-swing arc(s) on door layers",
        )
    if counts["floor_shapes"] and floor_area_units > 0:
        findings["flooring"] = Finding(
            "flooring",
            floor_area_units * ft * ft,
            "cad",
            "high",
            f"{counts['floor_shapes']} closed shape(s) / hatch(es) on floor layers",
        )

    for category, match in extract_matches("\n".join(notes)).items():
        note = Finding(
            category, match.quantity, "text", "high", "Drawing note: " + match.snippet
        )
        existing = findings.get(category)
        if existing is None:
            findings[category] = note
        elif abs(existing.quantity - note.quantity) > 0.01 * max(existing.quantity, 1):
            warnings.append(
                f"{category}: drawing note ({note.quantity:g}) differs from CAD geometry "
                f"({existing.quantity:g}); using the CAD geometry"
            )

    if not findings:
        warnings.append(
            "No entities matched the wall/door/flooring layer rules "
            f"(src/database/layer_rules.json). Layers present: {sorted(layers_seen)[:20]}"
        )
    review = [w for w in warnings if "differs" in w or "Skipped" in w]
    metadata = {
        "units": dxf_units.decode(doc.units) if not units else units,
        "feet_per_unit": round(ft, 6),
        "wall_representation": wall_representation,
        "entity_counts": counts,
        "layers_present": sorted(layers_seen)[:50],
        "text_notes": len(notes),
        "evidence": {c: f.evidence for c, f in findings.items()},
        "warnings": warnings,
        "needs_review": bool(review),
    }
    return TakeoffResponse(
        source=source, items=build_items(findings), metadata=metadata
    )
