"""GPU provider abstraction layer."""

from typing import Any

from .base import GpuPod, GpuPodSpec, GpuProvider, ImagePullStuckError
from .runpod import RunPodProvider
from .vastai import VastAIProvider


def get_provider(provider: str = "runpod", **kwargs: Any) -> GpuProvider:
    """Return a GPU provider instance for the given provider name.

    Args:
        provider: "runpod" or "vastai".
        **kwargs: Provider-specific keyword arguments.
            RunPod: api_key (str)
            VastAI: api_key (str), min_reliability (float), max_dph (float),
                    geo (str), cuda_min (float), max_inet_down_cost_per_tb (float)
    """
    if provider == "vastai":
        return VastAIProvider(
            api_key=str(kwargs["api_key"]),
            min_reliability=float(kwargs.get("min_reliability", 0.99)),
            max_dph=float(kwargs.get("max_dph", 2.0)),
            geo=str(kwargs.get("geo", "")),
            cuda_min=float(kwargs.get("cuda_min", 12.0)),
            max_inet_down_cost_per_tb=float(kwargs.get("max_inet_down_cost_per_tb", 0.0)),
        )
    return RunPodProvider(str(kwargs["api_key"]))


def get_provider_from_settings(provider_name: str | None = None) -> GpuProvider:
    """Build a provider from app settings.

    Args:
        provider_name: Override the provider to use.  If ``None``, reads
            ``settings.gpu_provider``.  Pass ``pod.provider.value`` from a
            persisted ``GpuPod`` record to ensure cleanup uses the same
            provider the pod was created with, even after a config change.
    """
    from app.config import settings  # lazy — avoids circular import

    name = provider_name if provider_name is not None else settings.gpu_provider
    if name == "vastai":
        return get_provider(
            "vastai",
            api_key=settings.vastai_api_key,
            min_reliability=settings.vastai_min_reliability,
            max_dph=settings.vastai_max_dph,
            geo=settings.vastai_geo,
            cuda_min=settings.vastai_cuda_min,
            max_inet_down_cost_per_tb=settings.vastai_max_inet_down_cost_per_tb,
        )
    return get_provider("runpod", api_key=settings.runpod_api_key)


__all__ = [
    "GpuProvider",
    "GpuPodSpec",
    "GpuPod",
    "RunPodProvider",
    "VastAIProvider",
    "get_provider",
    "get_provider_from_settings",
    "ImagePullStuckError",
]
