"""Deploy the current reviewed commit to an immutable local runtime."""

from __future__ import annotations

import sys
from pathlib import Path


sys.dont_write_bytecode = True


ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from material_studio_mcp_server.runtime_deployment import main


if __name__ == "__main__":
    raise SystemExit(main())
