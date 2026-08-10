"""无窗口启动入口：供 Windows 计划任务以 base pythonw.exe 直接调用。

背景：uv 创建的 venv 里 pythonw.exe 只是重定向器，会再拉起一个 CUI 子进程
python.exe，Windows 会为该子进程自动分配控制台窗口（开机弹出黑窗的根源）。
本入口绕过重定向器：由 base 解释器直接运行，手动把 venv 的 site-packages
加入 sys.path，全程单进程、纯 GUI 子系统，无任何窗口。

计划任务配置：
    Execute:   <base 解释器目录>\\pythonw.exe
    Arguments: "<项目根目录>\\serve_headless.py" serve
    WorkDir:   <项目根目录>
"""

import site
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def _find_venv_site_packages(root: Path) -> Path | None:
    """定位 venv 的 site-packages（兼容 Windows/POSIX 布局）。"""
    candidates = (
        root / ".venv" / "Lib" / "site-packages",  # Windows venv
        root / ".venv" / "lib" / "python3.11" / "site-packages",  # POSIX
    )
    for p in candidates:
        if p.is_dir():
            return p
    for p in (root / ".venv").glob("**/site-packages"):  # 兜底：任意 Python 版本
        if p.is_dir():
            return p
    return None


VENV_SITE = _find_venv_site_packages(ROOT)
if VENV_SITE is not None:
    site.addsitedir(str(VENV_SITE))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# pythonw 下 sys.stdout/stderr 为 None：先把它们重定向到日志文件，
# 否则任何 print/异常 traceback 都会直接杀死进程且不留痕迹（收敛自 logging_setup）
from src.logging_setup import redirect_stdio_if_detached  # noqa: E402

redirect_stdio_if_detached()

from src.main import main  # noqa: E402

if __name__ == "__main__":
    main()
