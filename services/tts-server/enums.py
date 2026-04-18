from enum import Enum


class AudioFormat(str, Enum):
    WAV = "wav"
    OPUS = "opus"
    MP3 = "mp3"


class ModelType(str, Enum):
    ORIGINAL = "original"
    TURBO = "turbo"
    MULTILINGUAL = "multilingual"


class DeviceType(str, Enum):
    AUTO = "auto"
    CUDA = "cuda"
    MPS = "mps"
    CPU = "cpu"


class JobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class ImageStyle(str, Enum):
    DARK_ATMOSPHERIC = "dark_atmospheric"
    COSMIC_HORROR = "cosmic_horror"
    GOTHIC = "gothic"
    SURREAL_NIGHTMARE = "surreal_nightmare"
    FOUND_FOOTAGE = "found_footage"
    PSYCHOLOGICAL = "psychological"
    FOLK_HORROR = "folk_horror"
    BODY_HORROR = "body_horror"


class ModelState(str, Enum):
    NOT_LOADED = "not_loaded"
    LOADING = "loading"
    READY = "ready"
    ERROR = "error"


class ValidationFailure(str, Enum):
    EMPTY = "empty"
    DURATION_TOO_SHORT = "duration_too_short"
    DURATION_TOO_LONG = "duration_too_long"
    SILENT_RMS = "silent_rms"
    SILENT_PEAK = "silent_peak"
    LOW_VOICED_RATIO = "low_voiced_ratio"
