"""从源代码运行 Material Studio MCP 服务器。

此启动器允许 MCP 客户端在不安装包或设置 PYTHONPATH 的情况下启动服务器。
它故意不配置 Materials Studio 路径；服务器在启动时会自动探测本地安装。
"""

from __future__ import annotations

import sys
from pathlib import Path


# 项目根目录和源代码目录
ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"

# 将源代码目录添加到 Python 路径
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

# 导入主服务器模块
from material_studio_mcp_server.server import main


if __name__ == "__main__":
    main()
