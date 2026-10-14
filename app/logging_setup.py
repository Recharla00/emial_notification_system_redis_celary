"""Application-only console logging; leave library verbosity unchanged."""
import logging
import sys
import time

from app.config import get_settings


def configure_logging():
    logger = logging.getLogger("app")
    logger.setLevel(get_settings().app_log_level)
    logger.propagate = False
    if not any(getattr(handler, "application_handler", False) for handler in logger.handlers):
        handler = logging.StreamHandler(sys.stderr)
        handler.application_handler = True
        formatter = logging.Formatter(
            "%(asctime)sZ %(levelname)s %(name)s process=%(processName)s %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S",
        )
        formatter.converter = time.gmtime
        handler.setFormatter(formatter)
        logger.addHandler(handler)
