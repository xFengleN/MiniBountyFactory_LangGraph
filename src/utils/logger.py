import logging
import os
from pathlib import Path
from logging.handlers import RotatingFileHandler
from typing import Optional

from ..core.config import config


class Logger:
    _loggers = {}

    @classmethod
    def get_logger(cls, name: str) -> logging.Logger:
        if name in cls._loggers:
            return cls._loggers[name]

        logger = logging.getLogger(name)
        logger.setLevel(logging.INFO)

        log_config = config.get('logging', {})
        log_file = log_config.get('file')
        log_level_str = log_config.get('level', 'INFO')

        if log_level_str == 'DEBUG':
            log_level = logging.DEBUG
        elif log_level_str == 'WARNING':
            log_level = logging.WARNING
        elif log_level_str == 'ERROR':
            log_level = logging.ERROR
        else:
            log_level = logging.INFO

        logger.setLevel(log_level)

        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )

        if log_file:
            Path(log_file).parent.mkdir(parents=True, exist_ok=True)
            max_bytes = log_config.get('max_bytes', 10485760)
            backup_count = log_config.get('backup_count', 5)
            handler = RotatingFileHandler(
                log_file,
                maxBytes=max_bytes,
                backupCount=backup_count
            )
            handler.setFormatter(formatter)
            logger.addHandler(handler)

        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)

        cls._loggers[name] = logger
        return logger


def get_logger(name: str) -> logging.Logger:
    return Logger.get_logger(name)