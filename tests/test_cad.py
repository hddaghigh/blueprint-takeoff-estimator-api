"""DXF pipeline tests against drawings written with ezdxf to known dimensions."""

import sys

import ezdxf
import pytest
from ezdxf import units

URL = "/api/v1/takeoffs/drawing-cad"


def new_doc(unit=units.FT):
    doc = ezdxf.new("R2018")
    doc.units = unit
    for name in ("A-WALL", "A-DOOR", "A-FLOR", "A-ANNO", "A-DIM-WALL", "0-MISC"):
        doc.layers.add(name)
    return doc


def save(doc, tmp_path, name="plan.dxf"):
    path = tmp_path / name
    doc.saveas(path)
    return path


async def upload(client, path, **form):
    with open(path, "rb") as fh:
        return await client.post(
            URL, files={"file": (path.name, fh, "application/dxf")}, data=form
        )


def item(body, category):
    return next((i for i in body["items"] if i["category"] == category), None)


@pytest.mark.asyncio
async def test_walls_doors_flooring_from_layers_and_blocks(client, tmp_path):
    doc = new_doc()
    msp = doc.modelspace()
    for a, b in [
        ((0, 0), (10, 0)),
        ((10, 0), (10, 10)),
        ((10, 10), (0, 10)),
        ((0, 10), (0, 0)),
    ]:
        msp.add_line(a, b, dxfattribs={"layer": "A-WALL"})  # 40 ft of wall
    msp.add_line(
        (0, 0), (500, 0), dxfattribs={"layer": "A-DIM-WALL"}
    )  # dimension: excluded
    msp.add_line((0, 0), (999, 0), dxfattribs={"layer": "0-MISC"})  # unrelated layer
    blk = doc.blocks.new("DOOR-36")
    blk.add_line((0, 0), (3, 0))
    for x in (1, 4, 7):
        msp.add_blockref("DOOR-36", (x, 0), dxfattribs={"layer": "A-DOOR"})
    msp.add_lwpolyline(
        [(0, 0), (20, 0), (20, 15), (0, 15)], close=True, dxfattribs={"layer": "A-FLOR"}
    )

    body = (await upload(client, save(doc, tmp_path))).json()

    assert item(body, "walls")["quantity"] == pytest.approx(40.0)
    assert item(body, "doors")["quantity"] == 3.0
    assert item(body, "flooring")["quantity"] == pytest.approx(300.0)
    for it in body["items"]:
        assert (it["basis"], it["confidence"]) == ("cad", "high")
    assert body["metadata"]["units"] == "ft"


@pytest.mark.asyncio
async def test_metric_drawing_is_converted_to_feet(client, tmp_path):
    doc = new_doc(units.MM)
    msp = doc.modelspace()
    msp.add_line((0, 0), (3048, 0), dxfattribs={"layer": "A-WALL"})  # 10 ft
    msp.add_lwpolyline(
        [(0, 0), (3048, 0), (3048, 3048), (0, 3048)],
        close=True,
        dxfattribs={"layer": "A-FLOR"},
    )  # 100 sq ft

    body = (await upload(client, save(doc, tmp_path))).json()

    assert item(body, "walls")["quantity"] == pytest.approx(10.0, abs=0.01)
    assert item(body, "flooring")["quantity"] == pytest.approx(100.0, abs=0.01)


@pytest.mark.asyncio
async def test_unitless_drawing_requires_units(client, tmp_path):
    doc = new_doc(units.NONE if hasattr(units, "NONE") else 0)
    doc.modelspace().add_line((0, 0), (10, 0), dxfattribs={"layer": "A-WALL"})
    path = save(doc, tmp_path)

    refused = await upload(client, path)
    accepted = await upload(client, path, units="ft")

    assert refused.status_code == 422 and "units" in refused.json()["detail"]
    assert item(accepted.json(), "walls")["quantity"] == pytest.approx(10.0)


@pytest.mark.asyncio
async def test_double_line_walls_are_paired(client, tmp_path):
    doc = new_doc()
    msp = doc.modelspace()
    msp.add_line((0, 0), (20, 0), dxfattribs={"layer": "A-WALL"})
    msp.add_line((0, 0.5), (20, 0.5), dxfattribs={"layer": "A-WALL"})  # 6 in wall
    path = save(doc, tmp_path)

    centerline = (await upload(client, path)).json()
    paired = (await upload(client, path, wall_representation="double_line")).json()

    assert item(centerline, "walls")["quantity"] == pytest.approx(40.0)  # both lines
    assert item(paired, "walls")["quantity"] == pytest.approx(20.0, abs=0.01)


@pytest.mark.asyncio
async def test_curved_polyline_wall_uses_true_arc_length(client, tmp_path):
    doc = new_doc()
    # bulge 1 = semicircle over a 2 ft chord: length = pi * 1 ft
    doc.modelspace().add_lwpolyline(
        [(0, 0, 0, 0, 1.0), (2, 0, 0, 0, 0)],
        format="xyseb",
        dxfattribs={"layer": "A-WALL"},
    )

    body = (await upload(client, save(doc, tmp_path))).json()

    assert item(body, "walls")["quantity"] == pytest.approx(3.1416, abs=0.01)


@pytest.mark.asyncio
async def test_hatch_area_with_a_hole(client, tmp_path):
    doc = new_doc()
    hatch = doc.modelspace().add_hatch(dxfattribs={"layer": "A-FLOR"})
    hatch.paths.add_polyline_path(
        [(0, 0), (10, 0), (10, 10), (0, 10)], is_closed=True, flags=1
    )
    hatch.paths.add_polyline_path(
        [(4, 4), (6, 4), (6, 6), (4, 6)], is_closed=True, flags=16
    )

    body = (await upload(client, save(doc, tmp_path))).json()

    assert item(body, "flooring")["quantity"] == pytest.approx(96.0)  # 100 - 2x2 hole


@pytest.mark.asyncio
async def test_door_swing_arcs_when_there_are_no_door_blocks(client, tmp_path):
    doc = new_doc()
    msp = doc.modelspace()
    for x in (0, 5):
        msp.add_arc((x, 0), 3, 0, 90, dxfattribs={"layer": "A-DOOR"})
    msp.add_arc(
        (20, 0), 3, 0, 180, dxfattribs={"layer": "A-DOOR"}
    )  # not a quarter swing

    body = (await upload(client, save(doc, tmp_path))).json()

    doors = item(body, "doors")
    assert doors["quantity"] == 2.0
    assert doors["confidence"] == "medium"


@pytest.mark.asyncio
async def test_text_notes_fill_gaps_and_conflicts_are_flagged(client, tmp_path):
    doc = new_doc()
    msp = doc.modelspace()
    msp.add_text(
        "Provide 1,500 sq ft of vinyl tile flooring.", dxfattribs={"layer": "A-ANNO"}
    )
    msp.add_mtext("Provide 9 doors.", dxfattribs={"layer": "A-ANNO"})
    doc.blocks.new("DOOR-36")
    for x in (1, 4):
        msp.add_blockref("DOOR-36", (x, 0), dxfattribs={"layer": "A-DOOR"})

    body = (await upload(client, save(doc, tmp_path))).json()

    assert item(body, "flooring")["quantity"] == 1500.0
    assert item(body, "flooring")["basis"] == "text"
    assert item(body, "doors")["quantity"] == 2.0  # CAD blocks beat the note
    assert any("differs from CAD" in w for w in body["metadata"]["warnings"])
    assert body["metadata"]["needs_review"] is True


@pytest.mark.asyncio
async def test_no_matching_layers_lists_the_layers_present(client, tmp_path):
    doc = new_doc()
    doc.modelspace().add_line((0, 0), (10, 0), dxfattribs={"layer": "0-MISC"})

    body = (await upload(client, save(doc, tmp_path))).json()

    assert body["items"] == []
    assert any("0-MISC" in w for w in body["metadata"]["warnings"])


@pytest.mark.asyncio
async def test_unreadable_and_dwg_inputs_return_422(client, tmp_path):
    junk = tmp_path / "junk.dxf"
    junk.write_bytes(b"this is not a dxf file at all")
    dwg = tmp_path / "plan.dwg"
    dwg.write_bytes(b"AC1032" + b"\x00" * 200)

    bad = await upload(client, junk)
    unconverted = await upload(client, dwg)

    assert bad.status_code == 422
    assert unconverted.status_code == 422 and "DWG" in unconverted.json()["detail"]


@pytest.mark.asyncio
async def test_dwg_is_converted_with_a_configured_converter(
    client, tmp_path, monkeypatch
):
    doc = new_doc()
    doc.modelspace().add_line((0, 0), (10, 0), dxfattribs={"layer": "A-WALL"})
    dxf_path = save(doc, tmp_path, "source.dxf")
    converter = tmp_path / "fake_converter.py"
    converter.write_text(
        "import shutil, sys\nshutil.copy(sys.argv[2], sys.argv[1])\n"  # argv: script, out, dxf
    )
    dwg = tmp_path / "plan.dwg"
    dwg.write_bytes(b"AC1032" + b"\x00" * 50)
    # the "converter" ignores the DWG and returns the prepared DXF, proving the hook is run
    monkeypatch.setenv(
        "DWG_CONVERTER_CMD", f'"{sys.executable}" "{converter}" {{output}} "{dxf_path}"'
    )

    body = (await upload(client, dwg)).json()

    assert item(body, "walls")["quantity"] == pytest.approx(10.0)
