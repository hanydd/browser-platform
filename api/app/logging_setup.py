from __future__ import annotations

import logging.config

from .config import Settings


def configure_logging(settings: Settings) -> None:
    formatter_name = "json" if settings.log_json else "plain"
    logging.config.dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "plain": {
                    "format": "%(asctime)s %(levelname)s %(name)s %(message)s",
                },
                "json": {
                    "()": "pythonjsonlogger.jsonlogger.JsonFormatter",
                    "fmt": "%(asctime)s %(levelname)s %(name)s %(message)s",
                },
            },
            "handlers": {
                "default": {
                    "class": "logging.StreamHandler",
                    "formatter": formatter_name,
                }
            },
            "root": {
                "level": settings.log_level,
                "handlers": ["default"],
            },
        }
    )
