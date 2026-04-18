"""GPU provider abstraction layer."""

from .base import GpuPod, GpuPodSpec, GpuProvider
from .runpod import RunPodProvider


def get_provider(api_key: str) -> GpuProvider:
    """Return the RunPod GPU provider configured with *api_key*."""
    return RunPodProvider(api_key)


__all__ = ["GpuProvider", "GpuPodSpec", "GpuPod", "RunPodProvider", "get_provider"]
