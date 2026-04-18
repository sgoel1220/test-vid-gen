from __future__ import annotations

from typing import Dict, List, Optional

from pydantic import BaseModel, Field

from enums import AudioFormat, JobStatus, ModelState, ModelType, ValidationFailure


class LiteCloneTTSRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=50000, description="Text to be synthesized.")
    reference_audio_filename: str = Field(
        ..., description="Filename inside reference_audio/ used for cloning."
    )
    output_format: AudioFormat = Field(
        AudioFormat.WAV, description="Encoded output format for chunks and final audio."
    )
    target_sample_rate: Optional[int] = Field(
        None, ge=8000, le=48000,
        description="Optional output sample rate override. Defaults to config audio_output.sample_rate.",
    )
    split_text: bool = Field(True, description="Whether to split long text into sentence-aware chunks.")
    chunk_size: int = Field(120, ge=50, le=1000, description="Approximate target character length for chunking.")
    temperature: Optional[float] = Field(None, ge=0.0, le=1.5, description="Overrides generation temperature.")
    exaggeration: Optional[float] = Field(None, ge=0.25, le=2.0, description="Overrides generation exaggeration.")
    cfg_weight: Optional[float] = Field(None, ge=0.2, le=1.0, description="Overrides CFG weight.")
    seed: Optional[int] = Field(None, ge=0, description="Overrides the generation seed. 0 keeps engine randomness.")
    speed_factor: Optional[float] = Field(None, ge=0.25, le=4.0, description="Post-generation speed multiplier.")
    language: Optional[str] = Field(None, description="Language passed through to the engine when supported.")
    enable_smart_stitching: bool = Field(
        True, description="If true, use crossfaded silence gaps between chunks. If false, use safety edge fades only.",
    )
    sentence_pause_ms: int = Field(200, ge=0, le=5000, description="Silence inserted between chunks in smart stitching mode.")
    crossfade_ms: int = Field(20, ge=0, le=500, description="Crossfade size used in smart stitching mode.")
    safety_fade_ms: int = Field(3, ge=0, le=100, description="Linear fade size used in fallback stitching mode.")
    enable_dc_removal: bool = Field(False, description="Apply high-pass DC offset removal before smart stitching.")
    dc_highpass_hz: int = Field(15, ge=1, le=200, description="High-pass cutoff used for DC removal.")
    peak_normalize_threshold: float = Field(0.99, ge=0.1, le=2.0, description="Normalize final audio if peak exceeds this threshold.")
    peak_normalize_target: float = Field(0.95, ge=0.1, le=1.0, description="Target peak used when normalization is triggered.")
    enable_silence_trimming: bool = Field(False, description="Trim leading/trailing silence on the stitched final audio.")
    enable_internal_silence_fix: bool = Field(False, description="Reduce long internal silences on the stitched final audio.")
    enable_unvoiced_removal: bool = Field(
        False, description="Remove long unvoiced spans from the stitched final audio if Parselmouth is available.",
    )
    max_reference_duration_sec: Optional[int] = Field(30, ge=1, le=600, description="Maximum allowed duration for the cloning reference file.")
    save_chunk_audio: bool = Field(True, description="Save each generated chunk to disk before stitching.")
    save_final_audio: bool = Field(True, description="Save the stitched final audio to disk.")
    run_label: Optional[str] = Field(None, description="Optional label added to the output directory name for easier grouping.")
    selected_chunk_indices: List[int] = Field(
        default_factory=list, description="Optional 1-based chunk indices to synthesize. Empty means all chunks.",
    )
    enable_chunk_validation: bool = Field(False, description="Validate each chunk for silence/truncation and retry on failure.")
    max_chunk_retries: int = Field(3, ge=0, le=10, description="Max synthesis retries per chunk when validation fails.")
    chunk_validation_min_rms: float = Field(1e-4, ge=0.0, description="Minimum RMS energy for a chunk to pass validation.")
    chunk_validation_min_peak: float = Field(1e-3, ge=0.0, description="Minimum peak amplitude for a chunk to pass validation.")
    chunk_validation_min_voiced_ratio: float = Field(0.05, ge=0.0, le=1.0, description="Minimum voiced-frame ratio for a chunk to pass validation.")
    enable_text_normalization: bool = Field(False, description="Use an LLM to normalize dates and dotted identifiers before TTS.")
    text_normalization_model_id: str = Field("Qwen/Qwen2.5-1.5B-Instruct", description="Hugging Face model ID used for text normalization.")


class ResolvedSettings(BaseModel):
    reference_audio_filename: str
    output_format: AudioFormat
    target_sample_rate: int
    split_text: bool
    chunk_size: int
    temperature: float
    exaggeration: float
    cfg_weight: float
    seed: int
    speed_factor: float
    language: str
    enable_smart_stitching: bool
    sentence_pause_ms: int
    crossfade_ms: int
    safety_fade_ms: int
    enable_dc_removal: bool
    dc_highpass_hz: int
    peak_normalize_threshold: float
    peak_normalize_target: float
    enable_silence_trimming: bool
    enable_internal_silence_fix: bool
    enable_unvoiced_removal: bool
    max_reference_duration_sec: Optional[int]
    save_chunk_audio: bool
    save_final_audio: bool
    run_label: Optional[str]
    enable_chunk_validation: bool
    max_chunk_retries: int
    chunk_validation_min_rms: float
    chunk_validation_min_peak: float
    chunk_validation_min_voiced_ratio: float
    enable_text_normalization: bool
    text_normalization_model_id: str
    selected_chunk_indices: List[int] = Field(default_factory=list)


class ChunkValidationResult(BaseModel):
    passed: bool
    duration_sec: float
    rms_energy: float
    peak_amplitude: float
    voiced_ratio: float
    failures: List[ValidationFailure]


class SavedAudioArtifact(BaseModel):
    filename: str
    relative_path: str
    url: str
    format: AudioFormat
    sample_rate: int
    target_sample_rate: int
    byte_size: int
    duration_sec: float


class SavedChunkInfo(BaseModel):
    index: int
    text: str
    artifact: Optional[SavedAudioArtifact] = None
    validation: Optional[ChunkValidationResult] = None
    attempts_used: int = 1


class LiteCloneRunResponse(BaseModel):
    run_id: str
    output_dir: str
    reference_audio_filename: str
    source_chunk_count: int
    chunk_count: int
    selected_chunk_indices: List[int] = Field(default_factory=list)
    resolved_settings: ResolvedSettings
    chunks: List[SavedChunkInfo]
    final_audio: Optional[SavedAudioArtifact] = None
    manifest_relative_path: str
    manifest_url: str
    warnings: List[str] = Field(default_factory=list)
    normalized_text: Optional[str] = None


class ChunkPreviewRequest(BaseModel):
    text: str = Field(..., min_length=1, description="Text to preview as chunks.")
    split_text: bool = Field(True, description="Whether to split long text into sentence-aware chunks.")
    chunk_size: int = Field(120, ge=50, le=1000, description="Approximate target character length for chunking.")


class ChunkPreviewInfo(BaseModel):
    index: int
    text: str
    char_count: int


class ChunkPreviewResponse(BaseModel):
    chunk_count: int
    chunks: List[ChunkPreviewInfo]


class LiteCloneJobCreatedResponse(BaseModel):
    job_id: str
    status_url: str


class LiteCloneJobStatusResponse(BaseModel):
    job_id: str
    status: JobStatus
    message: str
    progress_completed: int = 0
    progress_total: int = 0
    current_chunk_index: Optional[int] = None
    selected_chunk_indices: List[int] = Field(default_factory=list)
    result: Optional[LiteCloneRunResponse] = None
    error: Optional[str] = None


class ReferenceAudioFilesResponse(BaseModel):
    files: List[str]


class UploadReferenceAudioResponse(BaseModel):
    message: str
    uploaded_file: str
    already_exists: bool
    all_reference_files: List[str]


class SynthesizeRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=2000, description="Text to synthesize.")
    voice: str = Field(..., description="Reference audio filename for voice cloning.")
    seed: int = Field(0, ge=0, description="Random seed for reproducibility. 0 means random.")


class ModelInfo(BaseModel):
    state: ModelState
    loaded: bool
    loading: bool
    load_error: Optional[str]
    type: Optional[ModelType]
    class_name: Optional[str]
    device: Optional[str]
    sample_rate: Optional[int]
    supports_paralinguistic_tags: bool
    available_paralinguistic_tags: List[str]
    turbo_available_in_package: bool
    multilingual_available_in_package: bool
    supports_multilingual: bool
    supported_languages: Dict[str, str]
