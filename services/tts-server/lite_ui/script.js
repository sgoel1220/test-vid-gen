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

    // -----------------------------------------------------------------------
    // Image Generation
    // -----------------------------------------------------------------------
    const imgText = document.getElementById('img-text');
    const imgStyle = document.getElementById('img-style');
    const imgNumScenes = document.getElementById('img-num-scenes');
    const imgWidth = document.getElementById('img-width');
    const imgHeight = document.getElementById('img-height');
    const imgSteps = document.getElementById('img-steps');
    const imgGuidance = document.getElementById('img-guidance');
    const imgSeed = document.getElementById('img-seed');
    const imgLabel = document.getElementById('img-label');
    const imgUnloadTts = document.getElementById('img-unload-tts');
    const imgPreviewBtn = document.getElementById('img-preview-btn');
    const imgGenerateBtn = document.getElementById('img-generate-btn');
    const imgRequestStatus = document.getElementById('img-request-status');
    const imgStatusPill = document.getElementById('img-status');

    const imgPromptsEmpty = document.getElementById('img-prompts-empty');
    const imgPromptsContent = document.getElementById('img-prompts-content');
    const imgPromptsList = document.getElementById('img-prompts-list');
    const imgPromptCount = document.getElementById('img-prompt-count');

    const imgGalleryEmpty = document.getElementById('img-gallery-empty');
    const imgGalleryContent = document.getElementById('img-gallery-content');
    const imgGalleryGrid = document.getElementById('img-gallery-grid');
    const imgRunId = document.getElementById('img-run-id');
    const imgCount = document.getElementById('img-count');
    const imgManifestLink = document.getElementById('img-manifest-link');
    const imgWarningsBlock = document.getElementById('img-warnings-block');
    const imgWarningsList = document.getElementById('img-warnings-list');

    let imgJobPollHandle = null;
    let activeImgJobId = null;

    function setImgStatus(message, isError = false) {
        imgRequestStatus.textContent = message;
        imgRequestStatus.style.color = isError ? '#ff9b9b' : '';
    }

    function setImgPill(status, text) {
        imgStatusPill.dataset.status = status;
        imgStatusPill.textContent = text || status.charAt(0).toUpperCase() + status.slice(1);
    }

    function getImgStoryText() {
        const t = imgText.value.trim();
        if (t) return t;
        return form.elements.text.value.trim();
    }

    function buildImgPayload() {
        return {
            story_text: getImgStoryText(),
            num_scenes: parseInt(imgNumScenes.value, 10),
            style: imgStyle.value,
            width: parseInt(imgWidth.value, 10),
            height: parseInt(imgHeight.value, 10),
            steps: parseInt(imgSteps.value, 10),
            guidance_scale: parseFloat(imgGuidance.value),
            seed: parseInt(imgSeed.value, 10),
            run_label: imgLabel.value || null,
            unload_tts_for_vram: imgUnloadTts.checked,
        };
    }

    function renderPromptCard(scene) {
        const card = document.createElement('article');
        card.className = 'prompt-card';

        const header = document.createElement('div');
        header.className = 'prompt-card__header';
        const title = document.createElement('strong');
        title.textContent = `Scene ${scene.scene_index + 1}`;
        header.appendChild(title);
        card.appendChild(header);

        if (scene.text_segment) {
            const segment = document.createElement('div');
            segment.className = 'prompt-card__segment';
            segment.textContent = scene.text_segment;
            card.appendChild(segment);
        }

        const positive = document.createElement('div');
        positive.className = 'prompt-card__positive';
        positive.textContent = scene.prompt;
        card.appendChild(positive);

        const negative = document.createElement('div');
        negative.className = 'prompt-card__negative';
        negative.textContent = scene.negative_prompt;
        card.appendChild(negative);

        return card;
    }

    function renderScenePrompts(scenes) {
        imgPromptsList.innerHTML = '';
        if (!scenes || !scenes.length) {
            imgPromptsEmpty.classList.remove('hidden');
            imgPromptsContent.classList.add('hidden');
            imgPromptCount.textContent = 'No prompts extracted.';
            return;
        }
        imgPromptsEmpty.classList.add('hidden');
        imgPromptsContent.classList.remove('hidden');
        imgPromptCount.textContent = `${scenes.length} scene(s)`;
        scenes.forEach((scene) => {
            imgPromptsList.appendChild(renderPromptCard(scene));
        });
    }

    function renderImageCard(img) {
        const card = document.createElement('article');
        card.className = 'image-card';

        const imgEl = document.createElement('img');
        imgEl.src = img.url;
        imgEl.alt = img.prompt_used.slice(0, 100);
        imgEl.loading = 'lazy';
        imgEl.addEventListener('click', () => openLightbox(img.url));
        card.appendChild(imgEl);

        const info = document.createElement('div');
        info.className = 'image-card__info';

        const title = document.createElement('strong');
        title.textContent = img.filename;
        info.appendChild(title);

        const prompt = document.createElement('div');
        prompt.className = 'image-card__prompt';
        prompt.textContent = img.prompt_used;
        prompt.title = img.prompt_used;
        info.appendChild(prompt);

        const meta = document.createElement('div');
        meta.className = 'image-card__meta';
        meta.textContent = `${img.width}x${img.height} · seed ${img.seed_used}`;
        info.appendChild(meta);

        const link = document.createElement('a');
        link.href = img.url;
        link.target = '_blank';
        link.rel = 'noreferrer';
        link.textContent = 'Open full size';
        link.style.fontSize = '0.85rem';
        info.appendChild(link);

        card.appendChild(info);
        return card;
    }

    function renderImageGallery(result) {
        imgGalleryGrid.innerHTML = '';

        if (!result || !result.images || !result.images.length) {
            imgGalleryEmpty.classList.remove('hidden');
            imgGalleryContent.classList.add('hidden');
            return;
        }

        imgGalleryEmpty.classList.add('hidden');
        imgGalleryContent.classList.remove('hidden');
        imgRunId.textContent = result.run_id;
        imgCount.textContent = String(result.images.length);

        if (result.manifest_url) {
            imgManifestLink.href = result.manifest_url;
            imgManifestLink.classList.remove('hidden');
        }

        // Warnings
        imgWarningsList.innerHTML = '';
        if (result.warnings && result.warnings.length) {
            result.warnings.forEach((w) => {
                const li = document.createElement('li');
                li.textContent = w;
                imgWarningsList.appendChild(li);
            });
            imgWarningsBlock.classList.remove('hidden');
        } else {
            imgWarningsBlock.classList.add('hidden');
        }

        // Also show the prompts used
        if (result.scenes) {
            renderScenePrompts(result.scenes);
        }

        result.images.forEach((img) => {
            imgGalleryGrid.appendChild(renderImageCard(img));
        });
    }

    function openLightbox(imgUrl) {
        const overlay = document.createElement('div');
        overlay.className = 'lightbox-overlay';
        const img = document.createElement('img');
        img.src = imgUrl;
        overlay.appendChild(img);
        overlay.addEventListener('click', () => overlay.remove());
        document.addEventListener('keydown', function handler(e) {
            if (e.key === 'Escape') {
                overlay.remove();
                document.removeEventListener('keydown', handler);
            }
        });
        document.body.appendChild(overlay);
    }

    function stopImgJobPolling() {
        if (imgJobPollHandle !== null) {
            window.clearTimeout(imgJobPollHandle);
            imgJobPollHandle = null;
        }
    }

    function setImgInteractive(disabled) {
        imgPreviewBtn.disabled = disabled;
        imgGenerateBtn.disabled = disabled;
    }

    async function pollImgJob(jobIdToPoll) {
        if (!jobIdToPoll || activeImgJobId !== jobIdToPoll) return;

        try {
            const response = await fetch(`/api/images/jobs/${jobIdToPoll}`);
            const job = await parseJsonResponse(response);
            if (!response.ok) throw new Error(job.detail || 'Failed to load image job status.');

            const completed = Number(job.progress_completed || 0);
            const total = Number(job.progress_total || 0);
            setImgPill(job.status, `${formatJobStatus(job.status)} ${total > 0 ? completed + '/' + total : ''}`);
            setImgStatus(job.message || job.error || '');

            if (job.status === 'completed') {
                activeImgJobId = null;
                setImgInteractive(false);
                setImgPill('completed', 'Completed');
                if (job.result) renderImageGallery(job.result);
                setImgStatus(`Done — ${(job.result && job.result.images && job.result.images.length) || 0} image(s) generated.`);
                stopImgJobPolling();
                return;
            }

            if (job.status === 'failed') {
                activeImgJobId = null;
                setImgInteractive(false);
                setImgPill('failed', 'Failed');
                setImgStatus(job.error || job.message || 'Image generation failed.', true);
                stopImgJobPolling();
                return;
            }

            imgJobPollHandle = window.setTimeout(() => pollImgJob(jobIdToPoll), 1500);
        } catch (error) {
            activeImgJobId = null;
            setImgInteractive(false);
            setImgPill('failed', 'Error');
            setImgStatus(error.message, true);
            stopImgJobPolling();
        }
    }

    async function handleImgPreview() {
        const storyText = getImgStoryText();
        if (!storyText) {
            setImgStatus('Enter story text (or fill the TTS text field above).', true);
            return;
        }

        imgPreviewBtn.disabled = true;
        setImgStatus('Extracting scene prompts...');
        setImgPill('running', 'Extracting...');

        try {
            const response = await fetch('/api/images/prompts/preview', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    story_text: storyText,
                    num_scenes: parseInt(imgNumScenes.value, 10),
                    style: imgStyle.value,
                }),
            });
            const scenes = await parseJsonResponse(response);
            if (!response.ok) throw new Error(scenes.detail || 'Failed to extract prompts.');

            renderScenePrompts(Array.isArray(scenes) ? scenes : []);
            setImgStatus(`Extracted ${Array.isArray(scenes) ? scenes.length : 0} scene prompt(s).`);
            setImgPill('ready', 'Ready');
        } catch (error) {
            setImgStatus(error.message, true);
            setImgPill('failed', 'Error');
        } finally {
            imgPreviewBtn.disabled = false;
        }
    }

    async function handleImgGenerate() {
        const storyText = getImgStoryText();
        if (!storyText) {
            setImgStatus('Enter story text (or fill the TTS text field above).', true);
            return;
        }

        setImgInteractive(true);
        stopImgJobPolling();
        setImgStatus('Queueing image generation...');
        setImgPill('queued', 'Queued');

        try {
            const payload = buildImgPayload();
            const response = await fetch('/api/images/jobs', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
            });
            const job = await parseJsonResponse(response);
            if (!response.ok) throw new Error(job.detail || 'Failed to create image job.');

            activeImgJobId = job.job_id;
            setImgStatus(`Job ${job.job_id} queued.`);
            setImgPill('queued', 'Queued');
            pollImgJob(job.job_id);
        } catch (error) {
            activeImgJobId = null;
            setImgInteractive(false);
            setImgStatus(error.message, true);
            setImgPill('failed', 'Error');
        }
    }

    imgPreviewBtn.addEventListener('click', handleImgPreview);
    imgGenerateBtn.addEventListener('click', handleImgGenerate);

    // History panel
    const tabGenerateBtn = document.getElementById('tab-generate');
    const tabHistoryBtn = document.getElementById('tab-history');
    const generateTab = document.getElementById('generate-tab');
    const historyTab = document.getElementById('history-tab');
    const historyEmpty = document.getElementById('history-empty');
    const historyUnavailable = document.getElementById('history-unavailable');
    const historyContent = document.getElementById('history-content');
    const historyRunsList = document.getElementById('history-runs-list');
    const historyPrevBtn = document.getElementById('history-prev-btn');
    const historyNextBtn = document.getElementById('history-next-btn');
    const historyPageInfo = document.getElementById('history-page-info');
    const historyDetailPanel = document.getElementById('history-detail-panel');
    const historyDetailCloseBtn = document.getElementById('history-detail-close-btn');
    const historyDetailRunId = document.getElementById('history-detail-run-id');
    const historyDetailLabel = document.getElementById('history-detail-label');
    const historyDetailStatus = document.getElementById('history-detail-status');
    const historyDetailCreated = document.getElementById('history-detail-created');
    const historyDetailVoice = document.getElementById('history-detail-voice');
    const historyDetailChunkCount = document.getElementById('history-detail-chunk-count');
    const historyDetailFinalAudioBlock = document.getElementById('history-detail-final-audio-block');
    const historyDetailFinalAudio = document.getElementById('history-detail-final-audio');
    const historyDetailChunksBlock = document.getElementById('history-detail-chunks-block');
    const historyDetailChunksList = document.getElementById('history-detail-chunks-list');
    const historyReloadBtn = document.getElementById('history-reload-btn');

    let historyOffset = 0;
    let historyLimit = 50;
    let currentDetailRun = null;

    function switchTab(tabName) {
        if (tabName === 'generate') {
            tabGenerateBtn.classList.add('active');
            tabHistoryBtn.classList.remove('active');
            generateTab.classList.add('active');
            historyTab.classList.remove('hidden');
            historyTab.classList.remove('active');
        } else if (tabName === 'history') {
            tabHistoryBtn.classList.add('active');
            tabGenerateBtn.classList.remove('active');
            historyTab.classList.add('active');
            historyTab.classList.remove('hidden');
            generateTab.classList.remove('active');
            loadHistory();
        }
    }

    tabGenerateBtn.addEventListener('click', () => switchTab('generate'));
    tabHistoryBtn.addEventListener('click', () => switchTab('history'));

    async function loadHistory() {
        try {
            const response = await fetch(`/api/history?limit=${historyLimit}&offset=${historyOffset}`);

            if (response.status === 503) {
                historyEmpty.classList.add('hidden');
                historyUnavailable.classList.remove('hidden');
                historyContent.classList.add('hidden');
                return;
            }

            const runs = await parseJsonResponse(response);
            if (!response.ok) throw new Error(runs.detail || 'Failed to load history.');

            historyUnavailable.classList.add('hidden');

            if (!runs || !runs.length) {
                historyEmpty.classList.remove('hidden');
                historyContent.classList.add('hidden');
                return;
            }

            historyEmpty.classList.add('hidden');
            historyContent.classList.remove('hidden');
            renderHistoryRuns(runs);

            const page = Math.floor(historyOffset / historyLimit) + 1;
            historyPageInfo.textContent = `Page ${page}`;
            historyPrevBtn.disabled = historyOffset === 0;
            historyNextBtn.disabled = runs.length < historyLimit;
        } catch (error) {
            historyEmpty.classList.add('hidden');
            historyUnavailable.classList.remove('hidden');
            historyContent.classList.add('hidden');
        }
    }

    function renderHistoryRuns(runs) {
        historyRunsList.innerHTML = '';
        runs.forEach((run) => {
            const card = createHistoryRunCard(run);
            historyRunsList.appendChild(card);
        });
    }

    function createHistoryRunCard(run) {
        const card = document.createElement('div');
        card.className = 'history-run-card';
        card.addEventListener('click', () => loadRunDetail(run.run_id));

        const header = document.createElement('div');
        header.className = 'history-run-card__header';

        const title = document.createElement('div');
        title.className = 'history-run-card__title';
        title.textContent = run.run_label || run.run_id;
        header.appendChild(title);

        const statusPill = document.createElement('span');
        statusPill.className = 'pill';
        statusPill.textContent = formatJobStatus(run.status || 'unknown');
        statusPill.dataset.status = run.status || 'unknown';
        header.appendChild(statusPill);

        card.appendChild(header);

        const meta = document.createElement('div');
        meta.className = 'history-run-card__meta';

        const created = document.createElement('div');
        created.className = 'history-run-card__meta-item';
        created.innerHTML = `<strong>Created:</strong> ${formatTimestamp(run.created_at)}`;
        meta.appendChild(created);

        const chunks = document.createElement('div');
        chunks.className = 'history-run-card__meta-item';
        chunks.innerHTML = `<strong>Chunks:</strong> ${run.chunk_count || 0}`;
        meta.appendChild(chunks);

        if (run.duration_sec) {
            const duration = document.createElement('div');
            duration.className = 'history-run-card__meta-item';
            duration.innerHTML = `<strong>Duration:</strong> ${run.duration_sec.toFixed(1)}s`;
            meta.appendChild(duration);
        }

        card.appendChild(meta);
        return card;
    }

    function formatTimestamp(timestamp) {
        if (!timestamp) return '-';
        try {
            const date = new Date(timestamp);
            return date.toLocaleString();
        } catch {
            return timestamp;
        }
    }

    async function loadRunDetail(runId) {
        try {
            const response = await fetch(`/api/history/${runId}`);
            const run = await parseJsonResponse(response);
            if (!response.ok) throw new Error(run.detail || 'Failed to load run detail.');

            currentDetailRun = run;
            renderRunDetail(run);
            historyDetailPanel.classList.remove('hidden');
        } catch (error) {
            alert('Failed to load run detail: ' + error.message);
        }
    }

    function renderRunDetail(run) {
        historyDetailRunId.textContent = run.run_id || '-';
        historyDetailLabel.textContent = run.run_label || '-';
        historyDetailStatus.textContent = formatJobStatus(run.status || 'unknown');
        historyDetailStatus.dataset.status = run.status || 'unknown';
        historyDetailCreated.textContent = formatTimestamp(run.created_at);
        historyDetailVoice.textContent = run.voice_filename || '-';
        historyDetailChunkCount.textContent = run.chunks ? run.chunks.length : 0;

        // Final audio
        if (run.final_audio_id) {
            historyDetailFinalAudio.src = `/api/history/audio/${run.final_audio_id}`;
            historyDetailFinalAudioBlock.classList.remove('hidden');
        } else {
            historyDetailFinalAudioBlock.classList.add('hidden');
        }

        // Chunks
        if (run.chunks && run.chunks.length) {
            historyDetailChunksList.innerHTML = '';
            run.chunks.forEach((chunk) => {
                const chunkCard = createHistoryChunkCard(chunk);
                historyDetailChunksList.appendChild(chunkCard);
            });
            historyDetailChunksBlock.classList.remove('hidden');
        } else {
            historyDetailChunksBlock.classList.add('hidden');
        }
    }

    function createHistoryChunkCard(chunk) {
        const card = document.createElement('div');
        card.className = 'chunk-card';

        const header = document.createElement('div');
        header.className = 'chunk-card__header';
        header.innerHTML = `<strong>Chunk ${chunk.chunk_index}</strong>`;
        card.appendChild(header);

        if (chunk.text) {
            const text = document.createElement('p');
            text.textContent = chunk.text;
            card.appendChild(text);
        }

        if (chunk.audio_blob_id) {
            const audio = document.createElement('audio');
            audio.controls = true;
            audio.src = `/api/history/audio/${chunk.audio_blob_id}`;
            card.appendChild(audio);
        }

        return card;
    }

    function reloadFromRun() {
        if (!currentDetailRun || !currentDetailRun.settings) {
            alert('No settings available to reload.');
            return;
        }

        const settings = currentDetailRun.settings;

        // Switch to generate tab
        switchTab('generate');

        // Populate form fields from settings
        if (settings.target_sample_rate) document.getElementById('target-sample-rate').value = settings.target_sample_rate;
        if (settings.output_format) document.getElementById('output-format').value = settings.output_format;
        if (settings.chunk_size) document.getElementById('chunk-size').value = settings.chunk_size;
        if (settings.temperature !== undefined) document.getElementById('temperature').value = settings.temperature;
        if (settings.exaggeration !== undefined) document.getElementById('exaggeration').value = settings.exaggeration;
        if (settings.cfg_weight !== undefined) document.getElementById('cfg-weight').value = settings.cfg_weight;
        if (settings.seed !== undefined) document.getElementById('seed').value = settings.seed;
        if (settings.speed_factor !== undefined) document.getElementById('speed-factor').value = settings.speed_factor;
        if (settings.language) document.getElementById('language').value = settings.language;
        if (settings.sentence_pause_ms !== undefined) document.getElementById('sentence-pause-ms').value = settings.sentence_pause_ms;
        if (settings.crossfade_ms !== undefined) document.getElementById('crossfade-ms').value = settings.crossfade_ms;
        if (settings.safety_fade_ms !== undefined) document.getElementById('safety-fade-ms').value = settings.safety_fade_ms;

        // Checkboxes
        if (settings.split_text !== undefined) document.getElementById('split-text').checked = settings.split_text;
        if (settings.enable_smart_stitching !== undefined) document.getElementById('enable-smart-stitching').checked = settings.enable_smart_stitching;
        if (settings.enable_dc_removal !== undefined) document.getElementById('enable-dc-removal').checked = settings.enable_dc_removal;

        alert('Settings reloaded from run ' + currentDetailRun.run_label);
    }

    historyDetailCloseBtn.addEventListener('click', () => {
        historyDetailPanel.classList.add('hidden');
        currentDetailRun = null;
    });

    historyReloadBtn.addEventListener('click', reloadFromRun);

    historyPrevBtn.addEventListener('click', () => {
        if (historyOffset >= historyLimit) {
            historyOffset -= historyLimit;
            loadHistory();
        }
    });

    historyNextBtn.addEventListener('click', () => {
        historyOffset += historyLimit;
        loadHistory();
    });

    resetJobStatus();
    clearPreview();
    updateGenerationAvailability();
    loadModelInfo();
    loadReferenceAudioFiles();
});
