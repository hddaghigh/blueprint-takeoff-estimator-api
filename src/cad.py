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
import shutil
import subprocess
import sys
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


@lru_cache(maxsize=8)
def _load_rules(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            rules = {k: v for k, v in json.load(fh).items() if not k.startswith("_")}
    except (OSError, ValueError) as exc:
        raise HTTPException(
            status_code=500, detail=f"Cannot read layer rules file '{path}': {exc}"
        )
    for category, needs in (("walls", ()), ("doors", ("blocks",)), ("flooring", ())):
        rule = rules.get(category)
        if not isinstance(rule, dict) or not isinstance(rule.get("layers"), list):
            raise HTTPException(
                status_code=500,
                detail=f"Layer rules file '{path}': '{category}' needs a 'layers' list",
            )
        for key in needs:
            if not isinstance(rule.get(key), list):
                raise HTTPException(
                    status_code=500,
                    detail=f"Layer rules file '{path}': '{category}' needs a '{key}' list",
                )
    return rules


def layer_rules() -> dict:
    """Layer/block name rules. Set LAYER_RULES_FILE to use your office CAD standard."""
    return _load_rules(os.environ.get("LAYER_RULES_FILE") or str(LAYER_RULES_PATH))


def _matches(
    name: str, patterns: List[str], excludes: Optional[List[str]] = None
) -> bool:
    upper = name.upper()
    if any(fnmatch.fnmatchcase(upper, p.upper()) for p in excludes or []):
        return False
    return any(fnmatch.fnmatchcase(upper, p.upper()) for p in patterns)


CONVERT_TIMEOUT_S = 120
DWG_INSTALL_HELP = (
    "Export to DXF from your CAD tool (most reliable), or install a converter on "
    "the server: the ODA File Converter (free, proprietary; used through ezdxf), "
    "or LibreDWG's `dwg2dxf` (GPLv3; run as a separate program). You can also set "
    "DWG_CONVERTER_CMD, e.g. 'dwg2dxf -y -o {output} {input}'."
)


def _run_template(template: str, src: Path, dst: Path) -> None:
    # posix=False keeps Windows backslashes intact but also keeps the quote
    # characters, so strip one surrounding pair from each token.
    tokens = [
        t[1:-1] if len(t) >= 2 and t[0] == t[-1] and t[0] in "\"'" else t
        for t in shlex.split(template, posix=os.name != "nt")
    ]
    args = [
        t.replace("{input}", str(src)).replace("{output}", str(dst)) for t in tokens
    ]
    subprocess.run(args, check=True, capture_output=True, timeout=CONVERT_TIMEOUT_S)


def _run_oda(src: Path, dst: Path) -> None:
    """ODA File Converter through ezdxf's odafc addon, in a child process so a
    hang on a hostile file is cut off by a hard timeout."""
    code = (
        "import sys; from ezdxf.addons import odafc; "
        "odafc.convert(sys.argv[1], sys.argv[2], version='R2018', audit=False, replace=True)"
    )
    subprocess.run(
        [sys.executable, "-c", code, str(src), str(dst)],
        check=True,
        capture_output=True,
        timeout=CONVERT_TIMEOUT_S,
    )


def _run_libredwg(src: Path, dst: Path) -> None:
    """LibreDWG's dwg2dxf as a separate program (never imported: GPLv3)."""
    exe = shutil.which("dwg2dxf")
    subprocess.run(
        [exe, "-y", "-o", str(dst), str(src)],
        check=True,
        capture_output=True,
        timeout=CONVERT_TIMEOUT_S,
    )


def _oda_installed() -> bool:
    try:
        from ezdxf.addons import odafc

        return bool(odafc.is_installed())
    except Exception:
        return False


def dwg_converters() -> List[tuple]:
    """Available DWG->DXF converters as (name, runner), best first.

    An operator-set DWG_CONVERTER_CMD wins; then the ODA File Converter (best
    support for current DWG versions); then LibreDWG's dwg2dxf.
    """
    found = []
    template = os.environ.get("DWG_CONVERTER_CMD")
    if template:
        found.append(
            ("DWG_CONVERTER_CMD", lambda s, d, t=template: _run_template(t, s, d))
        )
    if _oda_installed():
        found.append(("odafc", _run_oda))
    if shutil.which("dwg2dxf"):
        found.append(("libredwg", _run_libredwg))
    return found


def _convert_dwg(raw: bytes) -> bytes:
    """DWG -> DXF using the first converter that works, else a clear 422."""
    converters = dwg_converters()
    if not converters:
        raise HTTPException(
            status_code=422,
            detail="DWG files are not read directly. " + DWG_INSTALL_HELP,
        )
    errors = []
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "in.dwg"
        src.write_bytes(raw)
        for index, (name, run) in enumerate(converters):
            dst = Path(tmp) / f"out{index}.dxf"
            try:
                run(src, dst)
            except (OSError, subprocess.SubprocessError) as exc:
                errors.append(f"{name}: {type(exc).__name__}")
                continue
            if dst.exists() and dst.stat().st_size > 0:
                return dst.read_bytes()
            errors.append(f"{name}: produced no DXF")
    raise HTTPException(
        status_code=422, detail="DWG conversion failed (" + "; ".join(errors) + ")"
    )


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
        except Exception as exc:  # incl. StopIteration/IndexError on truncated files
            raise HTTPException(
                status_code=422,
                detail=f"Not a readable DXF file: {type(exc).__name__}: {exc}",
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


MAX_BLOCK_DEPTH = 8
MAX_EXPANDED_INSERTS = 5000  # guards against pathological block nesting / huge arrays
TEXT_KINDS = ("TEXT", "MTEXT")
WALL_PATH_KINDS = ("LWPOLYLINE", "POLYLINE", "ARC", "SPLINE", "ELLIPSE")


class _WalkStats:
    def __init__(self):
        self.hidden = 0
        self.expanded = 0
        self.too_deep = 0
        self.cap_hit = False
        self.xrefs: set = set()
        self.unsupported: Dict[str, int] = {}
        self._layer_state: Dict[str, bool] = {}

    def layer_hidden(self, doc, name: str) -> bool:
        if name not in self._layer_state:
            try:
                layer = doc.layers.get(name)
                self._layer_state[name] = bool(layer.is_off() or layer.is_frozen())
            except Exception:  # unknown layer name: treat as visible
                self._layer_state[name] = False
        return self._layer_state[name]


def _is_door_insert(name: str, layer: str, rule: dict) -> bool:
    if _matches(layer, rule["layers"], rule.get("exclude_layers")):
        return True
    return not name.startswith("*") and _matches(name, rule["blocks"])


def _is_xref(doc, name: str) -> bool:
    try:
        return bool(doc.blocks.get(name).block.dxf.get("xref_path"))
    except Exception:
        return False


def _walk(entities, doc, stats, rule, depth=0, parent_layer=None, stack=()):
    """Yield (kind, entity, effective_layer, count) for every drawable entity.

    Nested blocks are expanded (with their scale/rotation applied) and block
    arrays are counted per cell. A door block is a single unit: it is yielded as
    "DOOR_BLOCK" with its cell count and not expanded. Entities on layer "0"
    inside a block take the layer of the INSERT, as CAD software does.
    """
    for e in entities:
        kind = e.dxftype()
        layer = e.dxf.layer
        if layer == "0" and parent_layer:
            layer = parent_layer
        if stats.layer_hidden(doc, layer) or e.dxf.get("invisible", 0):
            stats.hidden += 1
            continue
        if kind != "INSERT":
            yield kind, e, layer, 1
            continue
        name = e.dxf.name
        cells = max(1, e.dxf.get("column_count", 1)) * max(1, e.dxf.get("row_count", 1))
        if _is_door_insert(name, layer, rule):
            yield "DOOR_BLOCK", e, layer, cells
            continue
        if _is_xref(doc, name):
            stats.xrefs.add(name)
            continue
        if depth >= MAX_BLOCK_DEPTH or name in stack:
            stats.too_deep += 1
            continue
        if stats.expanded + cells > MAX_EXPANDED_INSERTS:
            stats.cap_hit = True
            continue
        stats.expanded += cells
        for cell in e.multi_insert():
            yield from _walk(
                cell.virtual_entities(),
                doc,
                stats,
                rule,
                depth + 1,
                layer,
                stack + (name,),
            )


def _paper_layouts(doc) -> List[dict]:
    """Paper-space layouts that hold viewports, with each viewport's scale."""
    out = []
    for name in doc.layout_names():
        if name == "Model":
            continue
        try:
            viewports = [
                vp
                for vp in doc.layout(name).viewports()
                if vp.dxf.get("status", 1) > 0 or vp.dxf.get("view_height", 0)
            ]
        except Exception:
            continue
        if viewports:
            scales = sorted(
                {
                    round(vp.dxf.height / vp.dxf.view_height, 4)
                    for vp in viewports
                    if vp.dxf.get("view_height", 0)
                }
            )
            out.append(
                {"name": name, "viewports": len(viewports), "paper_per_model": scales}
            )
    return out


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
    rules = layer_rules()  # fail fast on a bad rules file
    doc = _read_document(raw)
    ft = _feet_per_unit(doc, units)
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
    stats = _WalkStats()
    for kind, e, layer, cells in _walk(msp, doc, stats, dr):
        layers_seen.add(layer)
        try:
            if kind in TEXT_KINDS:
                notes.append(e.dxf.text if kind == "TEXT" else e.plain_text())
            elif kind == "DOOR_BLOCK":
                door_blocks += cells
            elif _matches(layer, wr["layers"], wr.get("exclude_layers")):
                if kind == "LINE":
                    a, b = e.dxf.start, e.dxf.end
                    wall_segments.append((a.x, a.y, b.x, b.y))
                    wall_length_units += math.dist((a.x, a.y), (b.x, b.y))
                    counts["wall_entities"] += 1
                elif kind == "MLINE":
                    stats.unsupported["MLINE"] = stats.unsupported.get("MLINE", 0) + 1
                elif kind in WALL_PATH_KINDS:
                    v = _vertices(e)
                    if kind == "LWPOLYLINE":
                        straight = all(p[4] == 0 for p in e.get_points("xyseb"))
                    elif kind == "POLYLINE":
                        straight = all(vtx.dxf.bulge == 0 for vtx in e.vertices)
                    else:  # ARC, SPLINE, ELLIPSE
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

    review: List[str] = []
    if stats.hidden:
        warnings.append(
            f"{stats.hidden} entit(ies) on frozen/off layers or marked invisible were ignored"
        )
    if stats.xrefs:
        warnings.append(
            f"External reference(s) {sorted(stats.xrefs)} are not loaded; geometry in them "
            "is not counted"
        )
        review.append("xrefs not loaded")
    if stats.too_deep:
        warnings.append(
            f"{stats.too_deep} block(s) nested deeper than {MAX_BLOCK_DEPTH} levels or "
            "referencing themselves were not expanded"
        )
        review.append("block nesting limit")
    if stats.cap_hit:
        warnings.append(
            f"More than {MAX_EXPANDED_INSERTS} block insertions; the rest were not expanded"
        )
        review.append("insert cap reached")
    for kind, n in stats.unsupported.items():
        warnings.append(f"{n} {kind} entit(ies) on wall layers are not measured")
        review.append(f"{kind} not measured")
    paper = _paper_layouts(doc)
    if paper:
        warnings.append(
            "Read model space only; paper-space layout(s) with viewports exist "
            f"({[p['name'] for p in paper]}), so printed scales were not applied"
        )

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
    review += [w for w in warnings if "differs" in w or "Skipped" in w]
    metadata = {
        "paper_layouts": paper,
        "hidden_entities_ignored": stats.hidden,
        "blocks_expanded": stats.expanded,
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
