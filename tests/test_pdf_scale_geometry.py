"""Scale, geometry and schedule tests against drawings with known ground truth.

At SCALE 1/4" = 1'-0" one paper foot (864 pt) is 48 real feet, so:
  360 pt = 20.0 ft,  240 pt = 13.33 ft,  9 pt = 6 in,  54 pt = 3.0 ft.
"""

import pytest

from tests.test_pdf_robustness import quantities, upload_pdf

SCALE_NOTE = 'SCALE: 1/4" = 1\'-0"'
PAGE = (1000, 800)


def note(c, text=SCALE_NOTE, y=40):
    c.drawString(50, y, text)


def item(body, category):
    return next((i for i in body["items"] if i["category"] == category), None)


@pytest.mark.asyncio
async def test_scale_converts_vector_length_to_real_feet(client, make_pdf):
    def draw(c, _):
        note(c)
        c.line(100, 300, 460, 300)  # 360 pt = 20 ft at 1/4" scale

    body = (await upload_pdf(client, make_pdf(draw, pagesize=PAGE))).json()
    meta = body["metadata"]

    assert meta["pages_with_scale"] == 1
    assert meta["scales"] == [SCALE_NOTE.split(": ")[1]]
    assert meta["vector_path_length_ft_real"] == pytest.approx(20.0, abs=0.01)
    assert meta["vector_path_length_ft_paper_space"] == pytest.approx(0.42, abs=0.01)


@pytest.mark.asyncio
async def test_double_line_walls_become_a_geometry_item(client, make_pdf):
    def draw(c, _):
        note(c)
        c.line(100, 500, 460, 500)  # 20 ft wall, two lines 9 pt (6 in) apart
        c.line(100, 509, 460, 509)
        c.line(600, 300, 600, 540)  # 13.33 ft wall
        c.line(609, 300, 609, 540)

    body = (await upload_pdf(client, make_pdf(draw, pagesize=PAGE))).json()
    walls = item(body, "walls")

    assert walls["quantity"] == pytest.approx(33.33, abs=0.1)
    assert walls["basis"] == "geometry"
    assert walls["confidence"] == "medium"
    assert body["metadata"]["needs_review"] is True


@pytest.mark.asyncio
async def test_walls_need_a_scale_and_a_plausible_thickness(client, make_pdf):
    def no_scale(c, _):
        c.line(100, 500, 460, 500)
        c.line(100, 509, 460, 509)

    def lone_line(c, _):
        note(c)
        c.line(100, 500, 460, 500)

    def too_far_apart(c, _):  # 100 pt = 66 in apart: not a wall
        note(c)
        c.line(100, 500, 460, 500)
        c.line(100, 600, 460, 600)

    for draw in (no_scale, lone_line, too_far_apart):
        body = (await upload_pdf(client, make_pdf(draw, pagesize=PAGE))).json()
        assert item(body, "walls") is None, draw.__name__


@pytest.mark.asyncio
async def test_door_swing_arcs_counted_only_at_door_size(client, make_pdf):
    def draw(c, _):
        note(c)
        c.arc(100, 300, 208, 408, 0, 90)  # radius 54 pt = 3.0 ft: a door
        c.arc(400, 300, 420, 320, 0, 90)  # radius 10 pt = 0.6 ft: too small
        c.arc(500, 300, 900, 700, 0, 90)  # radius 200 pt = 11 ft: too big

    body = (await upload_pdf(client, make_pdf(draw, pagesize=PAGE))).json()
    doors = item(body, "doors")

    assert doors["quantity"] == 1.0
    assert doors["basis"] == "geometry"
    assert doors["confidence"] == "low"
    assert body["metadata"]["door_swing_candidates"] == 3


@pytest.mark.asyncio
async def test_room_polygon_area_becomes_flooring(client, make_pdf):
    def draw(c, _):
        note(c)
        c.rect(100, 100, 360, 270)  # 20 ft x 15 ft = 300 sq ft
        c.drawString(200, 200, "OFFICE 101")

    body = (await upload_pdf(client, make_pdf(draw, pagesize=PAGE))).json()
    flooring = item(body, "flooring")

    assert flooring["quantity"] == pytest.approx(300.0, abs=0.5)
    assert flooring["basis"] == "geometry"
    assert flooring["confidence"] == "low"
    assert "OFFICE" in flooring["evidence"]


@pytest.mark.asyncio
async def test_text_callout_beats_geometry(client, make_pdf):
    def draw(c, _):
        note(c)
        c.rect(100, 100, 360, 270)
        c.drawString(200, 200, "OFFICE 101")
        c.drawString(50, 700, "Install 1,500 sq ft of vinyl tile flooring.")

    body = (await upload_pdf(client, make_pdf(draw, pagesize=PAGE))).json()
    flooring = item(body, "flooring")

    assert flooring["quantity"] == 1500.0
    assert flooring["basis"] == "text"
    assert flooring["confidence"] == "high"


def draw_door_schedule(c, with_qty):
    c.drawString(100, 640, "DOOR SCHEDULE")
    header = ["Door No.", "Type"] + (["Qty"] if with_qty else [])
    rows = [["D1", "A", "2"], ["D2", "B", "3"], ["D3", "A", "1"]]
    xs = [100 + 100 * i for i in range(len(header) + 1)]
    ys = [600 - 30 * i for i in range(5)]
    for y in ys:
        c.line(xs[0], y, xs[-1], y)
    for x in xs:
        c.line(x, ys[0], x, ys[-1])
    for r, row in enumerate([header] + [r[: len(header)] for r in rows]):
        for col, cell in enumerate(row):
            c.drawString(xs[col] + 8, ys[r] - 20, cell)


@pytest.mark.asyncio
async def test_door_schedule_quantity_column_is_summed(client, make_pdf):
    body = (
        await upload_pdf(client, make_pdf(lambda c, _: draw_door_schedule(c, True)))
    ).json()
    doors = item(body, "doors")

    assert doors["quantity"] == 6.0
    assert doors["basis"] == "schedule"
    assert doors["confidence"] == "high"


@pytest.mark.asyncio
async def test_door_schedule_without_qty_counts_rows(client, make_pdf):
    body = (
        await upload_pdf(client, make_pdf(lambda c, _: draw_door_schedule(c, False)))
    ).json()

    assert item(body, "doors")["quantity"] == 3.0


@pytest.mark.asyncio
async def test_schedule_and_callout_conflict_is_flagged(client, make_pdf):
    def draw(c, _):
        draw_door_schedule(c, True)
        c.drawString(100, 700, "Provide 4 doors.")

    body = (await upload_pdf(client, make_pdf(draw))).json()

    assert item(body, "doors")["quantity"] == 6.0  # schedule wins
    assert any("disagree" in w for w in body["metadata"]["warnings"])
    assert "door count conflict" in body["metadata"]["review_reasons"]


@pytest.mark.asyncio
async def test_ambiguous_scale_is_not_applied(client, make_pdf):
    def draw(c, _):
        note(c, 'PLAN: 1/4" = 1\'-0"   DETAIL: 1" = 1\'-0"')
        c.line(100, 500, 460, 500)
        c.line(100, 509, 460, 509)

    body = (await upload_pdf(client, make_pdf(draw, pagesize=PAGE))).json()

    assert item(body, "walls") is None
    assert body["metadata"]["pages_with_scale"] == 0
    assert "ambiguous scale" in body["metadata"]["review_reasons"]


@pytest.mark.asyncio
async def test_dimension_strings_are_reported(client, make_pdf):
    def draw(c, _):
        c.drawString(
            100, 700, "CORRIDOR 12'-6\" x 8'-0\"  door 3'-0\" wide  wall 3600 mm"
        )

    meta = (await upload_pdf(client, make_pdf(draw))).json()["metadata"]
    feet = [d["feet"] for d in meta["dimensions"]]

    assert feet == pytest.approx([12.5, 8.0, 3.0, 11.811], abs=0.001)
    assert meta["dimension_count"] == 4
