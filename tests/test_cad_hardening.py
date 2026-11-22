"""DXF/DWG hardening: arrays, nested blocks, hidden layers, entity types,
paper space, xrefs and converter selection. All drawings are written with
ezdxf to known dimensions."""

import subprocess

import ezdxf
import pytest
from ezdxf import units

from src import cad

URL = "/api/v1/takeoffs/drawing-cad"


def new_doc():
    doc = ezdxf.new("R2018")
    doc.units = units.FT
    for name in (
        "A-WALL",
        "A-DOOR",
        "A-FLOR",
        "A-ANNO",
        "0-MISC",
        "FROZEN-WALL",
        "OFF-WALL",
    ):
        doc.layers.add(name)
    door = doc.blocks.new("DOOR-36")
    door.add_line((0, 0), (3, 0))
    return doc


async def run(client, doc, tmp_path, **form):
    path = tmp_path / "plan.dxf"
    doc.saveas(path)
    with open(path, "rb") as fh:
        response = await client.post(
            URL, files={"file": ("plan.dxf", fh, "application/dxf")}, data=form
        )
    return response.json()


def item(body, category):
    return next((i for i in body["items"] if i["category"] == category), None)


def wall_attr():
    return {"layer": "A-WALL"}


@pytest.mark.asyncio
async def test_door_block_arrays_count_every_cell(client, tmp_path):
    doc = new_doc()
    msp = doc.modelspace()
    msp.add_blockref("DOOR-36", (0, 0), dxfattribs={"layer": "A-DOOR"})
    msp.add_blockref(
        "DOOR-36",
        (0, 10),
        dxfattribs={
            "layer": "A-DOOR",
            "column_count": 3,
            "row_count": 2,
            "column_spacing": 5,
            "row_spacing": 5,
        },
    )

    body = await run(client, doc, tmp_path)

    assert item(body, "doors")["quantity"] == 7.0  # 1 + 3 x 2


@pytest.mark.asyncio
async def test_nested_blocks_are_expanded_with_scale_and_layer_inheritance(
    client, tmp_path
):
    doc = new_doc()
    room = doc.blocks.new("ROOM")
    room.add_line((0, 0), (10, 0), dxfattribs={"layer": "0"})  # 10 ft, layer 0
    room.add_blockref("DOOR-36", (1, 0))  # nested door, layer 0
    outer = doc.blocks.new("WING")
    outer.add_blockref("ROOM", (0, 0))
    msp = doc.modelspace()
    # inserted at 2x scale on the wall layer: the 10 ft line measures 20 ft
    msp.add_blockref(
        "WING", (0, 0), dxfattribs={"layer": "A-WALL", "xscale": 2, "yscale": 2}
    )

    body = await run(client, doc, tmp_path)

    assert item(body, "walls")["quantity"] == pytest.approx(20.0)
    assert item(body, "doors")["quantity"] == 1.0
    assert body["metadata"]["blocks_expanded"] == 2


@pytest.mark.asyncio
async def test_layer_zero_geometry_on_an_unrelated_layer_is_not_counted(
    client, tmp_path
):
    doc = new_doc()
    room = doc.blocks.new("ROOM")
    room.add_line((0, 0), (10, 0), dxfattribs={"layer": "0"})
    doc.modelspace().add_blockref("ROOM", (0, 0), dxfattribs={"layer": "0-MISC"})

    body = await run(client, doc, tmp_path)

    assert body["items"] == []


@pytest.mark.asyncio
async def test_arrays_of_nested_blocks_multiply_geometry(client, tmp_path):
    doc = new_doc()
    room = doc.blocks.new("ROOM")
    room.add_line((0, 0), (10, 0), dxfattribs={"layer": "0"})
    room.add_blockref("DOOR-36", (1, 0))
    doc.modelspace().add_blockref(
        "ROOM",
        (0, 0),
        dxfattribs={
            "layer": "A-WALL",
            "column_count": 2,
            "row_count": 2,
            "column_spacing": 20,
            "row_spacing": 20,
        },
    )

    body = await run(client, doc, tmp_path)

    assert item(body, "walls")["quantity"] == pytest.approx(40.0)
    assert item(body, "doors")["quantity"] == 4.0


@pytest.mark.asyncio
async def test_frozen_off_and_invisible_entities_are_ignored(client, tmp_path):
    doc = new_doc()
    doc.layers.get("FROZEN-WALL").freeze()
    doc.layers.get("OFF-WALL").off()
    msp = doc.modelspace()
    msp.add_line((0, 0), (10, 0), dxfattribs=wall_attr())  # counted
    msp.add_line((0, 5), (100, 5), dxfattribs={"layer": "FROZEN-WALL"})
    msp.add_line((0, 6), (100, 6), dxfattribs={"layer": "OFF-WALL"})
    msp.add_line((0, 7), (100, 7), dxfattribs={"layer": "A-WALL", "invisible": 1})

    body = await run(client, doc, tmp_path)

    assert item(body, "walls")["quantity"] == pytest.approx(10.0)
    assert body["metadata"]["hidden_entities_ignored"] == 3


@pytest.mark.asyncio
async def test_spline_and_ellipse_walls_are_measured(client, tmp_path):
    doc = new_doc()
    msp = doc.modelspace()
    msp.add_spline([(0, 0), (5, 0), (10, 0)], dxfattribs=wall_attr())  # straight, 10 ft
    msp.add_ellipse(
        (50, 50), major_axis=(1, 0), ratio=1.0, dxfattribs=wall_attr()
    )  # circle r=1

    body = await run(client, doc, tmp_path)

    assert item(body, "walls")["quantity"] == pytest.approx(
        10.0 + 2 * 3.14159265, abs=0.05
    )


@pytest.mark.asyncio
async def test_mline_on_a_wall_layer_is_flagged_not_silently_dropped(client, tmp_path):
    doc = new_doc()
    msp = doc.modelspace()
    msp.add_line((0, 0), (10, 0), dxfattribs=wall_attr())
    msp.add_mline([(0, 5), (10, 5)], dxfattribs=wall_attr())

    body = await run(client, doc, tmp_path)

    assert any("MLINE" in w for w in body["metadata"]["warnings"])
    assert body["metadata"]["needs_review"] is True


@pytest.mark.asyncio
async def test_xref_geometry_is_flagged(client, tmp_path):
    doc = new_doc()
    doc.add_xref_def("other_floor.dxf", "OTHER")
    msp = doc.modelspace()
    msp.add_line((0, 0), (10, 0), dxfattribs=wall_attr())
    msp.add_blockref("OTHER", (0, 0), dxfattribs=wall_attr())

    body = await run(client, doc, tmp_path)

    assert item(body, "walls")["quantity"] == pytest.approx(10.0)
    assert any("External reference" in w for w in body["metadata"]["warnings"])
    assert (
        "xrefs not loaded" in str(body["metadata"]["warnings"])
        or body["metadata"]["needs_review"]
    )


@pytest.mark.asyncio
async def test_self_referencing_and_deeply_nested_blocks_terminate(client, tmp_path):
    doc = new_doc()
    loop = doc.blocks.new("LOOP")
    loop.add_line((0, 0), (1, 0), dxfattribs={"layer": "0"})
    loop.add_blockref("LOOP", (0, 0))  # references itself
    previous = "LOOP"
    for i in range(10):  # chain deeper than MAX_BLOCK_DEPTH
        blk = doc.blocks.new(f"L{i}")
        blk.add_blockref(previous, (0, 0))
        previous = f"L{i}"
    doc.modelspace().add_blockref(previous, (0, 0), dxfattribs=wall_attr())

    response = await run(client, doc, tmp_path)

    assert any("nested deeper" in w for w in response["metadata"]["warnings"])
    assert response["metadata"]["needs_review"] is True


@pytest.mark.asyncio
async def test_self_referencing_block_is_counted_once_not_per_recursion(
    client, tmp_path
):
    doc = new_doc()
    loop = doc.blocks.new("LOOP")
    loop.add_line((0, 0), (1, 0), dxfattribs={"layer": "0"})  # 1 ft
    loop.add_blockref("LOOP", (0, 0))
    doc.modelspace().add_blockref("LOOP", (0, 0), dxfattribs=wall_attr())

    body = await run(client, doc, tmp_path)

    assert item(body, "walls")["quantity"] == pytest.approx(1.0)  # not 8 (depth limit)
    assert body["metadata"]["blocks_expanded"] == 1
    assert any("referencing themselves" in w for w in body["metadata"]["warnings"])


@pytest.mark.asyncio
async def test_insert_cap_stops_runaway_expansion(client, tmp_path, monkeypatch):
    monkeypatch.setattr("src.cad.MAX_EXPANDED_INSERTS", 5)
    doc = new_doc()
    room = doc.blocks.new("ROOM")
    room.add_line((0, 0), (1, 0), dxfattribs={"layer": "0"})
    doc.modelspace().add_blockref(
        "ROOM",
        (0, 0),
        dxfattribs={
            "layer": "A-WALL",
            "column_count": 4,
            "row_count": 4,
            "column_spacing": 2,
            "row_spacing": 2,
        },
    )

    body = await run(client, doc, tmp_path)

    assert any("block insertions" in w for w in body["metadata"]["warnings"])


@pytest.mark.asyncio
async def test_paper_space_viewports_are_reported(client, tmp_path):
    doc = new_doc()
    doc.modelspace().add_line((0, 0), (10, 0), dxfattribs=wall_attr())
    layout = doc.layouts.new("Sheet A1")
    layout.add_viewport(
        center=(5, 5), size=(10, 10), view_center_point=(0, 0), view_height=20
    )

    body = await run(client, doc, tmp_path)

    (sheet,) = body["metadata"]["paper_layouts"]
    assert sheet["name"] == "Sheet A1" and sheet["paper_per_model"] == [0.5]
    assert any("paper-space" in w for w in body["metadata"]["warnings"])
    assert item(body, "walls")["quantity"] == pytest.approx(
        10.0
    )  # model space still read


# ------------------------------------------------------- DWG converter selection
def dxf_bytes(tmp_path):
    doc = new_doc()
    doc.modelspace().add_line((0, 0), (10, 0), dxfattribs=wall_attr())
    path = tmp_path / "converted.dxf"
    doc.saveas(path)
    return path.read_bytes()


def dwg_upload(tmp_path):
    path = tmp_path / "plan.dwg"
    path.write_bytes(b"AC1032" + b"\x00" * 64)
    return path


@pytest.fixture
def no_converters(monkeypatch):
    monkeypatch.delenv("DWG_CONVERTER_CMD", raising=False)
    monkeypatch.setattr(cad, "_oda_installed", lambda: False)
    monkeypatch.setattr(cad.shutil, "which", lambda name: None)


def test_converter_priority_is_env_then_oda_then_libredwg(monkeypatch, no_converters):
    assert cad.dwg_converters() == []
    monkeypatch.setattr(
        cad.shutil, "which", lambda name: "dwg2dxf" if name == "dwg2dxf" else None
    )
    assert [n for n, _ in cad.dwg_converters()] == ["libredwg"]
    monkeypatch.setattr(cad, "_oda_installed", lambda: True)
    assert [n for n, _ in cad.dwg_converters()] == ["odafc", "libredwg"]
    monkeypatch.setenv("DWG_CONVERTER_CMD", "my-converter {input} {output}")
    assert [n for n, _ in cad.dwg_converters()] == [
        "DWG_CONVERTER_CMD",
        "odafc",
        "libredwg",
    ]


@pytest.mark.asyncio
async def test_no_converter_gives_install_help(client, tmp_path, no_converters):
    with open(dwg_upload(tmp_path), "rb") as fh:
        response = await client.post(
            URL, files={"file": ("plan.dwg", fh, "application/octet-stream")}
        )

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert (
        "ODA File Converter" in detail
        and "dwg2dxf" in detail
        and "export to dxf" in detail.lower()
    )


@pytest.mark.asyncio
async def test_falls_back_to_the_next_converter_when_one_fails(
    client, tmp_path, monkeypatch, no_converters
):
    good = dxf_bytes(tmp_path)
    calls = []

    def failing_oda(src, dst):
        calls.append("odafc")
        raise subprocess.CalledProcessError(1, "ODAFileConverter")

    def working_libredwg(src, dst):
        calls.append("libredwg")
        dst.write_bytes(good)

    monkeypatch.setattr(cad, "_oda_installed", lambda: True)
    monkeypatch.setattr(cad.shutil, "which", lambda name: "dwg2dxf")
    monkeypatch.setattr(cad, "_run_oda", failing_oda)
    monkeypatch.setattr(cad, "_run_libredwg", working_libredwg)

    with open(dwg_upload(tmp_path), "rb") as fh:
        body = (
            await client.post(
                URL, files={"file": ("plan.dwg", fh, "application/octet-stream")}
            )
        ).json()

    assert calls == ["odafc", "libredwg"]
    assert item(body, "walls")["quantity"] == pytest.approx(10.0)


@pytest.mark.asyncio
async def test_all_converters_failing_reports_each_one(
    client, tmp_path, monkeypatch, no_converters
):
    def empty_output(src, dst):
        dst.write_bytes(b"")  # "succeeds" but produces nothing

    def timeout(src, dst):
        raise subprocess.TimeoutExpired("ODAFileConverter", 120)

    monkeypatch.setattr(cad, "_oda_installed", lambda: True)
    monkeypatch.setattr(cad.shutil, "which", lambda name: "dwg2dxf")
    monkeypatch.setattr(cad, "_run_oda", timeout)
    monkeypatch.setattr(cad, "_run_libredwg", empty_output)

    with open(dwg_upload(tmp_path), "rb") as fh:
        response = await client.post(
            URL, files={"file": ("plan.dwg", fh, "application/octet-stream")}
        )

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert "odafc: TimeoutExpired" in detail and "libredwg: produced no DXF" in detail


@pytest.mark.asyncio
async def test_health_lists_detected_converters(client, monkeypatch, no_converters):
    assert (await client.get("/health")).json()["dwg_converters"] == []
    monkeypatch.setattr(cad, "_oda_installed", lambda: True)

    health = (await client.get("/health")).json()

    assert health["dwg_converters"] == ["odafc"] and health["dwg_converter"] is True


# ------------------------------------------------------------ layer rule tuning
def german_doc():
    doc = ezdxf.new("R2018")
    doc.units = units.FT
    for name in ("WAND", "TUER", "BODEN", "WAND-MASS"):
        doc.layers.add(name)
    doc.blocks.new("TUER-90").add_line((0, 0), (3, 0))
    msp = doc.modelspace()
    msp.add_line((0, 0), (10, 0), dxfattribs={"layer": "WAND"})
    msp.add_line((0, 0), (999, 0), dxfattribs={"layer": "WAND-MASS"})  # dimension layer
    msp.add_blockref("TUER-90", (1, 0), dxfattribs={"layer": "TUER"})
    msp.add_lwpolyline(
        [(0, 0), (10, 0), (10, 10), (0, 10)], close=True, dxfattribs={"layer": "BODEN"}
    )
    return doc


@pytest.mark.asyncio
async def test_layer_rules_file_adapts_to_another_cad_standard(
    client, tmp_path, monkeypatch
):
    doc = german_doc()

    default = await run(client, doc, tmp_path)
    monkeypatch.setenv("LAYER_RULES_FILE", "examples/layer_rules.german.example.json")
    tuned = await run(client, doc, tmp_path)

    # built-in rules know "WALL" but not "WAND"/"TUER"/"BODEN": only the AIA-style
    # patterns can match, so nothing is found
    assert default["items"] == []
    assert item(tuned, "walls")["quantity"] == pytest.approx(10.0)  # WAND-MASS excluded
    assert item(tuned, "doors")["quantity"] == 1.0
    assert item(tuned, "flooring")["quantity"] == pytest.approx(100.0)


@pytest.mark.asyncio
async def test_bad_layer_rules_file_is_a_clear_server_error(
    client, tmp_path, monkeypatch
):
    bad = tmp_path / "rules.json"
    bad.write_text('{"walls": {"layers": "not-a-list"}}', encoding="utf-8")
    monkeypatch.setenv("LAYER_RULES_FILE", str(bad))

    response = await client.post(
        URL, files={"file": ("p.dxf", b"0\nSECTION\n", "application/dxf")}
    )

    assert response.status_code == 500
    assert "layer rules" in response.json()["detail"].lower()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "content",
    [
        b"",
        b"0\nSECTION\n",
        b"0\nSECTION\n2\nHEADER\n9\n$ACADVER\n1\nAC1027\n",
        b"not a dxf at all",
    ],
)
async def test_truncated_or_garbage_dxf_is_422_never_500(client, content):
    response = await client.post(
        URL, files={"file": ("p.dxf", content, "application/dxf")}
    )

    assert response.status_code == 422
    assert "DXF" in response.json()["detail"]


# ------------------------------------------------ multi-floor drawings + room schedule
RULES = "examples/layer_rules.jp-apartment.example.json"


def building_doc():
    """Two floors, each drawn at 1:100 and again at 1:50 as top-level blocks, with a
    room schedule held in ROOM block attributes (area in m2), one room labelled twice.
    """
    doc = ezdxf.new("R2018")
    doc.units = units.MM
    doc.layers.add("1F-F_FINI1-100")
    doc.layers.add("2F-F_FINI1-100")
    doc.layers.add("1F-F_FITT1-100")
    room = doc.blocks.new("ROOM")
    for tag in ("ID", "NAME", "AREA"):
        room.add_attdef(tag, (0, 0))
    doc.blocks.new("SD-A01_100").add_line((0, 0), (900, 0))
    doc.blocks.new("AW-A01_100").add_line((0, 0), (1800, 0))  # a window: not a door
    for floor, wall_mm, doors in (("1F", 10000, 2), ("2F", 3000, 1)):
        for scale in ("100", "50"):
            blk = doc.blocks.new(f"{floor}-BLOCK_{scale}")
            blk.add_line(
                (0, 0), (wall_mm, 0), dxfattribs={"layer": f"{floor}-F_FINI1-100"}
            )
            blk.add_line(
                (0, 200), (wall_mm, 200), dxfattribs={"layer": f"{floor}-F_FINI1-100"}
            )
            for i in range(doors):
                blk.add_blockref(
                    "SD-A01_100",
                    (500 + i * 2000, 0),
                    dxfattribs={"layer": f"{floor}-F_FITT1-100"},
                )
            blk.add_blockref(
                "AW-A01_100", (100, 0), dxfattribs={"layer": f"{floor}-F_FITT1-100"}
            )
            if floor == "1F":
                for rid, name, area in (
                    ("1F_1", "Hall", "10.0"),
                    ("1F_2", "Store", "5.5"),
                    ("1F_2", "Store", "5.5"),
                    ("1F_3", "Lobby", "-"),
                ):
                    ref = blk.add_blockref("ROOM", (0, 1000))
                    ref.add_attrib("ID", rid)
                    ref.add_attrib("NAME", name)
                    ref.add_attrib("AREA", area)
    msp = doc.modelspace()
    for floor in ("1F", "2F"):
        for scale in ("100", "50"):
            x = (
                0 if floor == "1F" else 100000
            )  # floors and scales sit side by side, as in real sheets
            y = 0 if scale == "100" else -50000
            msp.add_blockref(f"{floor}-BLOCK_{scale}", (x, y))
    return doc


async def run_with_rules(client, doc, tmp_path, monkeypatch, **form):
    monkeypatch.setenv("LAYER_RULES_FILE", RULES)
    return await run(client, doc, tmp_path, wall_representation="double_line", **form)


@pytest.mark.asyncio
async def test_include_blocks_reads_one_floor_at_one_scale(
    client, tmp_path, monkeypatch
):
    doc = building_doc()

    one = await run_with_rules(
        client, doc, tmp_path, monkeypatch, include_blocks="1F-BLOCK_100"
    )
    every = await run_with_rules(client, doc, tmp_path, monkeypatch)

    # 10 m of wall = 32.81 ft; 2 doors; the AW window block is not a door
    assert item(one, "walls")["quantity"] == pytest.approx(10000 / 304.8, abs=0.05)
    assert item(one, "doors")["quantity"] == 2.0
    # without a selection both floors AND both scales are counted: 2*(10+3) m, 2*(2+1) doors
    assert item(every, "walls")["quantity"] == pytest.approx(26000 / 304.8, abs=0.1)
    assert item(every, "doors")["quantity"] == 6.0
    names = {b["name"] for b in one["metadata"]["top_level_blocks"]}
    assert {"1F-BLOCK_100", "1F-BLOCK_50", "2F-BLOCK_100", "2F-BLOCK_50"} <= names
    assert one["metadata"]["include_blocks"] == ["1F-BLOCK_100"]


@pytest.mark.asyncio
async def test_include_blocks_accepts_globs_and_lists(client, tmp_path, monkeypatch):
    body = await run_with_rules(
        client,
        building_doc(),
        tmp_path,
        monkeypatch,
        include_blocks="1F-*_100, 2F-*_100",
    )

    assert item(body, "doors")["quantity"] == 3.0  # 2 on 1F + 1 on 2F, 1:100 only


@pytest.mark.asyncio
async def test_unknown_block_selection_lists_what_is_available(
    client, tmp_path, monkeypatch
):
    path = tmp_path / "plan.dxf"
    building_doc().saveas(path)
    monkeypatch.setenv("LAYER_RULES_FILE", RULES)
    with open(path, "rb") as fh:
        response = await client.post(
            URL,
            files={"file": ("plan.dxf", fh, "application/dxf")},
            data={"include_blocks": "9F-*"},
        )

    assert response.status_code == 422
    assert "1F-BLOCK_100" in response.json()["detail"]


@pytest.mark.asyncio
async def test_room_schedule_gives_flooring_without_duplicates(
    client, tmp_path, monkeypatch
):
    body = await run_with_rules(
        client, building_doc(), tmp_path, monkeypatch, include_blocks="1F-BLOCK_100"
    )

    flooring = item(body, "flooring")
    assert flooring["quantity"] == pytest.approx(
        (10.0 + 5.5) * 10.7639, abs=0.01
    )  # Store counted once
    assert (flooring["basis"], flooring["confidence"]) == ("schedule", "medium")
    meta = body["metadata"]
    assert [r["id"] for r in meta["rooms"]] == ["1F_1", "1F_2"]
    warnings = " ".join(meta["warnings"])
    assert "repeated room label" in warnings and "1F_2" in warnings
    assert "without a numeric area" in warnings and "Lobby" in warnings
    assert (
        "flooring from room schedule" in str(meta["warnings"]) or meta["needs_review"]
    )


@pytest.mark.asyncio
async def test_without_a_rooms_rule_no_flooring_is_invented(client, tmp_path):
    body = await run(client, building_doc(), tmp_path, include_blocks="1F-BLOCK_100")

    assert item(body, "flooring") is None
    assert body["metadata"]["rooms"] == []


@pytest.mark.asyncio
async def test_bad_rooms_rule_is_a_clear_error(client, tmp_path, monkeypatch):
    bad = tmp_path / "rules.json"
    bad.write_text(
        '{"walls": {"layers": []}, "doors": {"layers": [], "blocks": []}, '
        '"flooring": {"layers": []}, "rooms": {"blocks": ["ROOM"]}}',
        encoding="utf-8",
    )
    monkeypatch.setenv("LAYER_RULES_FILE", str(bad))

    response = await client.post(
        URL, files={"file": ("p.dxf", b"0\nSECTION\n", "application/dxf")}
    )

    assert response.status_code == 500 and "rooms" in response.json()["detail"]


def test_cad_unicode_escapes_are_decoded():
    from src.cad import _decode_cad_text, _parse_area

    bs = chr(92)  # CAD stores non-ASCII text as <backslash>U+XXXX
    assert _decode_cad_text(f"{bs}U+99D0{bs}U+8ECA{bs}U+5834") == "駐車場"
    assert _decode_cad_text("plain") == "plain"
    assert _parse_area("168.3") == 168.3
    assert _parse_area("１２．５") == 12.5  # full-width digits
    assert (
        _parse_area("-") is None
        and _parse_area("") is None
        and _parse_area("0") is None
    )
