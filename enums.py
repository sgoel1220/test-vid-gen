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
