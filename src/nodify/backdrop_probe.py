"""Check whether this machine actually blurs, instead of assuming it does.

Blur behind a window is a compositor feature reached through several Windows
APIs, one of them undocumented, and they do not behave the same way across
Windows builds or window styles. Measured on Windows 11 25H2 with a layered Qt
window: plain ``ACCENT_ENABLE_BLURBEHIND`` blurs, while
``ACCENT_ENABLE_ADVANCEDHOSTBACKDROP`` and the documented
``DWMWA_SYSTEMBACKDROP_TYPE`` do nothing at all and report success while doing
so. Every one of them is plausible, so nothing here can be settled by reading.

That failure mode is invisible: the window is still translucent and the text is
still readable, so the only symptom is that the effect looks like a flat tint.
Whether the effect landed has to be measured.

Run it directly::

    python -m nodify.backdrop_probe [screenshot.png]

It paints a deliberately harsh high-frequency pattern, puts one window per
candidate mechanism over it, captures the composited screen, and compares how
much fine detail survives in each. A blurred region has measurably less
high-frequency detail than the same pattern left sharp. The output answers "will
Nodify's glass look like glass on this PC", and the same measurement guards the
change that claims it will.

Pass a path to keep the capture, which is the only way to confirm a result that
looks wrong.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from dataclasses import dataclass

from PyQt6.QtCore import QRect, Qt
from PyQt6.QtGui import QColor, QFont, QGuiApplication, QImage, QPainter, QScreen, QShowEvent
from PyQt6.QtWidgets import QApplication, QWidget

from nodify.ui.win_backdrop import Backdrop, _candidates_for

#: The tint the samples paint. Matches the medium setting the tiles use, so the
#: probe measures the same thing the application will draw. It also has to be
#: translucent, or it would simply hide the compositor's work.
TINT = QColor(14, 14, 14, 140)

#: Sample window size, and the gap between samples.
SAMPLE_SIZE = QRect(0, 0, 220, 90)
MARGIN = 40
GAP = 20
SAMPLE_TOP = 120

#: Below this fraction of the control's detail, a mechanism counts as blurring.
BLURRED_BELOW = 0.75

#: Where the uncovered pattern is sampled from, below every sample row.
REFERENCE_TOP = 800

#: One candidate: something that can be pointed at a native handle and reports
#: whether the compositor accepted it.
Mechanism = Callable[[int], bool]


def _paint_harsh_pattern(painter: QPainter, rect: QRect) -> None:
    """Fill ``rect`` with 1-pixel alternating stripes.

    Real blur is easy to miss against a smooth gradient, where a blurred and an
    unblurred surface look nearly identical. Alternating single pixels give the
    blur something to destroy, so the measurement has something to measure.
    """
    painter.fillRect(rect, QColor(255, 255, 255))
    x = rect.left()
    while x < rect.right():
        painter.fillRect(QRect(x, rect.top(), 1, rect.height()), QColor(0, 0, 0))
        x += 2


class _Background(QWidget):
    """The full-screen 'desktop' the samples are judged against."""

    def paintEvent(self, event: object) -> None:  # noqa: N802 (Qt naming)
        _paint_harsh_pattern(QPainter(self), self.rect())


class _Sample(QWidget):
    """One translucent window with a single backdrop treatment applied."""

    def __init__(self, label: str) -> None:
        super().__init__(
            None,
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool,
        )
        self._label = label
        self._mechanism: object = None
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAutoFillBackground(False)
        self.resize(SAMPLE_SIZE.size())

    def paintEvent(self, event: object) -> None:  # noqa: N802 (Qt naming)
        painter = QPainter(self)
        painter.setBrush(TINT)
        painter.setPen(QColor(236, 236, 236, 200))
        painter.drawRoundedRect(self.rect().adjusted(0, 0, -1, -1), 10, 10)
        painter.setPen(QColor(245, 245, 245))
        painter.setFont(QFont("Segoe UI", 9))
        painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self._label)

    def showEvent(self, event: QShowEvent | None) -> None:  # noqa: N802 (Qt naming)
        # The native handle does not exist until now, so this is the first point
        # at which a backdrop can be attached at all.
        super().showEvent(event)


def _screen() -> QScreen:
    """The primary screen, or a hard failure.

    There is no sensible way to measure a screen effect without one, and letting
    ``None`` propagate would turn into an ``AttributeError`` halfway through
    leaving stray always-on-top windows on the user's desktop.
    """
    screen = QGuiApplication.primaryScreen()
    if screen is None:  # pragma: no cover - a running QApplication always has one
        raise RuntimeError("no screen is available, so no backdrop can be measured")
    return screen


def _capture(screen: QScreen) -> QImage:
    """Grab the whole composited screen.

    ``0`` means "the entire desktop" to Qt. The stub types the argument as a
    window handle pointer, which is the same value widened, hence the ignore.
    """
    return screen.grabWindow(0).toImage()  # type: ignore[arg-type]


@dataclass(frozen=True, slots=True)
class Result:
    label: str
    #: `None` when the region held no pixels, which is not a measurement of zero.
    sharpness: float | None


def _layout(count: int, screen: QRect) -> list[QRect] | None:
    """Grid positions for ``count`` samples, or ``None`` if they do not fit.

    Wrapping matters: a row wide enough for every sample runs off a 1600px
    screen, and a sample that lands off-screen reads as a perfectly flat region,
    which a naive "is it smoother" test then scores as a successful blur. That
    is how a mechanism gets reported working without ever being measured.
    """
    columns = max(1, (screen.width() - 2 * MARGIN + GAP) // (SAMPLE_SIZE.width() + GAP))
    rows = -(-count // columns)  # ceiling division
    needed_height = SAMPLE_TOP + rows * (SAMPLE_SIZE.height() + GAP)
    if needed_height > REFERENCE_TOP:
        return None

    return [
        QRect(
            MARGIN + (index % columns) * (SAMPLE_SIZE.width() + GAP),
            SAMPLE_TOP + (index // columns) * (SAMPLE_SIZE.height() + GAP),
            SAMPLE_SIZE.width(),
            SAMPLE_SIZE.height(),
        )
        for index in range(count)
    ]


def _sharpness(image: object, rect: QRect) -> float | None:
    """Mean absolute difference between horizontally adjacent pixels.

    A deliberately crude high-frequency energy measure. Blur suppresses exactly
    that, so a blurred patch scores low and a sharp one scores high. It does not
    need to be perceptual, only monotone.

    ``None`` means the region held no pixels, which is not a measurement of zero
    detail and must not be read as one.
    """
    left, right = max(0, rect.left()), min(image.width(), rect.right())  # type: ignore[attr-defined]
    top, bottom = max(0, rect.top()), min(image.height(), rect.bottom())  # type: ignore[attr-defined]
    if left >= right or top >= bottom:
        return None

    total = 0
    count = 0
    for y in range(top, bottom):
        previous: int | None = None
        for x in range(left, right):
            channel = image.pixel(x, y) & 0xFF  # type: ignore[attr-defined]
            if previous is not None:
                total += abs(channel - previous)
                count += 1
            previous = channel
    return total / count if count else None


def run(save: str | None = None) -> list[Result]:
    """Show every mechanism, capture the screen, and measure each one."""
    app = QApplication.instance() or QApplication(sys.argv)

    background = _Background()
    # Always on top, and raised before the samples. Without this the probe is at
    # the mercy of whatever else is on screen: a fullscreen game or video sits
    # above an ordinary window, the samples then blur *that* instead of the
    # stripes, and every reading comes out plausible while measuring nothing.
    background.setWindowFlags(
        Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
    )
    screen = _screen()
    background.setGeometry(screen.geometry())
    background.show()
    background.raise_()

    # One window per candidate mechanism, so each is measured against the same
    # pattern with nothing else in the way. The control comes first and gets no
    # mechanism at all, which is the known-sharp baseline.
    plan: list[tuple[str, Mechanism | None]] = [("control: no backdrop", None)]
    for backdrop in (Backdrop.ACRYLIC, Backdrop.BLUR, Backdrop.MICA):
        for name, candidate in _candidates_for(backdrop):
            plan.append((f"{backdrop.value} / {name}", candidate))

    geometry = screen.geometry()
    rects = _layout(len(plan), geometry)
    if rects is None:
        print(
            f"Cannot fit {len(plan)} samples on a {geometry.width()}x{geometry.height()} "
            "screen, so the run would measure windows that are not visible. "
            "Narrow the screen or widen the code's sample size."
        )
        background.close()
        return []

    windows: list[_Sample] = []
    for rect, (label, _mechanism) in zip(rects, plan, strict=True):
        window = _Sample(label)
        window.move(rect.topLeft())
        window.show()
        windows.append(window)

    for _ in range(12):
        app.processEvents()

    # Apply the mechanisms now, after every window has a real handle, so all of
    # them are identical up to this point and the only difference between them is
    # the effect itself.
    for window, (_, mechanism) in zip(windows, plan, strict=True):
        if mechanism is not None:
            mechanism(int(window.winId()))

    for _ in range(12):
        app.processEvents()

    image = _capture(screen)
    if save is not None:
        image.save(save)

    results = [
        Result(label, _sharpness(image, rect)) for rect, (label, _) in zip(rects, plan, strict=True)
    ]
    results.append(
        Result("reference: bare pattern", _sharpness(image, QRect(MARGIN, REFERENCE_TOP, 200, 60)))
    )

    for window in windows:
        window.close()
    background.close()
    return results


def main() -> int:
    save = sys.argv[1] if len(sys.argv) > 1 else None
    results = run(save)
    if not results:
        return 2

    reference = next((r.sharpness for r in results if r.label.startswith("reference")), None)
    control = next((r.sharpness for r in results if r.label.startswith("control")), None)
    if not reference or not control:
        print("The capture contained no measurable region; nothing can be concluded.")
        return 2

    # The control paints a 140/255 tint over the stripes, so its detail should
    # land near the bare pattern scaled by the tint's transparency, roughly
    # 115 here. A control near zero means the window is showing nothing at all
    # and the verdicts below are meaningless; a control near the bare pattern
    # means the tint was not applied and nothing can be told apart.
    expected = reference * (255 - TINT.alpha()) / 255
    if not expected * 0.4 <= control <= expected * 1.6:
        print(
            f"WARNING: expected a control near {expected:.0f} but measured {control:.0f}, "
            "so the windows were\nprobably not showing the pattern. Treat the verdicts "
            "as unreliable and look at\nat the saved screenshot."
        )
        return 2

    print(f"{'mechanism':<44} {'detail':>8} {'vs control':>11}  verdict")
    working: list[str] = []
    for result in results:
        if result.label.startswith(("reference", "control")):
            continue
        if result.sharpness is None:
            print(f"{result.label:<44} {'n/a':>8} {'n/a':>11}  not visible")
            continue
        ratio = result.sharpness / control
        if ratio < BLURRED_BELOW:
            working.append(result.label)
        verdict = "BLURRED" if ratio < BLURRED_BELOW else "no visible effect"
        print(f"{result.label:<44} {result.sharpness:8.1f} {ratio:10.2f}x  {verdict}")

    print(
        f"\nbare pattern {reference:.1f}   expected control {expected:.1f}   measured {control:.1f}"
    )
    if working:
        print("\nBlur works here. Mechanisms that produced it:")
        for label in working:
            print(f"  {label}")
        return 0
    print("\nNo mechanism changed the pixels. The tiles will show a flat tint.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
