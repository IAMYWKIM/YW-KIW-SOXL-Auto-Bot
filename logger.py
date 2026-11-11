# -*- coding: utf-8 -*-
"""kiw_soxl_trader - 로깅 모듈 ([SOXL] 프리픽스, 일별 로테이션 14일 보관)

원본 엔진(engine/)의 로거 이름은 'toss_sniper.*' 이므로 같은 파일로 함께 모읍니다.
"""
import logging
from logging.handlers import TimedRotatingFileHandler

import config

_LOG_FILE = config.LOG_DIR / "kiw_soxl_trader.log"
_FMT = logging.Formatter(
    f"%(asctime)s {config.LOG_PREFIX} %(levelname)s %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
_configured = False


def _setup() -> None:
    global _configured
    if _configured:
        return
    level = getattr(logging, config.LOG_LEVEL.upper(), logging.INFO)
    fh = TimedRotatingFileHandler(_LOG_FILE, when="midnight", backupCount=14, encoding="utf-8")
    fh.setFormatter(_FMT)
    sh = logging.StreamHandler()
    sh.setFormatter(_FMT)
    for name in ("kiw_soxl", "toss_sniper"):
        lg = logging.getLogger(name)
        lg.setLevel(level)
        lg.addHandler(fh)
        lg.addHandler(sh)
        lg.propagate = False
    root = logging.getLogger()
    if not root.handlers:          # 엔진의 그 밖의 로거(이름 없는 것)도 놓치지 않도록
        root.setLevel(logging.WARNING)
        root.addHandler(fh)
    _configured = True


def get_logger(name: str = "soxl") -> logging.Logger:
    _setup()
    return logging.getLogger("kiw_soxl").getChild(name)
