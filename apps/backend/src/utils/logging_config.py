"""
Structured (JSON) logging configuration for Maigie application.

Copyright (C) 2025 Maigie

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU Affero General Public License as published
by the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU Affero General Public License for more details.

You should have received a copy of the GNU Affero General Public License
along with this program.  If not, see <https://www.gnu.org/licenses/>.
"""

import logging
import os
import sys
from typing import Any

from pythonjsonlogger import jsonlogger


class ColoredFormatter(logging.Formatter):
    """
    Colored formatter for development logs.
    Makes logs more readable with color-coded log levels.
    """
    
    # ANSI color codes
    COLORS = {
        'DEBUG': '\033[36m',      # Cyan
        'INFO': '\033[32m',       # Green
        'WARNING': '\033[33m',    # Yellow
        'ERROR': '\033[31m',      # Red
        'CRITICAL': '\033[35m',   # Magenta
    }
    RESET = '\033[0m'
    
    def format(self, record: logging.LogRecord) -> str:
        """Format log record with colors."""
        # Get color for log level
        color = self.COLORS.get(record.levelname, '')
        reset = self.RESET
        
        # Format timestamp
        timestamp = self.formatTime(record, self.datefmt or '%Y-%m-%d %H:%M:%S')
        
        # Format logger name (shorten if too long)
        logger_name = record.name
        if len(logger_name) > 25:
            logger_name = '...' + logger_name[-22:]
        
        # Build formatted message
        level_name = f"{color}{record.levelname:8s}{reset}"
        logger_str = f"{logger_name:28s}"
        
        # Format message
        message = record.getMessage()
        
        # Add extra fields if present (exclude noisy fields)
        extra_info = []
        if hasattr(record, '__dict__'):
            for key, value in record.__dict__.items():
                if key not in ['name', 'msg', 'args', 'created', 'filename', 'funcName', 
                              'levelname', 'levelno', 'lineno', 'module', 'msecs', 'message',
                              'pathname', 'process', 'processName', 'relativeCreated', 'thread',
                              'threadName', 'exc_info', 'exc_text', 'stack_info', 'taskName',
                              'environment', 'application', 'logger', 'module', 'function', 'line']:
                    if value and str(value).strip():
                        extra_info.append(f"{key}={value}")
        
        if extra_info:
            # Limit extra info to avoid clutter
            if len(extra_info) > 3:
                extra_info = extra_info[:3] + ['...']
            message += f" | {' | '.join(extra_info)}"
        
        # Format exception if present
        if record.exc_info:
            message += f"\n{self.formatException(record.exc_info)}"
        
        return f"{timestamp} | {level_name} | {logger_str} | {message}"


class CustomJsonFormatter(jsonlogger.JsonFormatter):
    """
    Custom JSON formatter for structured logging.

    Enriches log records with standardized fields for consistent
    log aggregation and analysis.
    """

    def add_fields(
        self, log_record: dict[str, Any], record: logging.LogRecord, message_dict: dict[str, Any]
    ) -> None:
        """
        Add custom fields to the log record.

        Args:
            log_record: Dictionary to populate with log fields
            record: The LogRecord being formatted
            message_dict: Additional message data
        """
        super().add_fields(log_record, record, message_dict)

        # Add essential fields for structured logging
        log_record["timestamp"] = self.formatTime(record, self.datefmt)
        log_record["level"] = record.levelname
        log_record["logger"] = record.name
        log_record["module"] = record.module
        log_record["function"] = record.funcName
        log_record["line"] = record.lineno

        # Add exception information if present
        if record.exc_info:
            log_record["exception"] = self.formatException(record.exc_info)

        # Move the actual message to 'message' field
        if "message" not in log_record and hasattr(record, "getMessage"):
            log_record["message"] = record.getMessage()


def configure_logging() -> None:
    """
    Configure logging for the application.
    
    In development: Uses colored, human-readable format
    In production: Uses structured JSON format

    This function should be called once during application startup.
    It configures the root logger to output logs with
    appropriate log levels based on the environment.

    Environment Variables:
        LOG_LEVEL: Desired log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
                   Defaults to INFO in production, DEBUG in development
        ENVIRONMENT: Application environment (development, production, staging)
    """
    # Determine log level from environment
    environment = os.getenv("ENVIRONMENT", "development").lower()
    log_level_str = os.getenv("LOG_LEVEL", "").upper()

    # Set default log level based on environment if not explicitly set
    if not log_level_str:
        log_level_str = "DEBUG" if environment == "development" else "INFO"

    # Convert string to logging level
    log_level = getattr(logging, log_level_str, logging.INFO)

    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)

    # Remove existing handlers to avoid duplicate logs
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    # Create console handler with encoding error handling
    class SafeStreamHandler(logging.StreamHandler):
        """StreamHandler that handles encoding errors gracefully."""
        def emit(self, record):
            try:
                super().emit(record)
            except UnicodeEncodeError:
                # If encoding fails, try to write with error replacement
                try:
                    msg = self.format(record)
                    # Replace any problematic characters
                    msg = msg.encode(self.stream.encoding or 'utf-8', errors='replace').decode(self.stream.encoding or 'utf-8', errors='replace')
                    stream = self.stream
                    stream.write(msg + self.terminator)
                    self.flush()
                except Exception:
                    # Last resort: write a safe ASCII message
                    self.handleError(record)
    
    console_handler = SafeStreamHandler(sys.stdout)
    console_handler.setLevel(log_level)

    # Choose formatter based on environment
    if environment == "development":
        # Use colored formatter for development
        formatter = ColoredFormatter(
            datefmt="%Y-%m-%d %H:%M:%S"
        )
    else:
        # Use JSON formatter for production/staging
        formatter = CustomJsonFormatter(
            fmt="%(timestamp)s %(level)s %(name)s %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S",
            static_fields={
                "environment": environment,
                "application": "maigie-backend",
            },
        )

    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    # Log configuration success
    root_logger.info(
        "Logging configured",
        extra={
            "log_level": log_level_str,
            "environment": environment,
            "format": "colored" if environment == "development" else "json",
        },
    )

    # Adjust third-party library log levels to reduce noise
    logging.getLogger("uvicorn").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("fastapi").setLevel(logging.INFO)
    logging.getLogger("httpx").setLevel(logging.WARNING)  # Reduce httpx noise


def get_logger(name: str) -> logging.Logger:
    """
    Get a logger instance with the specified name.

    This is a convenience function that returns a logger configured
    with the appropriate formatter based on environment.

    Args:
        name: Name of the logger (typically __name__)

    Returns:
        Logger instance
    """
    return logging.getLogger(name)
