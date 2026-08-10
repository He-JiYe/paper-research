"""路径常量：项目根目录与关键子目录。

独立成模块，避免各层相互 import 造成环依赖。
"""

from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent  # src/ 上一级 = 项目根
CONFIG_DIR = ROOT_DIR / "config"
DATA_DIR = ROOT_DIR / "data"
LOG_DIR = ROOT_DIR / "log"
# 前端 SPA + 邮件模板/静态元数据统一目录（前后端分离，git 版本化）
APP_DIR = ROOT_DIR / "app"

CONFIG_PATH = CONFIG_DIR / "config.yaml"
