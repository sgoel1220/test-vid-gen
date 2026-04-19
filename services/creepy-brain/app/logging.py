"""Structured logging configuration using structlog"""

import logging
import sys

import structlog
from structlog.typing import Processor

from app.log_buffer import WorkflowLogHandler, structlog_capture_processor


def configure_logging(json_logs: bool = True) -> None:
    """Configure structlog for the application

    Args:
        json_logs: If True, output JSON logs. If False, use pretty console output for dev.
    """

    processors: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog_capture_processor,  # capture to per-workflow buffer
    ]

    if json_logs:
        processors.append(structlog.processors.JSONRenderer())
    else:
        # Pretty print for development
        processors.append(structlog.dev.ConsoleRenderer())

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # Also configure standard library logging
    logging.basicConfig(
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stdout,
        level=logging.INFO,
    )

    # Attach workflow buffer handler to root logger so stdlib log calls are captured.
    handler = WorkflowLogHandler()
    handler.setFormatter(logging.Formatter("%(name)s: %(message)s"))
    logging.getLogger().addHandler(handler)
