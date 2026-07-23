"""Run guarded Codex registration directly from one source tree."""

from __future__ import annotations

import os
import sys
from pathlib import Path


sys.dont_write_bytecode = True


ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from material_studio_mcp_server.managed_runtime import (
    consume_runtime_manifest_argument,
    require_managed_runtime_launcher_binding,
)


RUNTIME_MANIFEST_SHA256 = consume_runtime_manifest_argument(sys.argv, os.environ)
require_managed_runtime_launcher_binding(ROOT, RUNTIME_MANIFEST_SHA256)

from material_studio_mcp_server.codex_registration import main


if __name__ == "__main__":
    raise SystemExit(main())
