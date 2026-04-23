"""Enums for model fields."""

from enum import Enum


class WorkflowType(str, Enum):
    """Workflow types."""

    CONTENT_PIPELINE = "content_pipeline"


class WorkflowStatus(str, Enum):
    """Workflow execution status."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    PAUSED = "paused"


class StepName(str, Enum):
    """Workflow step names."""

    GENERATE_STORY = "generate_story"
    TTS_SYNTHESIS = "tts_synthesis"
    IMAGE_GENERATION = "image_generation"
    MUSIC_GENERATION = "music_generation"
    SFX_GENERATION = "sfx_generation"
    STITCH_FINAL = "stitch_final"
    WAVEFORM_OVERLAY = "waveform_overlay"
    CLEANUP_GPU_POD = "cleanup_gpu_pod"
    STEP_ONE = "step_one"
    STEP_TWO = "step_two"
    RECON_ORPHANED_PODS = "recon_orphaned_pods"


class StepStatus(str, Enum):
    """Step execution status."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class ChunkStatus(str, Enum):
    """Chunk processing status."""

    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class GpuProvider(str, Enum):
    """GPU provider types."""

    RUNPOD = "runpod"
    LOCAL = "local"
    MODAL = "modal"


class GpuPodStatus(str, Enum):
    """GPU pod status."""

    CREATING = "creating"
    RUNNING = "running"
    READY = "ready"
    STOPPED = "stopped"
    TERMINATED = "terminated"
    ERROR = "error"


class BlobType(str, Enum):
    """Blob content types."""

    CHUNK_AUDIO = "chunk_audio"
    CHUNK_AUDIO_MP3 = "chunk_audio_mp3"
    FINAL_AUDIO = "final_audio"
    IMAGE = "image"
    FINAL_VIDEO = "final_video"
    WAVEFORM_VIDEO = "waveform_video"
    VOICE_AUDIO = "voice_audio"
    MUSIC_AUDIO = "music_audio"
    SFX_AUDIO = "sfx_audio"
    MUSIC_BED = "music_bed"


class StoryStatus(str, Enum):
    """Story generation status."""

    PENDING = "pending"
    GENERATING = "generating"
    REVIEWING = "reviewing"
    COMPLETED = "completed"
    FAILED = "failed"


class RunStatus(str, Enum):
    """TTS run status."""

    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
