"""
Structured (JSON) logging configuration for Maigie application.

Copyright (C) 2024 Maigie Team

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


class CustomJsonFormatter(jsonlogger.JsonFormatter):
    """
    Custom JSON formatter for structured logging.
    
    Enriches log records with standardized fields for consistent
    log aggregation and analysis.
    """
    
    def add_fields(
        self,
        log_record: dict[str, Any],
        record: logging.LogRecord,
        message_dict: dict[str, Any]
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
        log_record['timestamp'] = self.formatTime(record, self.datefmt)
        log_record['level'] = record.levelname
        log_record['logger'] = record.name
        log_record['module'] = record.module
        log_record['function'] = record.funcName
        log_record['line'] = record.lineno
        
        # Add exception information if present
        if record.exc_info:
            log_record['exception'] = self.formatException(record.exc_info)
        
        # Move the actual message to 'message' field
        if 'message' not in log_record and hasattr(record, 'getMessage'):
            log_record['message'] = record.getMessage()


def configure_logging() -> None:
    """
    Configure structured JSON logging for the application.
    
    This function should be called once during application startup.
    It configures the root logger to output JSON-formatted logs with
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
    
    # Create console handler with JSON formatter
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(log_level)
    
    # Create JSON formatter with custom fields
    json_formatter = CustomJsonFormatter(
        fmt='%(timestamp)s %(level)s %(name)s %(message)s',
        datefmt='%Y-%m-%dT%H:%M:%S',
        static_fields={
            'environment': environment,
            'application': 'maigie-backend',
        }
    )
    
    console_handler.setFormatter(json_formatter)
    root_logger.addHandler(console_handler)
    
    # Log configuration success
    root_logger.info(
        "Structured logging configured",
        extra={
            "log_level": log_level_str,
            "environment": environment,
        }
    )
    
    # Adjust third-party library log levels to reduce noise
    logging.getLogger("uvicorn").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("fastapi").setLevel(logging.INFO)


def get_logger(name: str) -> logging.Logger:
    """
    Get a logger instance with the specified name.
    
    This is a convenience function that returns a logger configured
    with the structured JSON formatter.
    
    Args:
        name: Name of the logger (typically __name__)
        
    Returns:
        Logger instance
    """
    return logging.getLogger(name)

