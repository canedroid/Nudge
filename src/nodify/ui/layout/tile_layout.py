"""Tile layout: placement, dragging, snapping, swapping and resizing.

The layout is pure geometry with no widget knowledge, so every rule can be tested
without a screen. :class:`TileLayout` computes rectangles; the overlay applies them.

Three rules come from the plan (section 8.1).

**Dropping a tile on another swaps them, keeping their sizes.** A user rearranging
the panel wants the two tiles to trade places, not to resize.

**A tile dragged near an edge snaps to a grid.** Releasing near a neighbour lines
up with it, which makes a row readable without pixel-nudging.

**A tile resized while others are present does not push them around.** The tiles
overlap rather than reflow, because a productivity overlay is a set of independent
windows, not a document layout.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from enum import StrEnum

#: A drop within this many pixels of a neighbour counts as "onto" it, and a drop
#: within SNAP_DISTANCE of an edge counts as aligned.
SWAP_DISTANCE = 48
SNAP_DISTANCE = 12

#: Grid the tiles snap to, in pixels.
SNAP_GRID = 8


class TileId(StrEnum):
    """The four tiles, in their default left-to-right order."""

    TIMERS = "timers"
    NOTES = "notes"
    TODOS = "todos"
    FILES = "files"


#: The approved left-to-right order: timers, notes, to-dos, files.
DEFAULT_ORDER: tuple[TileId, ...] = (
    TileId.TIMERS,
    TileId.NOTES,
    TileId.TODOS,
    TileId.FILES,
)


@dataclass(frozen=True, slots=True)
class Rect:
    """An immutable rectangle in overlay coordinates."""

    x: int
    y: int
    width: int
    height: int

    @property
    def right(self) -> int:
        return self.x + self.width

    @property
    def bottom(self) -> int:
        return self.y + self.height

    def centre(self) -> tuple[int, int]:
        return (self.x + self.width // 2, self.y + self.height // 2)

    def contains_point(self, px: int, py: int) -> bool:
        return self.x <= px < self.right and self.y <= py < self.bottom

    def intersects(self, other: Rect) -> bool:
        return not (
            self.right <= other.x
            or other.right <= self.x
            or self.bottom <= other.y
            or other.bottom <= self.y
        )

    def distance_to(self, other: Rect) -> int:
        """The smallest gap between two rectangles; 0 when they overlap."""
        dx = max(0, max(other.x - self.right, self.x - other.right))
        dy = max(0, max(other.y - self.bottom, self.y - other.bottom))
        return max(dx, dy)

    def translated(self, dx: int, dy: int) -> Rect:
        return replace(self, x=self.x + dx, y=self.y + dy)

    def clamped_to(self, bounds: Rect) -> Rect:
        """Move, and shrink if needed, so the rectangle fits inside ``bounds``.

        A tile is never allowed to be dragged off screen: the header would then be
        unreachable and the tile could not be brought back.
        """
        width = min(self.width, bounds.width)
        height = min(self.height, bounds.height)
        x = max(bounds.x, min(self.x, bounds.x + bounds.width - width))
        y = max(bounds.y, min(self.y, bounds.y + bounds.height - height))
        return Rect(x=x, y=y, width=width, height=height)

    def resized(self, left: int, top: int, right: int, bottom: int) -> Rect:
        """A rectangle from two corners, with a minimum size enforced."""
        return normalise(left, top, right, bottom)


def normalise(left: int, top: int, right: int, bottom: int) -> Rect:
    """Build a rectangle from two corners in any order, with a minimum size."""
    x1, x2 = sorted((int(left), int(right)))
    y1, y2 = sorted((int(top), int(bottom)))
    return Rect(
        x=x1,
        y=y1,
        width=max(1, x2 - x1),
        height=max(1, y2 - y1),
    )


def snap(value: int, grid: int = SNAP_GRID) -> int:
    """Round a coordinate to the nearest grid line."""
    if grid <= 1:
        return int(value)
    return int(round(value / grid) * grid)


def snap_to_neighbour(value: int, others: Sequence[int], distance: int = SNAP_DISTANCE) -> int:
    """Align ``value`` with the nearest edge in ``others``, if one is close.

    Aligning with what is already there is what makes a row of tiles look
    deliberate, which nudging each one to the grid does not. The *nearest* edge
    wins rather than the first one found, so a tile sitting between two edges
    joins the closer one, which is what the user was aiming at.
    """
    best: int | None = None
    best_distance = distance
    for candidate in others:
        offset = abs(candidate - value)
        if offset <= best_distance:
            best = candidate
            best_distance = offset
    return value if best is None else best


@dataclass(frozen=True, slots=True)
class TileState:
    """One tile's identity and rectangle."""

    tile: TileId
    rect: Rect

    @property
    def id(self) -> TileId:
        return self.tile


@dataclass
class LayoutState:
    """The order and geometry of every tile.

    Mutable and replaceable in one step, so a drag can be applied atomically: a
    failed swap must not leave the order half-changed.
    """

    order: list[TileId] = field(default_factory=lambda: list(DEFAULT_ORDER))
    tiles: dict[TileId, Rect] = field(default_factory=dict)
    bounds: Rect = field(default_factory=lambda: Rect(0, 0, 1600, 900))

    def copy(self) -> LayoutState:
        return LayoutState(order=list(self.order), tiles=dict(self.tiles), bounds=self.bounds)

    def rect_for(self, tile: TileId) -> Rect:
        return self.tiles.get(tile, Rect(0, 0, 0, 0))

    def positions(self) -> list[TileState]:
        """Every tile, in current order."""
        return [TileState(tile, self.rect_for(tile)) for tile in self.order]

    def default_geometry(self, area: Rect, *, gap: int = 16) -> None:
        """Lay the tiles out in a row filling ``area``.

        The timers column is narrower, because it holds a countdown and a short
        list rather than an editor.
        """
        if not self.order:
            return
        self.bounds = area
        count = len(self.order)
        gap_total = gap * (count - 1)
        usable = max(0, area.width - gap_total)

        # Weighted widths: the timers column is deliberately the narrowest.
        weights = {
            TileId.TIMERS: 0.8,
            TileId.NOTES: 1.25,
            TileId.TODOS: 1.0,
            TileId.FILES: 1.0,
        }
        total_weight = sum(weights.get(t, 1.0) for t in self.order) or 1.0

        x = area.x
        for tile in self.order:
            share = weights.get(tile, 1.0) / total_weight
            width = int(usable * share)
            self.tiles[tile] = Rect(x, area.y, width, area.height)
            x += width + gap


class TileLayout:
    """Drag, swap, snap and resize over a :class:`LayoutState`."""

    def __init__(self, state: LayoutState | None = None) -> None:
        self._state = state or LayoutState()

    @property
    def state(self) -> LayoutState:
        return self._state

    def set_bounds(self, bounds: Rect) -> None:
        """Change the usable area, clamping every tile into it."""
        self._state.bounds = bounds
        for tile, rect in list(self._state.tiles.items()):
            self._state.tiles[tile] = rect.clamped_to(bounds)

    def move_tile(self, tile: TileId, to: Rect, *, snap_enabled: bool = True) -> Rect:
        """Move a tile, snapping and clamping it.

        The tile keeps its size; only its position changes.
        """
        current = self._state.rect_for(tile)
        candidate = Rect(x=to.x, y=to.y, width=current.width, height=current.height)
        if snap_enabled:
            candidate = self._snap(candidate, excluding=tile)
        final = candidate.clamped_to(self._state.bounds)
        self._state.tiles[tile] = final
        return final

    def _snap(self, rect: Rect, *, excluding: TileId) -> Rect:
        """Align a rectangle to nearby edges and to the grid."""
        vertical = [other.x for name, other in self._state.tiles.items() if name is not excluding]
        horizontal = [other.y for name, other in self._state.tiles.items() if name is not excluding]
        return Rect(
            x=snap_to_neighbour(snap(rect.x), vertical),
            y=snap_to_neighbour(snap(rect.y), horizontal),
            width=rect.width,
            height=rect.height,
        )

    def tile_at(self, px: int, py: int) -> TileId | None:
        """The topmost tile under a point, or ``None``."""
        for tile in reversed(self._state.order):
            if self._state.rect_for(tile).contains_point(px, py):
                return tile
        return None

    def swap_target(self, tile: TileId, rect: Rect) -> TileId | None:
        """The tile ``rect`` should swap with, if any.

        A swap is proposed when the dragged tile's centre has come within
        :data:`SWAP_DISTANCE` of another tile's centre. Using centres rather than
        edges means a long tile dropped on a short one still swaps, which is what
        the user meant.
        """
        cx, cy = rect.centre()
        best: TileId | None = None
        best_distance = SWAP_DISTANCE * 4

        for other in self._state.order:
            if other is tile:
                continue
            other_rect = self._state.rect_for(other)
            ox, oy = other_rect.centre()
            distance = max(abs(cx - ox), abs(cy - oy))
            if distance < best_distance:
                best = other
                best_distance = distance

        if best is None:
            return None
        # A swap needs real intent: the centres must actually be close.
        other_rect = self._state.rect_for(best)
        if rect.distance_to(other_rect) > SWAP_DISTANCE and best_distance > SWAP_DISTANCE:
            return None
        return best

    def swap(self, first: TileId, second: TileId) -> None:
        """Trade two tiles' positions, each keeping its own size."""
        if first is second:
            return
        order = self._state.order
        if first not in order or second not in order:
            return

        first_rect = self._state.rect_for(first)
        second_rect = self._state.rect_for(second)
        # Each tile takes the other's position but keeps its own dimensions, so
        # the narrow timers column stays narrow after moving.
        self._state.tiles[first] = Rect(
            second_rect.x, second_rect.y, first_rect.width, first_rect.height
        )
        self._state.tiles[second] = Rect(
            first_rect.x, first_rect.y, second_rect.width, second_rect.height
        )

        first_index = order.index(first)
        second_index = order.index(second)
        order[first_index], order[second_index] = second, first

    def drop_tile(self, tile: TileId, rect: Rect) -> tuple[Rect, TileId | None]:
        """Place a dragged tile, swapping it if it landed on another.

        Returns the final rectangle and the tile it swapped with, if any. The swap
        is applied *before* the move so the dragged tile ends up exactly where the
        other one was, rather than merely near it.
        """
        target = self.swap_target(tile, rect)
        if target is not None:
            self.swap(tile, target)
            return self._state.rect_for(tile), target
        return self.move_tile(tile, rect), None

    def resize_tile(self, tile: TileId, rect: Rect) -> Rect:
        """Resize a tile, clamped to the bounds and snapped."""
        candidate = rect.clamped_to(self._state.bounds)
        snapped = Rect(
            x=snap(candidate.x),
            y=snap(candidate.y),
            width=max(SNAP_GRID, snap(candidate.width)),
            height=max(SNAP_GRID, snap(candidate.height)),
        )
        self._state.tiles[tile] = snapped
        return snapped

    def resize_tile_from_corners(
        self, tile: TileId, left: int, top: int, right: int, bottom: int
    ) -> Rect:
        """Resize a tile given two drag corners."""
        return self.resize_tile(tile, normalise(left, top, right, bottom))

    def reset(self, area: Rect, *, gap: int = 16) -> None:
        """Return to the default arrangement."""
        self._state.tiles.clear()
        self._state.order = list(DEFAULT_ORDER)
        self._state.default_geometry(area, gap=gap)


__all__ = [
    "DEFAULT_ORDER",
    "SNAP_DISTANCE",
    "SNAP_GRID",
    "SWAP_DISTANCE",
    "LayoutState",
    "Rect",
    "TileId",
    "TileLayout",
    "TileState",
    "normalise",
    "snap",
    "snap_to_neighbour",
]
