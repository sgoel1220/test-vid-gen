from __future__ import annotations

import logging
import os
from typing import Optional, Tuple


def _positive_or_default(configured_value: Optional[int], fallback: int) -> int:
    """Returns a positive configured value, or the supplied fallback."""
    if configured_value is None or configured_value <= 0:
        return max(fallback, 1)
    return configured_value


def resolve_cpu_thread_settings(
    configured_num_threads: Optional[int],
    configured_interop_threads: Optional[int],
    logical_cpu_count: Optional[int] = None,
) -> Tuple[int, int]:
    """Resolves CPU intra-op and inter-op thread counts."""
    available_cpus = max(logical_cpu_count or os.cpu_count() or 1, 1)
    num_threads = _positive_or_default(configured_num_threads, available_cpus)
    interop_threads = _positive_or_default(configured_interop_threads, 1)
    interop_threads = min(interop_threads, num_threads)
    return num_threads, interop_threads


def apply_torch_cpu_thread_settings(
    torch_module,
    num_threads: int,
    interop_threads: int,
    interop_already_configured: bool,
    logger: logging.Logger,
) -> bool:
    """Applies PyTorch CPU thread settings and returns the inter-op configured state."""
    torch_module.set_num_threads(num_threads)
    logger.info(f"Configured PyTorch CPU intra-op threads to {num_threads}")

    if interop_already_configured:
        logger.info(
            "PyTorch CPU inter-op threads were already configured earlier in this process; keeping the existing setting."
        )
        return True

    try:
        torch_module.set_num_interop_threads(interop_threads)
        logger.info(f"Configured PyTorch CPU inter-op threads to {interop_threads}")
        return True
    except RuntimeError as exc:
        logger.warning(
            f"Could not configure PyTorch CPU inter-op threads to {interop_threads}: {exc}"
        )
        return False
