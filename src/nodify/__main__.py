"""Entry point for the Nodify overlay.

Run with ``python -m nodify``.
"""

from __future__ import annotations

import sys

from nodify.app.application import main

if __name__ == "__main__":
    sys.exit(main())
