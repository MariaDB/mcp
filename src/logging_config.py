"""
Logging configuration for the mariadb-mcp project.

This module sets up a dedicated logger that does NOT configure the root logger,
following Python best practices for library code.
"""
import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path
from pythonjsonlogger import jsonlogger


# Logger name for this project - NOT the root logger
LOGGER_NAME = "mariadb_mcp"


class CustomJsonFormatter(jsonlogger.JsonFormatter):
    """
    Custom JSON formatter that includes timestamp, calling context,
    and all relevant fields for structured logging.
    """
    def add_fields(self, log_record, record, message_dict):
        super(CustomJsonFormatter, self).add_fields(log_record, record, message_dict)

        # Ensure timestamp is always present
        if not log_record.get('timestamp'):
            log_record['timestamp'] = self.formatTime(record, self.datefmt)

        # Add calling context
        log_record['level'] = record.levelname
        log_record['logger'] = record.name
        log_record['module'] = record.module
        log_record['function'] = record.funcName
        log_record['line'] = record.lineno

        # Add process/thread info if relevant
        if record.process:
            log_record['process'] = record.process
        if record.thread:
            log_record['thread'] = record.thread


def setup_logger(
    log_level: str = "INFO",
    log_file_path: str = "logs/mcp_server.log",
    log_max_bytes: int = 10 * 1024 * 1024,
    log_backup_count: int = 5,
    enable_console: bool = True,
    enable_file: bool = True
) -> logging.Logger:
    """
    Set up the dedicated logger for mariadb-mcp.

    This function creates a logger with the name "mariadb_mcp" and configures
    it with console and/or file handlers. It does NOT touch the root logger.

    Args:
        log_level: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        log_file_path: Path to the log file
        log_max_bytes: Maximum size of log file before rotation
        log_backup_count: Number of backup log files to keep
        enable_console: Whether to enable console logging
        enable_file: Whether to enable file logging

    Returns:
        Configured logger instance
    """
    # Get the dedicated logger (NOT root logger)
    logger = logging.getLogger(LOGGER_NAME)

    # Set the level
    logger.setLevel(getattr(logging, log_level.upper(), logging.INFO))

    # Prevent propagation to root logger to avoid duplicate logs
    logger.propagate = False

    # Remove any existing handlers to avoid duplication
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)

    # Create formatter with timestamp and calling context
    formatter = CustomJsonFormatter(
        fmt='%(timestamp)s %(level)s %(name)s %(module)s %(funcName)s:%(lineno)d %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    # Console Handler
    if enable_console:
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)

    # File Handler
    if enable_file:
        # Ensure log directory exists
        log_file = Path(log_file_path)
        log_file.parent.mkdir(parents=True, exist_ok=True)

        file_handler = RotatingFileHandler(
            log_file,
            maxBytes=log_max_bytes,
            backupCount=log_backup_count
        )
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    return logger


def get_logger(name: str = None) -> logging.Logger:
    """
    Get a child logger under the mariadb_mcp logger hierarchy.

    Args:
        name: Optional name for the child logger. If None, returns the root mariadb_mcp logger.

    Returns:
        Logger instance
    """
    if name:
        return logging.getLogger(f"{LOGGER_NAME}.{name}")
    return logging.getLogger(LOGGER_NAME)


def setup_third_party_logging(level: str = "WARNING"):
    """
    Configure logging for third-party libraries like fastmcp, uvicorn, etc.

    This sets the logging level for known third-party loggers to reduce noise
    without touching the root logger.

    Args:
        level: Logging level for third-party libraries
    """
    third_party_loggers = [
        "fastmcp",
        "uvicorn",
        "uvicorn.access",
        "uvicorn.error",
        "starlette",
        "asyncmy",
        "httpx",
        "httpcore"
    ]

    log_level = getattr(logging, level.upper(), logging.WARNING)

    for logger_name in third_party_loggers:
        third_party_logger = logging.getLogger(logger_name)
        third_party_logger.setLevel(log_level)
        # Ensure they don't propagate excessively
        third_party_logger.propagate = True
