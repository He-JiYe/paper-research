"""无窗口启动入口：供 Windows 计划任务以 base pythonw.exe 直接调用。

背景：uv 创建的 venv 里 pythonw.exe 只是重定向器，会再拉起一个 CUI 子进程
python.exe，Windows 会为该子进程自动分配控制台窗口（开机弹出黑窗的根源）。
本入口绕过重定向器：由 base 解释器直接运行，手动把 venv 的 site-packages
加入 sys.path，全程单进程、纯 GUI 子系统，无任何窗口。

计划任务配置：
    Execute:   <base 解释器目录>\\pythonw.exe
    Arguments: "E:\\ZGCA-USTC-Phd\\Assert\\paper research\\serve_headless.py" serve
    WorkDir:   E:\\ZGCA-USTC-Phd\\Assert\\paper research
"""

import site
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV_SITE = ROOT / ".venv" / "Lib" / "site-packages"
if VENV_SITE.is_dir():
    site.addsitedir(str(VENV_SITE))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# pythonw 下 sys.stdout/stderr 为 None：先把它们重定向到日志文件，
# 否则任何 print/异常 traceback 都会直接杀死进程且不留痕迹
_LOG_DIR = ROOT / "output" / "logs"
_LOG_DIR.mkdir(parents=True, exist_ok=True)
if sys.stdout is None or sys.stderr is None:
    _fh = open(_LOG_DIR / "serve-stdout.log", "a", encoding="utf-8", errors="replace", buffering=1)  # noqa: SIM115
    if sys.stdout is None:
        sys.stdout = _fh
    if sys.stderr is None:
        sys.stderr = _fh

from src.main import main  # noqa: E402

if __name__ == "__main__":
    main()
