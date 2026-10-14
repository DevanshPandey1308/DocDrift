"""Convenience launcher for an uninstalled source checkout."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "packages/core/src"))
sys.path.insert(0, str(Path(__file__).parent / "packages/cli/src"))

from docdrift_cli.main import main

raise SystemExit(main())
