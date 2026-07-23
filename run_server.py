"""从源代码运行 Material Studio MCP 服务器。

此启动器允许 MCP 客户端在不安装包或设置 PYTHONPATH 的情况下启动服务器。
它故意不配置 Materials Studio 路径；服务器在启动时会自动探测本地安装。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


sys.dont_write_bytecode = True


def _import_path(path: Path) -> Path:
    resolved = str(path.resolve())
    if os.name != "nt" or resolved.startswith("\\\\?\\"):
        return Path(resolved)
    if resolved.startswith("\\\\"):
        return Path("\\\\?\\UNC\\" + resolved[2:])
    return Path("\\\\?\\" + resolved)


# 项目根目录和源代码目录
ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
ROOT_IMPORT = _import_path(ROOT)
SRC_IMPORT = _import_path(SRC)

# 将源代码目录添加到 Python 路径
if str(SRC_IMPORT) not in sys.path:
    sys.path.insert(0, str(SRC_IMPORT))
os.chdir(ROOT_IMPORT)

# 导入主服务器模块
from material_studio_mcp_server.managed_runtime import (
    consume_runtime_manifest_argument,
    require_managed_runtime_launcher_binding,
)


RUNTIME_MANIFEST_SHA256 = consume_runtime_manifest_argument(sys.argv, os.environ)
require_managed_runtime_launcher_binding(ROOT, RUNTIME_MANIFEST_SHA256)

from material_studio_mcp_server.server import main


if __name__ == "__main__":
    main()
