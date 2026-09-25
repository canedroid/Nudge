"""Geometry for the tile layout.

Every rule here is pure arithmetic with no widget involved, so the whole of the
drag, swap, snap and resize behaviour can be tested without a screen, a display,
or an event loop. That is the point: layout bugs are easy to introduce and hard to
reproduce by hand, and a rule that cannot be asserted cannot be trusted.
"""

from __future__ import annotations

import pytest

from nodify.ui.layout.tile_layout import (
    DEFAULT_ORDER,
    SNAP_DISTANCE,
    LayoutState,
    Rect,
    TileId,
    TileLayout,
    normalise,
    snap,
    snap_to_neighbour,
)

AREA = Rect(0, 0, 1600, 900)


@pytest.fixture
def layout() -> TileLayout:
    engine = TileLayout(LayoutState())
    engine.reset(AREA)
    return engine


class TestRect:
    def test_edges(self) -> None:
        rect = Rect(10, 20, 100, 200)
        assert rect.right == 110
        assert rect.bottom == 220

    def test_centre(self) -> None:
        assert Rect(0, 0, 100, 50).centre() == (50, 25)

    def test_contains_point(self) -> None:
        rect = Rect(0, 0, 10, 10)
        assert rect.contains_point(0, 0)
        assert rect.contains_point(9, 9)
        # The right and bottom edges are exclusive, so adjacent tiles do not
        # both claim the same pixel.
        assert not rect.contains_point(10, 5)
        assert not rect.contains_point(5, 10)
        assert not rect.contains_point(-1, 5)

    def test_intersects(self) -> None:
        a = Rect(0, 0, 10, 10)
        assert a.intersects(Rect(5, 5, 10, 10))
        assert a.intersects(Rect(0, 0, 10, 10))
        assert not a.intersects(Rect(10, 0, 10, 10))
        assert not a.intersects(Rect(20, 20, 5, 5))

    def test_distance_to_overlapping_is_zero(self) -> None:
        assert Rect(0, 0, 10, 10).distance_to(Rect(5, 5, 10, 10)) == 0

    def test_distance_to_separated(self) -> None:
        assert Rect(0, 0, 10, 10).distance_to(Rect(30, 0, 10, 10)) == 20

    def test_clamped_into_bounds(self) -> None:
        bounds = Rect(0, 0, 100, 100)
        assert Rect(150, 150, 20, 20).clamped_to(bounds) == Rect(80, 80, 20, 20)

    def test_clamped_keeps_a_usable_size(self) -> None:
        """A tile bigger than the bounds shrinks rather than vanishing."""
        bounds = Rect(0, 0, 100, 100)
        clamped = Rect(0, 0, 500, 500).clamped_to(bounds)
        assert clamped.width == 100
        assert clamped.height == 100

    def test_clamped_keeps_a_negative_tile_on_screen(self) -> None:
        """A tile dragged off the left edge stays reachable by the header."""
        assert Rect(-500, 0, 20, 20).clamped_to(Rect(0, 0, 100, 100)).x == 0

    def test_translated(self) -> None:
        assert Rect(0, 0, 10, 10).translated(5, -5) == Rect(5, -5, 10, 10)

    def test_is_immutable(self) -> None:
        rect = Rect(0, 0, 1, 1)
        with pytest.raises(AttributeError):
            rect.x = 5  # type: ignore[misc]


class TestNormalise:
    def test_orders_the_corners(self) -> None:
        assert normalise(10, 20, 0, 0) == Rect(0, 0, 10, 20)

    def test_already_ordered(self) -> None:
        assert normalise(0, 0, 10, 20) == Rect(0, 0, 10, 20)

    def test_enforces_a_minimum_size(self) -> None:
        assert normalise(5, 5, 5, 5).width == 1
        assert normalise(5, 5, 5, 5).height == 1

    def test_handles_a_reversed_drag(self) -> None:
        """Dragging the bottom-right corner up and left must still work."""
        assert normalise(300, 200, 100, 50) == Rect(100, 50, 200, 150)


class TestSnapping:
    def test_snaps_to_the_grid(self) -> None:
        assert snap(13) == 16
        assert snap(11) == 8
        assert snap(0) == 0

    def test_a_grid_of_one_is_the_identity(self) -> None:
        assert snap(37, grid=1) == 37

    def test_aligns_with_a_nearby_edge(self) -> None:
        assert snap_to_neighbour(105, [100]) == 100

    def test_ignores_a_distant_edge(self) -> None:
        assert snap_to_neighbour(500, [100]) == 500

    def test_ignores_an_edge_outside_the_threshold(self) -> None:
        assert snap_to_neighbour(100 + SNAP_DISTANCE + 1, [100]) == 100 + SNAP_DISTANCE + 1

    def test_uses_the_closest_candidate(self) -> None:
        """A tile between two edges joins the nearer one.

        Taking the first in range instead would snap to whichever edge happened to
        be listed first, which is not what the user was aiming at.
        """
        # 105 is 5 from 100 and 7 from 112, so 100 wins.
        assert snap_to_neighbour(105, [100, 112]) == 100
        # 111 is 11 from 100 and 1 from 112, so 112 wins.
        assert snap_to_neighbour(111, [100, 112]) == 112
        # A candidate exactly at the threshold is still in range.
        assert snap_to_neighbour(100 + SNAP_DISTANCE, [100]) == 100


class TestDefaultGeometry:
    def test_four_tiles(self, layout: TileLayout) -> None:
        assert len(layout.state.tiles) == 4

    def test_in_the_approved_order(self, layout: TileLayout) -> None:
        assert layout.state.order == list(DEFAULT_ORDER)
        assert layout.state.order[0] is TileId.TIMERS
        assert layout.state.order[-1] is TileId.FILES

    def test_tiles_fill_the_width(self, layout: TileLayout) -> None:
        tiles = list(layout.state.tiles.values())
        assert tiles[0].x == AREA.x
        # Integer division can leave a pixel of slack, so the last tile ends
        # within a few pixels of the edge rather than exactly on it.
        assert AREA.right - tiles[-1].right <= 4

    def test_tiles_do_not_overlap(self, layout: TileLayout) -> None:
        rects = list(layout.state.tiles.values())
        for index, first in enumerate(rects):
            for second in rects[index + 1 :]:
                assert not first.intersects(second)

    def test_tiles_fill_the_height(self, layout: TileLayout) -> None:
        for rect in layout.state.tiles.values():
            assert rect.height == AREA.height

    def test_the_timers_column_is_the_narrowest(self, layout: TileLayout) -> None:
        widths = {tile: rect.width for tile, rect in layout.state.tiles.items()}
        assert widths[TileId.TIMERS] == min(widths.values())

    def test_gutter_between_tiles(self, layout: TileLayout) -> None:
        left = layout.state.rect_for(TileId.TIMERS)
        right = layout.state.rect_for(TileId.NOTES)
        assert right.x - left.right == 16


class TestHitTesting:
    def test_finds_the_tile_under_a_point(self, layout: TileLayout) -> None:
        rect = layout.state.rect_for(TileId.NOTES)
        assert layout.tile_at(*rect.centre()) is TileId.NOTES

    def test_outside_every_tile(self, layout: TileLayout) -> None:
        assert layout.tile_at(-100, -100) is None

    def test_the_topmost_wins(self, layout: TileLayout) -> None:
        """Where tiles overlap after a drag, the later one is on top."""
        rect = layout.state.rect_for(TileId.NOTES)
        layout.state.tiles[TileId.FILES] = rect
        assert layout.tile_at(*rect.centre()) is TileId.FILES


class TestMove:
    def test_moves_a_tile(self, layout: TileLayout) -> None:
        """Snapping is on by default, so the result is a multiple of the grid."""
        moved = layout.move_tile(TileId.NOTES, Rect(400, 0, 0, 0))
        assert moved.x % 8 == 0
        assert moved.y == 0

    def test_moves_exactly_when_snapping_is_off(self, layout: TileLayout) -> None:
        moved = layout.move_tile(TileId.NOTES, Rect(400, 0, 0, 0), snap_enabled=False)
        assert moved.x == 400
        assert moved.y == 0

    def test_a_vertical_move_is_kept(self) -> None:
        """Vertical movement matters once a tile is shorter than the area."""
        engine = TileLayout(LayoutState())
        engine.reset(AREA)
        engine.resize_tile(TileId.NOTES, Rect(400, 0, 200, 200))
        moved = engine.move_tile(TileId.NOTES, Rect(400, 300, 0, 0), snap_enabled=False)
        assert moved.y == 300

    def test_keeps_the_size(self, layout: TileLayout) -> None:
        before = layout.state.rect_for(TileId.NOTES)
        layout.move_tile(TileId.NOTES, Rect(400, 0, 0, 0), snap_enabled=False)
        after = layout.state.rect_for(TileId.NOTES)
        assert (after.width, after.height) == (before.width, before.height)

    def test_clamps_inside_the_bounds(self, layout: TileLayout) -> None:
        moved = layout.move_tile(TileId.NOTES, Rect(99_999, 99_999, 0, 0))
        assert moved.right <= AREA.right
        assert moved.bottom <= AREA.bottom

    def test_snapping_can_be_disabled(self, layout: TileLayout) -> None:
        """A deliberate pixel position should survive when snapping is off."""
        layout.move_tile(TileId.NOTES, Rect(401, 0, 0, 0), snap_enabled=False)
        assert layout.state.rect_for(TileId.NOTES).x == 401

    def test_snaps_by_default(self, layout: TileLayout) -> None:
        layout.move_tile(TileId.NOTES, Rect(401, 0, 0, 0))
        assert layout.state.rect_for(TileId.NOTES).x % 8 == 0

    def test_aligns_with_a_neighbour(self) -> None:
        engine = TileLayout(LayoutState())
        engine.reset(AREA)
        # A short tile, so the target position is reachable without clamping.
        engine.resize_tile(TileId.NOTES, Rect(0, 0, 200, 200))
        target = engine.state.rect_for(TileId.FILES)
        engine.move_tile(TileId.NOTES, Rect(target.x + 3, 0, 0, 0))
        assert engine.state.rect_for(TileId.NOTES).x == target.x

    def test_alignment_is_ignored_when_snapping_is_off(self) -> None:
        """Turning snapping off must disable neighbour alignment too."""
        engine = TileLayout(LayoutState())
        engine.reset(AREA)
        # A short tile, so the target position is reachable without clamping.
        engine.resize_tile(TileId.NOTES, Rect(0, 0, 200, 200))
        target = engine.state.rect_for(TileId.TIMERS)
        engine.move_tile(TileId.NOTES, Rect(target.x + 3, 0, 0, 0), snap_enabled=False)
        assert engine.state.rect_for(TileId.NOTES).x == target.x + 3


class TestSwap:
    def test_swaps_positions(self, layout: TileLayout) -> None:
        before_notes = layout.state.rect_for(TileId.NOTES)
        before_files = layout.state.rect_for(TileId.FILES)
        layout.swap(TileId.NOTES, TileId.FILES)

        assert layout.state.rect_for(TileId.NOTES).x == before_files.x
        assert layout.state.rect_for(TileId.FILES).x == before_notes.x

    def test_each_tile_keeps_its_own_size(self, layout: TileLayout) -> None:
        """A narrow column must stay narrow after moving.

        Trading rectangles wholesale would give the timers column the notes
        column's width, which is not what the user sees when they drag.
        """
        notes_size = (
            layout.state.rect_for(TileId.NOTES).width,
            layout.state.rect_for(TileId.NOTES).height,
        )
        timers_size = (
            layout.state.rect_for(TileId.TIMERS).width,
            layout.state.rect_for(TileId.TIMERS).height,
        )
        layout.swap(TileId.NOTES, TileId.TIMERS)

        assert (
            layout.state.rect_for(TileId.NOTES).width,
            layout.state.rect_for(TileId.NOTES).height,
        ) == notes_size
        assert (
            layout.state.rect_for(TileId.TIMERS).width,
            layout.state.rect_for(TileId.TIMERS).height,
        ) == timers_size

    def test_swaps_the_order(self, layout: TileLayout) -> None:
        layout.swap(TileId.NOTES, TileId.FILES)
        assert layout.state.order.index(TileId.FILES) < layout.state.order.index(TileId.NOTES)

    def test_swapping_with_itself_does_nothing(self, layout: TileLayout) -> None:
        before = layout.state.rect_for(TileId.NOTES)
        layout.swap(TileId.NOTES, TileId.NOTES)
        assert layout.state.rect_for(TileId.NOTES) == before

    def test_swapping_is_a_no_op_when_a_tile_is_absent(self) -> None:
        engine = TileLayout(LayoutState())
        engine.reset(AREA)
        before = engine.state.tiles[TileId.NOTES]
        # A tile removed from the order but still holding a rectangle.
        engine.state.order.remove(TileId.NOTES)
        engine.swap(TileId.NOTES, TileId.FILES)
        assert engine.state.tiles[TileId.NOTES] == before


class TestSwapTarget:
    def test_proposes_a_swap_when_dropped_on_a_tile(self, layout: TileLayout) -> None:
        target_rect = layout.state.rect_for(TileId.FILES)
        proposed = layout.swap_target(TileId.TIMERS, target_rect)
        assert proposed is TileId.FILES

    def test_proposes_nothing_when_dropped_far_away(self, layout: TileLayout) -> None:
        assert layout.swap_target(TileId.TIMERS, Rect(-500, -500, 100, 100)) is None

    def test_proposes_nothing_for_itself(self, layout: TileLayout) -> None:
        rect = layout.state.rect_for(TileId.NOTES)
        assert layout.swap_target(TileId.NOTES, rect) is None


class TestDrop:
    def test_dropping_on_a_tile_swaps(self, layout: TileLayout) -> None:
        target_rect = layout.state.rect_for(TileId.FILES)
        _, swapped_with = layout.drop_tile(TileId.TIMERS, target_rect)
        assert swapped_with is TileId.FILES

    def test_a_swapped_tile_lands_exactly_where_the_other_was(self, layout: TileLayout) -> None:
        target_rect = layout.state.rect_for(TileId.FILES)
        final, _ = layout.drop_tile(TileId.TIMERS, target_rect)
        assert (final.x, final.y) == (target_rect.x, target_rect.y)

    def test_dropping_in_empty_space_just_moves(self, layout: TileLayout) -> None:
        final, swapped_with = layout.drop_tile(TileId.TIMERS, Rect(0, 0, 0, 0))
        assert swapped_with is None
        assert final.x == 0


class TestResize:
    def test_resizes(self, layout: TileLayout) -> None:
        """Sizes snap to the grid, so a round number stays a round number."""
        layout.resize_tile(TileId.NOTES, Rect(96, 96, 400, 400))
        rect = layout.state.rect_for(TileId.NOTES)
        assert rect.width == 400
        assert rect.height == 400

    def test_snaps_the_size(self, layout: TileLayout) -> None:
        layout.resize_tile(TileId.NOTES, Rect(0, 0, 301, 401))
        rect = layout.state.rect_for(TileId.NOTES)
        assert rect.width % 8 == 0
        assert rect.height % 8 == 0

    def test_enforces_a_minimum(self, layout: TileLayout) -> None:
        layout.resize_tile(TileId.NOTES, Rect(0, 0, 0, 0))
        rect = layout.state.rect_for(TileId.NOTES)
        assert rect.width >= 8
        assert rect.height >= 8

    def test_clamps_to_the_bounds(self, layout: TileLayout) -> None:
        layout.resize_tile(TileId.NOTES, Rect(0, 0, 99_999, 99_999))
        rect = layout.state.rect_for(TileId.NOTES)
        assert rect.width <= AREA.width
        assert rect.height <= AREA.height

    def test_from_drag_corners(self, layout: TileLayout) -> None:
        layout.resize_tile_from_corners(TileId.NOTES, 96, 96, 400, 500)
        rect = layout.state.rect_for(TileId.NOTES)
        assert (rect.x, rect.y, rect.width, rect.height) == (96, 96, 304, 400)

    def test_a_reversed_corner_drag_works(self, layout: TileLayout) -> None:
        layout.resize_tile_from_corners(TileId.NOTES, 400, 500, 100, 100)
        rect = layout.state.rect_for(TileId.NOTES)
        # The origin is snapped to the grid; the extent is what matters here.
        assert (rect.x, rect.y) == (96, 96)
        assert (rect.width, rect.height) == (304, 400)

    def test_resizing_does_not_move_the_others(self, layout: TileLayout) -> None:
        """Tiles are independent, not a flow layout.

        A document layout would reflow; an overlay's tiles must not, or resizing
        one would move three others the user had already arranged.
        """
        before = {t: r for t, r in layout.state.tiles.items() if t is not TileId.NOTES}
        layout.resize_tile(TileId.NOTES, Rect(0, 0, 200, 200))
        after = {t: r for t, r in layout.state.tiles.items() if t is not TileId.NOTES}
        assert before == after


class TestBounds:
    def test_changing_bounds_clamps_every_tile(self) -> None:
        engine = TileLayout(LayoutState())
        engine.reset(AREA)
        smaller = Rect(0, 0, 400, 300)
        engine.set_bounds(smaller)
        for rect in engine.state.tiles.values():
            assert rect.right <= smaller.right
            assert rect.bottom <= smaller.bottom


class TestReset:
    def test_returns_to_the_default(self, layout: TileLayout) -> None:
        original = layout.state.copy()
        layout.swap(TileId.TIMERS, TileId.FILES)
        layout.move_tile(TileId.NOTES, Rect(0, 0, 0, 0))
        layout.reset(AREA)

        assert layout.state.order == original.order
        assert layout.state.tiles == original.tiles

    def test_a_reset_on_an_empty_layout(self) -> None:
        engine = TileLayout(LayoutState())
        engine.reset(Rect(0, 0, 800, 600))
        assert len(engine.state.tiles) == 4


class TestLayoutState:
    def test_copy_is_independent(self, layout: TileLayout) -> None:
        copy = layout.state.copy()
        copy.tiles[TileId.NOTES] = Rect(0, 0, 1, 1)
        copy.order.clear()
        assert layout.state.tiles[TileId.NOTES] != Rect(0, 0, 1, 1)
        assert layout.state.order

    def test_positions_are_in_order(self, layout: TileLayout) -> None:
        positions = layout.state.positions()
        assert [p.tile for p in positions] == layout.state.order

    def test_rect_for_an_unplaced_tile_is_empty(self) -> None:
        state = LayoutState()
        assert state.rect_for(TileId.NOTES) == Rect(0, 0, 0, 0)
