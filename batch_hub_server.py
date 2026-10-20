"""
ComfyUI 视频批量生产中枢系统 (Batch Hub Server) - 模块化工业重构版
遵循“底层架构与组织规范”：
1. web/    : 纯前端UI与组件交互，100% 物理路径隔离
2. server/ : 纯后端架构 (services, workers, routes, config, main)
"""

import os
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from server.main import main

if __name__ == "__main__":
    main()
