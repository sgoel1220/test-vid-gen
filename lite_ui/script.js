document.addEventListener('DOMContentLoaded', () => {
    const form = document.getElementById('tts-form');
    const referenceAudioSelect = document.getElementById('reference-audio');
    const refreshReferenceButton = document.getElementById('refresh-reference');
    const referenceUploadInput = document.getElementById('reference-upload-input');
    const uploadReferenceButton = document.getElementById('upload-reference-btn');
    const previewButton = document.getElementById('preview-btn');
    const runSelectedButton = document.getElementById('run-selected-btn');
    const modelStatus = document.getElementById('model-status');
    const requestStatus = document.getElementById('request-status');
    const submitButton = document.getElementById('submit-btn');

    const jobStatusPill = document.getElementById('job-status-pill');
    const jobId = document.getElementById('job-id');
    const jobProgressText = document.getElementById('job-progress-text');
    const jobCurrentChunk = document.getElementById('job-current-chunk');
    const jobProgressFill = document.getElementById('job-progress-fill');
    const jobMessage = document.getElementById('job-message');

    const previewSummary = document.getElementById('preview-summary');
    const previewEmpty = document.getElementById('preview-empty');
    const previewContent = document.getElementById('preview-content');
    const previewCount = document.getElementById('preview-count');
    const previewSelectedCount = document.getElementById('preview-selected-count');
    const previewChunksList = document.getElementById('preview-chunks-list');

    const resultEmpty = document.getElementById('result-empty');
    const resultContent = document.getElementById('result-content');
    const runId = document.getElementById('run-id');
    const chunkCount = document.getElementById('chunk-count');
    const outputDir = document.getElementById('output-dir');
    const manifestLink = document.getElementById('manifest-link');
    const warningsBlock = document.getElementById('warnings-block');
    const warningsList = document.getElementById('warnings-list');
    const finalAudioBlock = document.getElementById('final-audio-block');
    const finalAudioPlayer = document.getElementById('final-audio-player');
    const finalAudioLink = document.getElementById('final-audio-link');
    const chunksList = document.getElementById('chunks-list');
    const resolvedSettings = document.getElementById('resolved-settings');

    const numberFields = [
        'target_sample_rate',
        'chunk_size',
        'temperature',
        'exaggeration',
        'cfg_weight',
        'seed',
        'speed_factor',
        'sentence_pause_ms',
        'crossfade_ms',
        'safety_fade_ms',
        'dc_highpass_hz',
        'peak_normalize_threshold',
        'peak_normalize_target',
        'max_reference_duration_sec',
        'max_chunk_retries',
        'chunk_validation_min_rms',
        'chunk_validation_min_peak',
        'chunk_validation_min_voiced_ratio'
    ];

    const booleanFields = [
        'split_text',
        'enable_smart_stitching',
        'enable_dc_removal',
        'enable_silence_trimming',
        'enable_internal_silence_fix',
        'enable_unvoiced_removal',
        'save_chunk_audio',
        'save_final_audio',
        'enable_chunk_validation',
        'enable_text_normalization'
    ];

    let previewChunks = [];
    let activeJobId = null;
    let jobPollHandle = null;
    let isJobRunning = false;
    let isModelReady = false;
    let modelPollHandle = null;

    async function parseJsonResponse(response) {
        const text = await response.text();
        if (!text) {
            return {};
        }

        try {
            return JSON.parse(text);
        } catch {
            throw new Error(`Server returned non-JSON response (status ${response.status}).`);
        }
    }

    function setStatus(message, isError = false) {
        requestStatus.textContent = message;
        requestStatus.style.color = isError ? '#ff9b9b' : '';
    }

    function formatJobStatus(status) {
        if (!status) {
            return 'Idle';
        }
        return status.charAt(0).toUpperCase() + status.slice(1);
    }

    function renderJobStatus(job) {
        const completed = Number(job.progress_completed || 0);
        const total = Number(job.progress_total || 0);
        const percent = total > 0 ? Math.max(0, Math.min(100, (completed / total) * 100)) : 0;

        jobStatusPill.textContent = formatJobStatus(job.status || 'idle');
        jobStatusPill.dataset.status = job.status || 'idle';
        jobId.textContent = job.job_id || '-';
        jobProgressText.textContent = total > 0 ? `${completed} / ${total}` : '0 / 0';
        jobCurrentChunk.textContent = job.current_chunk_index ? `Chunk ${job.current_chunk_index}` : '-';
        jobMessage.textContent = job.error || job.message || 'No active generation job.';
        jobProgressFill.style.width = `${percent}%`;
    }

    function resetJobStatus() {
        renderJobStatus({
            job_id: '-',
            status: 'idle',
            progress_completed: 0,
            progress_total: 0,
            current_chunk_index: null,
            message: 'Preview text chunks or run the full request.'
        });
    }

    function stopJobPolling() {
        if (jobPollHandle !== null) {
            window.clearTimeout(jobPollHandle);
            jobPollHandle = null;
        }
    }

    function getSelectedChunkIndices() {
        return Array.from(previewChunksList.querySelectorAll('input[data-chunk-checkbox]:checked'))
            .map((input) => parseInt(input.value, 10))
            .filter((value) => Number.isInteger(value));
    }

    function updatePreviewSelectionState() {
        const selectedCount = getSelectedChunkIndices().length;
        previewSelectedCount.textContent = String(selectedCount);
        runSelectedButton.disabled = isJobRunning || !isModelReady || !previewChunks.length || selectedCount === 0;
    }

    function updateGenerationAvailability() {
        submitButton.disabled = isJobRunning || !isModelReady;

        Array.from(previewChunksList.querySelectorAll('button.chunk-action-btn')).forEach((button) => {
            button.disabled = isJobRunning || !isModelReady;
        });

        updatePreviewSelectionState();
    }

    function setInteractiveState(disabled) {
        isJobRunning = disabled;

        Array.from(form.elements).forEach((element) => {
            element.disabled = disabled;
        });

        Array.from(previewChunksList.querySelectorAll('input, button')).forEach((element) => {
            element.disabled = disabled;
        });

        updateGenerationAvailability();
    }

    function stopModelPolling() {
        if (modelPollHandle !== null) {
            window.clearTimeout(modelPollHandle);
            modelPollHandle = null;
        }
    }

    function scheduleModelInfoPoll(delayMs = 1500) {
        stopModelPolling();
        modelPollHandle = window.setTimeout(() => {
            loadModelInfo();
        }, delayMs);
    }

    function renderModelInfo(info) {
        const state = info.state || (info.loaded ? 'ready' : 'not_loaded');
        isModelReady = Boolean(info.loaded);
        modelStatus.dataset.status = state;

        if (state === 'ready') {
            modelStatus.textContent = `${info.type || 'unknown'} on ${info.device || 'unknown'} @ ${info.sample_rate || '?'} Hz`;
            stopModelPolling();
        } else if (state === 'loading') {
            const deviceLabel = info.device ? ` on ${info.device}` : '';
            modelStatus.textContent = `Model loading${deviceLabel}...`;
            scheduleModelInfoPoll();
        } else if (state === 'error') {
            modelStatus.textContent = `Model failed: ${info.load_error || 'Unknown load error'}`;
            stopModelPolling();
        } else {
            modelStatus.textContent = 'Model not loaded';
            scheduleModelInfoPoll();
        }

        updateGenerationAvailability();
    }

    async function loadModelInfo() {
        try {
            const response = await fetch('/api/model-info');
            const info = await parseJsonResponse(response);
            if (!response.ok) {
                throw new Error(info.detail || 'Failed to load model info.');
            }

            renderModelInfo(info);
        } catch (error) {
            isModelReady = false;
            modelStatus.dataset.status = 'error';
            modelStatus.textContent = `Model info error: ${error.message}`;
            updateGenerationAvailability();
            scheduleModelInfoPoll(3000);
        }
    }

    async function loadReferenceAudioFiles(preferredFileName = null) {
        referenceAudioSelect.innerHTML = '<option>Loading...</option>';
        try {
            const response = await fetch('/api/reference-audio');
            const result = await parseJsonResponse(response);
            if (!response.ok) {
                throw new Error(result.detail || 'Failed to load reference audio files.');
            }

            const files = result.files || [];
            const currentSelection = preferredFileName || referenceAudioSelect.value;
            referenceAudioSelect.innerHTML = '';
            if (!files.length) {
                referenceAudioSelect.innerHTML = '<option value="">No reference audio files found</option>';
                return;
            }

            files.forEach((fileName) => {
                const option = document.createElement('option');
                option.value = fileName;
                option.textContent = fileName;
                referenceAudioSelect.appendChild(option);
            });

            if (currentSelection && files.includes(currentSelection)) {
                referenceAudioSelect.value = currentSelection;
                return;
            }

            referenceAudioSelect.value = files[0];
        } catch (error) {
            referenceAudioSelect.innerHTML = '<option value="">Reference list unavailable</option>';
            setStatus(error.message, true);
        }
    }

    async function uploadReferenceAudio() {
        const file = referenceUploadInput.files && referenceUploadInput.files[0];
        if (!file) {
            setStatus('Choose a .wav or .mp3 reference file first.', true);
            return;
        }

        uploadReferenceButton.disabled = true;
        setStatus(`Uploading ${file.name}...`);

        try {
            const formData = new FormData();
            formData.append('file', file);

            const response = await fetch('/api/reference-audio/upload', {
                method: 'POST',
                body: formData
            });
            const result = await parseJsonResponse(response);

            if (!response.ok) {
                throw new Error(result.detail || 'Failed to upload reference audio.');
            }

            await loadReferenceAudioFiles(result.uploaded_file || file.name);
            referenceUploadInput.value = '';
            setStatus(result.message || `Uploaded ${file.name}`);
        } catch (error) {
            setStatus(error.message, true);
        } finally {
            uploadReferenceButton.disabled = false;
        }
    }

    function buildPayload(selectedChunkIndices = []) {
        const formData = new FormData(form);
        const payload = {
            text: formData.get('text'),
            reference_audio_filename: formData.get('reference_audio_filename'),
            output_format: formData.get('output_format'),
            language: formData.get('language'),
            run_label: formData.get('run_label') || null,
            selected_chunk_indices: selectedChunkIndices
        };

        numberFields.forEach((fieldName) => {
            const rawValue = formData.get(fieldName);
            if (rawValue === null || rawValue === '') {
                payload[fieldName] = null;
                return;
            }

            if (fieldName === 'seed' || fieldName.endsWith('_ms') || fieldName.endsWith('_hz') || fieldName === 'chunk_size' || fieldName === 'max_reference_duration_sec' || fieldName === 'target_sample_rate') {
                payload[fieldName] = parseInt(rawValue, 10);
            } else {
                payload[fieldName] = parseFloat(rawValue);
            }
        });

        booleanFields.forEach((fieldName) => {
            payload[fieldName] = form.elements[fieldName].checked;
        });

        payload.text_normalization_model_id = formData.get('text_normalization_model_id') || null;

        return payload;
    }

    function buildPreviewPayload() {
        return {
            text: form.elements.text.value,
            split_text: form.elements.split_text.checked,
            chunk_size: parseInt(form.elements.chunk_size.value, 10)
        };
    }

    function renderWarnings(warnings) {
        warningsList.innerHTML = '';
        if (!warnings || !warnings.length) {
            warningsBlock.classList.add('hidden');
            return;
        }

        warnings.forEach((warning) => {
            const item = document.createElement('li');
            item.textContent = warning;
            warningsList.appendChild(item);
        });
        warningsBlock.classList.remove('hidden');
    }

    function renderSavedChunks(chunks) {
        chunksList.innerHTML = '';

        (chunks || []).forEach((chunk) => {
            const card = document.createElement('article');
            card.className = 'chunk-card';

            const header = document.createElement('div');
            header.className = 'chunk-card__header';

            const title = document.createElement('strong');
            title.textContent = `Chunk ${chunk.index}`;
            header.appendChild(title);
            card.appendChild(header);

            const text = document.createElement('p');
            text.textContent = chunk.text;
            card.appendChild(text);

            if (chunk.artifact) {
                const link = document.createElement('a');
                link.href = chunk.artifact.url;
                link.target = '_blank';
                link.rel = 'noreferrer';
                link.textContent = `${chunk.artifact.filename} (${chunk.artifact.duration_sec}s, ${chunk.artifact.byte_size} bytes)`;
                card.appendChild(link);

                const player = document.createElement('audio');
                player.controls = true;
                player.src = chunk.artifact.url;
                card.appendChild(player);
            } else {
                const unsaved = document.createElement('p');
                unsaved.className = 'muted';
                unsaved.textContent = 'Chunk audio was not saved for this run.';
                card.appendChild(unsaved);
            }

            chunksList.appendChild(card);
        });
    }

    function renderResult(result) {
        resultEmpty.classList.add('hidden');
        resultContent.classList.remove('hidden');

        runId.textContent = result.run_id;
        chunkCount.textContent = result.chunk_count === result.source_chunk_count
            ? String(result.chunk_count)
            : `${result.chunk_count} selected of ${result.source_chunk_count}`;
        outputDir.textContent = result.output_dir;

        manifestLink.href = result.manifest_url;
        manifestLink.classList.remove('hidden');

        renderWarnings(result.warnings);
        renderSavedChunks(result.chunks);
        resolvedSettings.textContent = JSON.stringify(result.resolved_settings, null, 2);

        if (result.final_audio) {
            finalAudioBlock.classList.remove('hidden');
            finalAudioPlayer.src = result.final_audio.url;
            finalAudioPlayer.load();
            finalAudioLink.href = result.final_audio.url;
            finalAudioLink.textContent = `Open ${result.final_audio.filename}`;
        } else {
            finalAudioBlock.classList.add('hidden');
            finalAudioPlayer.removeAttribute('src');
            finalAudioPlayer.load();
            finalAudioLink.removeAttribute('href');
            finalAudioLink.textContent = 'Final audio was not saved for this run';
        }
    }

    function clearPreview() {
        previewChunks = [];
        previewChunksList.innerHTML = '';
        previewCount.textContent = '0';
        previewSelectedCount.textContent = '0';
        previewSummary.textContent = 'Inspect the current text split before generation.';
        previewContent.classList.add('hidden');
        previewEmpty.classList.remove('hidden');
        updatePreviewSelectionState();
    }

    function createPreviewCard(chunk) {
        const card = document.createElement('article');
        card.className = 'chunk-card';

        const header = document.createElement('div');
        header.className = 'chunk-card__header';

        const titleGroup = document.createElement('div');

        const title = document.createElement('strong');
        title.textContent = `Chunk ${chunk.index}`;
        titleGroup.appendChild(title);

        const meta = document.createElement('div');
        meta.className = 'chunk-meta';
        meta.textContent = `${chunk.char_count} characters`;
        titleGroup.appendChild(meta);

        header.appendChild(titleGroup);

        const actions = document.createElement('div');
        actions.className = 'chunk-card__actions';

        const selectLabel = document.createElement('label');
        selectLabel.className = 'checkbox checkbox--compact';

        const checkbox = document.createElement('input');
        checkbox.type = 'checkbox';
        checkbox.checked = true;
        checkbox.value = String(chunk.index);
        checkbox.dataset.chunkCheckbox = 'true';
        checkbox.addEventListener('change', updatePreviewSelectionState);
        selectLabel.appendChild(checkbox);

        const selectText = document.createElement('span');
        selectText.textContent = 'Select';
        selectLabel.appendChild(selectText);
        actions.appendChild(selectLabel);

        const runButton = document.createElement('button');
        runButton.type = 'button';
        runButton.className = 'secondary chunk-action-btn';
        runButton.textContent = 'Run chunk';
        runButton.addEventListener('click', () => {
            startJob([chunk.index]);
        });
        actions.appendChild(runButton);

        header.appendChild(actions);
        card.appendChild(header);

        const text = document.createElement('p');
        text.textContent = chunk.text;
        card.appendChild(text);

        return card;
    }

    function renderPreview(chunks) {
        previewChunks = chunks || [];
        previewChunksList.innerHTML = '';

        if (!previewChunks.length) {
            clearPreview();
            return;
        }

        previewCount.textContent = String(previewChunks.length);
        previewSummary.textContent = `Split into ${previewChunks.length} chunk(s). Select any subset or run an individual chunk.`;
        previewEmpty.classList.add('hidden');
        previewContent.classList.remove('hidden');

        previewChunks.forEach((chunk) => {
            previewChunksList.appendChild(createPreviewCard(chunk));
        });

        updatePreviewSelectionState();
    }

    async function pollJob(jobIdToPoll) {
        if (!jobIdToPoll || activeJobId !== jobIdToPoll) {
            return;
        }

        try {
            const response = await fetch(`/api/jobs/${jobIdToPoll}`);
            const job = await parseJsonResponse(response);
            if (!response.ok) {
                throw new Error(job.detail || 'Failed to load job status.');
            }

            renderJobStatus(job);

            if (job.status === 'completed') {
                activeJobId = null;
                setInteractiveState(false);
                if (job.result) {
                    renderResult(job.result);
                }
                setStatus(job.message || `Saved run ${jobIdToPoll}`);
                stopJobPolling();
                return;
            }

            if (job.status === 'failed') {
                activeJobId = null;
                setInteractiveState(false);
                setStatus(job.error || job.message || 'Generation failed.', true);
                stopJobPolling();
                return;
            }

            jobPollHandle = window.setTimeout(() => {
                pollJob(jobIdToPoll);
            }, 1000);
        } catch (error) {
            activeJobId = null;
            setInteractiveState(false);
            setStatus(error.message, true);
            stopJobPolling();
        }
    }

    async function startJob(selectedChunkIndices = []) {
        if (!isModelReady) {
            setStatus('Model is still warming up. Wait until the status pill shows ready.', true);
            return;
        }

        if (!form.elements.text.value.trim()) {
            setStatus('Text is required.', true);
            return;
        }

        const payload = buildPayload(selectedChunkIndices);
        const scopeLabel = selectedChunkIndices.length ? `${selectedChunkIndices.length} selected chunk(s)` : 'all chunks';

        setInteractiveState(true);
        stopJobPolling();
        setStatus(`Queueing ${scopeLabel}...`);

        try {
            const response = await fetch('/api/jobs', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });
            const job = await parseJsonResponse(response);

            if (!response.ok) {
                throw new Error(job.detail || 'Failed to create generation job.');
            }

            activeJobId = job.job_id;
            renderJobStatus({
                job_id: job.job_id,
                status: 'queued',
                progress_completed: 0,
                progress_total: 0,
                current_chunk_index: null,
                message: `Queued ${scopeLabel}.`
            });
            setStatus(`Queued job ${job.job_id}`);
            pollJob(job.job_id);
        } catch (error) {
            activeJobId = null;
            setInteractiveState(false);
            setStatus(error.message, true);
        }
    }

    async function loadChunkPreview() {
        const previewPayload = buildPreviewPayload();
        if (!previewPayload.text || !previewPayload.text.trim()) {
            setStatus('Text is required to preview chunks.', true);
            return;
        }

        previewButton.disabled = true;
        setStatus('Building chunk preview...');

        try {
            const response = await fetch('/api/chunks/preview', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(previewPayload)
            });
            const result = await parseJsonResponse(response);

            if (!response.ok) {
                throw new Error(result.detail || 'Failed to build chunk preview.');
            }

            renderPreview(result.chunks);
            setStatus(`Prepared ${result.chunk_count} chunk(s).`);
        } catch (error) {
            clearPreview();
            setStatus(error.message, true);
        } finally {
            previewButton.disabled = false;
        }
    }

    function invalidatePreview() {
        if (previewChunks.length && !isJobRunning) {
            clearPreview();
        }
    }

    function handleRunSelected() {
        const selectedChunkIndices = getSelectedChunkIndices();
        if (!selectedChunkIndices.length) {
            setStatus('Select at least one preview chunk to run.', true);
            return;
        }
        startJob(selectedChunkIndices);
    }

    function submitForm(event) {
        event.preventDefault();
        startJob([]);
    }

    refreshReferenceButton.addEventListener('click', loadReferenceAudioFiles);
    uploadReferenceButton.addEventListener('click', uploadReferenceAudio);
    previewButton.addEventListener('click', loadChunkPreview);
    runSelectedButton.addEventListener('click', handleRunSelected);
    form.addEventListener('submit', submitForm);

    form.elements.text.addEventListener('input', invalidatePreview);
    form.elements.chunk_size.addEventListener('input', invalidatePreview);
    form.elements.split_text.addEventListener('change', invalidatePreview);

    resetJobStatus();
    clearPreview();
    updateGenerationAvailability();
    loadModelInfo();
    loadReferenceAudioFiles();
});
