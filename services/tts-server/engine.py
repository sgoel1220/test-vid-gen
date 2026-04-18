# File: engine.py
# Core TTS model loading and speech generation logic.

import gc
import logging
import random
import threading
from dataclasses import dataclass, field
import numpy as np
import torch
from typing import Any, Optional, Tuple

from chatterbox.tts import ChatterboxTTS  # Main TTS engine class
from chatterbox.models.s3gen.const import (
    S3GEN_SR,
)  # Default sample rate from the engine
from cpu_runtime import resolve_cpu_thread_settings, apply_torch_cpu_thread_settings

# Defensive Turbo import - Turbo may not be available in older package versions
try:
    from chatterbox.tts_turbo import ChatterboxTurboTTS

    turbo_available = True
except ImportError:
    ChatterboxTurboTTS = None
    turbo_available = False

# Defensive Multilingual import
try:
    from chatterbox import (
        ChatterboxMultilingualTTS,
        SUPPORTED_LANGUAGES as supported_languages,
    )

    multilingual_available = True
except ImportError:
    ChatterboxMultilingualTTS = None
    supported_languages = {}
    multilingual_available = False

# Import the singleton config_manager
from config import config_manager
from config import get_tts_cpu_num_threads, get_tts_cpu_num_interop_threads
from enums import DeviceType, ModelState, ModelType
from models import ModelInfo

logger = logging.getLogger(__name__)

# Log Turbo availability status at module load time
if turbo_available:
    logger.info("ChatterboxTurboTTS is available in the installed chatterbox package.")
else:
    logger.info("ChatterboxTurboTTS not available in installed chatterbox package.")

# Log Multilingual availability status at module load time
if multilingual_available:
    logger.info(
        "ChatterboxMultilingualTTS is available in the installed chatterbox package."
    )
    logger.info(f"Supported languages: {list(supported_languages.keys())}")
else:
    logger.info(
        "ChatterboxMultilingualTTS not available in installed chatterbox package."
    )

# Model selector whitelist - maps config values to model types
MODEL_SELECTOR_MAP: dict[str, ModelType] = {
    # Original model selectors
    "chatterbox": ModelType.ORIGINAL,
    "original": ModelType.ORIGINAL,
    "resembleai/chatterbox": ModelType.ORIGINAL,
    # Turbo model selectors
    "chatterbox-turbo": ModelType.TURBO,
    "turbo": ModelType.TURBO,
    "resembleai/chatterbox-turbo": ModelType.TURBO,
    # Multilingual model selectors
    "chatterbox-multilingual": ModelType.MULTILINGUAL,
    "multilingual": ModelType.MULTILINGUAL,
}

# Paralinguistic tags supported by Turbo model
TURBO_PARALINGUISTIC_TAGS = [
    "laugh",
    "chuckle",
    "sigh",
    "gasp",
    "cough",
    "clear throat",
    "sniff",
    "groan",
    "shush",
]


@dataclass
class _EngineState:
    """Single source of truth for all mutable engine state."""

    model: Optional[ChatterboxTTS] = None
    loaded: bool = False
    device: Optional[DeviceType] = None
    model_type: Optional[ModelType] = None
    model_class_name: Optional[str] = None
    cpu_interop_threads_configured: bool = False
    loading: bool = False
    load_error: Optional[str] = None
    lock: threading.Lock = field(default_factory=threading.Lock)
    load_thread: Optional[threading.Thread] = None

    def get_state(self) -> ModelState:
        """Return current model state under the assumption the lock is held."""
        if self.loading:
            return ModelState.LOADING
        if self.loaded:
            return ModelState.READY
        if self.load_error:
            return ModelState.ERROR
        return ModelState.NOT_LOADED


_engine = _EngineState()


def configure_cpu_threading() -> Tuple[int, int]:
    """Configures PyTorch to use the requested CPU thread counts."""
    num_threads, interop_threads = resolve_cpu_thread_settings(
        get_tts_cpu_num_threads(),
        get_tts_cpu_num_interop_threads(),
    )

    _engine.cpu_interop_threads_configured = apply_torch_cpu_thread_settings(
        torch,
        num_threads,
        interop_threads,
        _engine.cpu_interop_threads_configured,
        logger,
    )

    logger.info(
        "CPU runtime configured for Chatterbox with "
        f"{num_threads} intra-op threads and {interop_threads} inter-op threads"
    )
    return num_threads, interop_threads


def set_seed(seed_value: int):
    """
    Sets the seed for torch, random, and numpy for reproducibility.
    This is called if a non-zero seed is provided for generation.
    """
    torch.manual_seed(seed_value)
    if torch.cuda.is_available():
        torch.cuda.manual_seed(seed_value)
        torch.cuda.manual_seed_all(seed_value)  # if using multi-GPU
    if torch.backends.mps.is_available():
        torch.mps.manual_seed(seed_value)
    random.seed(seed_value)
    np.random.seed(seed_value)
    logger.info(f"Global seed set to: {seed_value}")


def _test_cuda_functionality() -> bool:
    """
    Tests if CUDA is actually functional, not just available.

    Returns:
        bool: True if CUDA works, False otherwise.
    """
    if not torch.cuda.is_available():
        return False

    try:
        test_tensor = torch.tensor([1.0])
        test_tensor = test_tensor.cuda()
        test_tensor = test_tensor.cpu()
        return True
    except Exception as e:
        logger.warning(f"CUDA functionality test failed: {e}")
        return False


def _test_mps_functionality() -> bool:
    """
    Tests if MPS is actually functional, not just available.

    Returns:
        bool: True if MPS works, False otherwise.
    """
    if not torch.backends.mps.is_available():
        return False

    try:
        test_tensor = torch.tensor([1.0])
        test_tensor = test_tensor.to("mps")
        test_tensor = test_tensor.cpu()
        return True
    except Exception as e:
        logger.warning(f"MPS functionality test failed: {e}")
        return False


def _get_model_class(selector: str) -> Tuple[Any, str]:
    """
    Determines which model class to use based on the config selector value.

    Args:
        selector: The value from config model.repo_id

    Returns:
        Tuple of (model_class, model_type)

    Raises:
        ImportError: If Turbo or Multilingual is selected but not available in the package
    """
    selector_normalized = selector.lower().strip()
    model_type = MODEL_SELECTOR_MAP.get(selector_normalized)

    if model_type == ModelType.TURBO:
        if not turbo_available:
            raise ImportError(
                f"Model selector '{selector}' requires ChatterboxTurboTTS, "
                f"but it is not available in the installed chatterbox package. "
                f"Please update the chatterbox-tts package to the latest version, "
                f"or use 'chatterbox' to select the original model."
            )
        logger.info(
            f"Model selector '{selector}' resolved to Turbo model (ChatterboxTurboTTS)"
        )
        return ChatterboxTurboTTS, ModelType.TURBO

    if model_type == ModelType.MULTILINGUAL:
        if not multilingual_available:
            raise ImportError(
                f"Model selector '{selector}' requires ChatterboxMultilingualTTS, "
                f"but it is not available in the installed chatterbox package. "
                f"Please update the chatterbox-tts package to the latest version, "
                f"or use 'chatterbox' to select the original model."
            )
        logger.info(
            f"Model selector '{selector}' resolved to Multilingual model (ChatterboxMultilingualTTS)"
        )
        return ChatterboxMultilingualTTS, ModelType.MULTILINGUAL

    if model_type == ModelType.ORIGINAL:
        logger.info(
            f"Model selector '{selector}' resolved to Original model (ChatterboxTTS)"
        )
        return ChatterboxTTS, ModelType.ORIGINAL

    # Unknown selector - default to original with warning
    logger.warning(
        f"Unknown model selector '{selector}'. "
        f"Valid values: chatterbox, chatterbox-turbo, chatterbox-multilingual, original, turbo, multilingual, "
        f"ResembleAI/chatterbox, ResembleAI/chatterbox-turbo. "
        f"Defaulting to original ChatterboxTTS model."
    )
    return ChatterboxTTS, ModelType.ORIGINAL


def get_model_info() -> ModelInfo:
    """
    Returns information about the currently loaded model.
    Used by the API to expose model details to the UI.
    """
    with _engine.lock:
        model_state = _engine.get_state()
        loaded = _engine.loaded
        loading = _engine.loading
        load_error = _engine.load_error
        current_model = _engine.model
        current_model_type = _engine.model_type
        current_model_class_name = _engine.model_class_name
        current_device = _engine.device

    return ModelInfo(
        state=model_state,
        loaded=loaded,
        loading=loading,
        load_error=load_error,
        type=current_model_type,
        class_name=current_model_class_name,
        device=current_device,
        sample_rate=current_model.sr if current_model else None,
        supports_paralinguistic_tags=current_model_type == ModelType.TURBO,
        available_paralinguistic_tags=(
            TURBO_PARALINGUISTIC_TAGS if current_model_type == ModelType.TURBO else []
        ),
        turbo_available_in_package=turbo_available,
        multilingual_available_in_package=multilingual_available,
        supports_multilingual=current_model_type == ModelType.MULTILINGUAL,
        supported_languages=(
            supported_languages
            if current_model_type == ModelType.MULTILINGUAL
            else {"en": "English"}
        ),
    )


def is_model_ready() -> bool:
    with _engine.lock:
        return _engine.loaded and _engine.model is not None


def _load_model_impl(mark_loading_started: bool) -> bool:
    """
    Loads the TTS model.
    This version directly attempts to load from the Hugging Face repository (or its cache)
    using `from_pretrained`, bypassing the local `paths.model_cache` directory.
    Updates `_engine` state fields.

    Returns:
        bool: True if the model was loaded successfully, False otherwise.
    """
    try:
        if not mark_loading_started:
            with _engine.lock:
                if _engine.loaded and _engine.model is not None:
                    logger.info("TTS model is already loaded.")
                    return True
                if _engine.loading:
                    logger.info("TTS model load already in progress.")
                    return False
                _engine.loading = True
                _engine.load_error = None

        # Determine processing device with robust CUDA detection and intelligent fallback
        device_setting = config_manager.get_string("tts_engine.device", DeviceType.AUTO)

        if device_setting == DeviceType.AUTO:
            if _test_cuda_functionality():
                resolved_device_str = DeviceType.CUDA
                logger.info("CUDA functionality test passed. Using CUDA.")
            elif _test_mps_functionality():
                resolved_device_str = DeviceType.MPS
                logger.info("MPS functionality test passed. Using MPS.")
            else:
                resolved_device_str = DeviceType.CPU
                logger.info("CUDA and MPS not functional or not available. Using CPU.")

        elif device_setting == DeviceType.CUDA:
            if _test_cuda_functionality():
                resolved_device_str = DeviceType.CUDA
                logger.info("CUDA requested and functional. Using CUDA.")
            else:
                resolved_device_str = DeviceType.CPU
                logger.warning(
                    "CUDA was requested in config but functionality test failed. "
                    "PyTorch may not be compiled with CUDA support. "
                    "Automatically falling back to CPU."
                )

        elif device_setting == DeviceType.MPS:
            if _test_mps_functionality():
                resolved_device_str = DeviceType.MPS
                logger.info("MPS requested and functional. Using MPS.")
            else:
                resolved_device_str = DeviceType.CPU
                logger.warning(
                    "MPS was requested in config but functionality test failed. "
                    "PyTorch may not be compiled with MPS support. "
                    "Automatically falling back to CPU."
                )

        elif device_setting == DeviceType.CPU:
            resolved_device_str = DeviceType.CPU
            logger.info("CPU device explicitly requested in config. Using CPU.")

        else:
            logger.warning(
                f"Invalid device setting '{device_setting}' in config. "
                f"Defaulting to auto-detection."
            )
            if _test_cuda_functionality():
                resolved_device_str = DeviceType.CUDA
            elif _test_mps_functionality():
                resolved_device_str = DeviceType.MPS
            else:
                resolved_device_str = DeviceType.CPU
            logger.info(f"Auto-detection resolved to: {resolved_device_str}")

        with _engine.lock:
            _engine.device = resolved_device_str
        logger.info(f"Final device selection: {resolved_device_str}")

        if resolved_device_str == DeviceType.CPU:
            configure_cpu_threading()

        # Get the model selector from config
        model_selector = config_manager.get_string("model.repo_id", "chatterbox-turbo")

        logger.info(f"Model selector from config: '{model_selector}'")

        try:
            # Determine which model class to use
            model_class, model_type = _get_model_class(model_selector)

            logger.info(
                f"Initializing {model_class.__name__} on device '{resolved_device_str}'..."
            )
            logger.info(f"Model type: {model_type}")
            if model_type == ModelType.TURBO:
                logger.info(
                    f"Turbo model supports paralinguistic tags: {TURBO_PARALINGUISTIC_TAGS}"
                )

            # Load the model using from_pretrained - handles HuggingFace downloads automatically
            loaded_model = model_class.from_pretrained(device=resolved_device_str)

            logger.info(
                f"Successfully loaded {model_class.__name__} on {resolved_device_str}"
            )
            logger.info(f"Model sample rate: {loaded_model.sr} Hz")
        except ImportError as e_import:
            logger.error(
                f"Failed to load model due to import error: {e_import}",
                exc_info=True,
            )
            with _engine.lock:
                _engine.model = None
                _engine.loaded = False
                _engine.loading = False
                _engine.load_error = str(e_import)
                _engine.model_type = None
                _engine.model_class_name = None
            return False
        except Exception as e_hf:
            logger.error(
                f"Failed to load model using from_pretrained: {e_hf}",
                exc_info=True,
            )
            with _engine.lock:
                _engine.model = None
                _engine.loaded = False
                _engine.loading = False
                _engine.load_error = str(e_hf)
                _engine.model_type = None
                _engine.model_class_name = None
            return False

        with _engine.lock:
            _engine.model = loaded_model
            _engine.model_type = model_type
            _engine.model_class_name = model_class.__name__
            _engine.loaded = True
            _engine.loading = False
            _engine.load_error = None

        if loaded_model:
            logger.info(
                f"TTS Model loaded successfully on {resolved_device_str}. Engine sample rate: {loaded_model.sr} Hz."
            )
        else:
            logger.error(
                "Model loading sequence completed, but model is None. This indicates an unexpected issue."
            )
            with _engine.lock:
                _engine.model = None
                _engine.loaded = False
                _engine.loading = False
                _engine.load_error = (
                    "Model loading completed without an instantiated model."
                )
                _engine.model_type = None
                _engine.model_class_name = None
            return False

        return True

    except Exception as e:
        logger.error(
            f"An unexpected error occurred during model loading: {e}", exc_info=True
        )
        with _engine.lock:
            _engine.model = None
            _engine.loaded = False
            _engine.loading = False
            _engine.load_error = str(e)
            _engine.model_type = None
            _engine.model_class_name = None
        return False


def load_model() -> bool:
    return _load_model_impl(mark_loading_started=False)


def start_background_model_load() -> bool:
    with _engine.lock:
        if _engine.loaded and _engine.model is not None:
            logger.info("TTS model is already loaded.")
            return False
        if _engine.loading and _engine.load_thread and _engine.load_thread.is_alive():
            logger.info("TTS model background load already in progress.")
            return False

        _engine.loading = True
        _engine.load_error = None

        _engine.load_thread = threading.Thread(
            target=_load_model_impl,
            args=(True,),
            name="chatterbox-model-loader",
            daemon=True,
        )
        _engine.load_thread.start()
        return True


def synthesize(
    text: str,
    audio_prompt_path: Optional[str] = None,
    temperature: float = 0.8,
    exaggeration: float = 0.5,
    cfg_weight: float = 0.5,
    seed: int = 0,
    language: str = "en",
) -> Tuple[Optional[torch.Tensor], Optional[int]]:
    """
    Synthesizes audio from text using the loaded TTS model.

    Args:
        text: The text to synthesize.
        audio_prompt_path: Path to an audio file for voice cloning or predefined voice.
        temperature: Controls randomness in generation.
        exaggeration: Controls expressiveness.
        cfg_weight: Classifier-Free Guidance weight.
        seed: Random seed for generation. If 0, default randomness is used.
              If non-zero, a global seed is set for reproducibility.
        language: Language code for multilingual model (e.g., 'en', 'it', 'de').

    Returns:
        A tuple containing the audio waveform (torch.Tensor) and the sample rate (int),
        or (None, None) if synthesis fails.
    """
    with _engine.lock:
        model = _engine.model
        model_type = _engine.model_type
        is_loaded = _engine.loaded

    if not is_loaded or model is None:
        logger.error("TTS model is not loaded. Cannot synthesize audio.")
        return None, None

    try:
        # Set seed globally if a specific seed value is provided and is non-zero.
        if seed != 0:
            logger.info(f"Applying user-provided seed for generation: {seed}")
            set_seed(seed)
        else:
            logger.info(
                "Using default (potentially random) generation behavior as seed is 0."
            )

        logger.debug(
            f"Synthesizing with params: audio_prompt='{audio_prompt_path}', temp={temperature}, "
            f"exag={exaggeration}, cfg_weight={cfg_weight}, seed_applied_globally_if_nonzero={seed}, "
            f"language={language}"
        )

        # Call the core model's generate method
        # Multilingual model requires language_id parameter
        if model_type == ModelType.MULTILINGUAL:
            wav_tensor = model.generate(
                text=text,
                language_id=language,
                audio_prompt_path=audio_prompt_path,
                temperature=temperature,
                exaggeration=exaggeration,
                cfg_weight=cfg_weight,
            )
        else:
            wav_tensor = model.generate(
                text=text,
                audio_prompt_path=audio_prompt_path,
                temperature=temperature,
                exaggeration=exaggeration,
                cfg_weight=cfg_weight,
            )

        # The ChatterboxTTS.generate method already returns a CPU tensor.
        return wav_tensor, model.sr

    except Exception as e:
        logger.error(f"Error during TTS synthesis: {e}", exc_info=True)
        return None, None


def unload_model() -> bool:
    """
    Unloads the current model and releases all GPU memory.
    Does NOT reload the model - use reload_model() for that.

    Returns:
        bool: True if the model was unloaded successfully, False otherwise.
    """
    logger.info("Initiating model unload sequence...")

    # 1. Unload existing model
    if _engine.model is not None:
        logger.info("Unloading TTS model from memory...")
        _engine.model = None

    # 2. Reset state flags
    with _engine.lock:
        _engine.loaded = False
        _engine.loading = False
        _engine.load_error = None
        _engine.device = None
        _engine.model_type = None
        _engine.model_class_name = None

    # 3. Force Python Garbage Collection (aggressive — models must swap in/out)
    gc.collect()
    gc.collect()  # Second pass to catch circular refs
    logger.info("Python garbage collection completed.")

    # 4. Clear GPU Cache (CUDA)
    if torch.cuda.is_available():
        logger.info("Clearing CUDA cache...")
        torch.cuda.empty_cache()
        torch.cuda.synchronize()  # Wait for all CUDA ops to finish
        free_mem, total_mem = torch.cuda.mem_get_info()
        logger.info("CUDA memory freed: %.2f GB / %.2f GB available", free_mem / 1024**3, total_mem / 1024**3)

    # 5. Clear GPU Cache (MPS - Apple Silicon)
    if torch.backends.mps.is_available():
        try:
            torch.mps.empty_cache()
            logger.info("Cleared MPS cache.")
        except AttributeError:
            logger.debug(
                "torch.mps.empty_cache() not available in this PyTorch version."
            )

    logger.info("Model unloaded and GPU memory released.")
    return True


def reload_model() -> bool:
    """
    Unloads the current model, clears GPU memory, and reloads the model
    based on the current configuration. Used for hot-swapping models
    without restarting the server process.

    Returns:
        bool: True if the new model loaded successfully, False otherwise.
    """
    logger.info("Initiating model hot-swap/reload sequence...")

    # 1. Unload existing model
    if _engine.model is not None:
        logger.info("Unloading existing TTS model from memory...")
        _engine.model = None

    # 2. Reset state flags
    with _engine.lock:
        _engine.loaded = False
        _engine.loading = False
        _engine.load_error = None
        _engine.device = None
        _engine.model_type = None
        _engine.model_class_name = None

    # 3. Force Python Garbage Collection
    gc.collect()
    logger.info("Python garbage collection completed.")

    # 4. Clear GPU Cache (CUDA)
    if torch.cuda.is_available():
        logger.info("Clearing CUDA cache...")
        torch.cuda.empty_cache()

    # 5. Clear GPU Cache (MPS - Apple Silicon)
    if torch.backends.mps.is_available():
        try:
            torch.mps.empty_cache()
            logger.info("Cleared MPS cache.")
        except AttributeError:
            # Older PyTorch versions may not have mps.empty_cache()
            logger.debug(
                "torch.mps.empty_cache() not available in this PyTorch version."
            )

    # 6. Reload model from the (now updated) configuration
    logger.info("Memory cleared. Reloading model from updated config...")
    return load_model()


# --- End File: engine.py ---
