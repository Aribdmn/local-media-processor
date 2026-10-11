// Local Media Processor - front-end logic (vanilla JS, zero external dependencies).

const POLL_INTERVAL_MS = 500;
const MAX_POLL_ATTEMPTS = 2400; // ~20 minutes safety net for long encodes

const ACTIVE_TAB_CLASSES = "px-4 py-2 text-sm font-medium border-b-2 -mb-px border-indigo-400 text-white";
const INACTIVE_TAB_CLASSES = "px-4 py-2 text-sm font-medium border-b-2 -mb-px border-transparent text-gray-400 hover:text-gray-200";

const ACCEPTED_FILE_PATTERNS = {
  image: /\.(png|jpe?g|webp|bmp|gif|tiff?)$/i,
  video: /\.(mp4|mov|mkv|avi|webm|m4v|wmv|flv|gif)$/i,
};

const DROP_ZONE_CONFIGURATION = {
  image: { zoneId: "imageDropZone", inputId: "imageFileInput", multiple: true },
  video: { zoneId: "videoDropZone", inputId: "videoFileInput" },
};

const applicationState = { imageFiles: [], videoFile: null, videoToolsEnabled: true };
const IMAGE_LIST_DISPLAY_LIMIT = 50; // bulk lists cap at 50 names, then show "+N more"
let activeVideoPollTimer = null;
let imagePreviewObjectUrl = null;

document.addEventListener("DOMContentLoaded", () => {
  bindTabNavigation();
  bindDropZone("image");
  bindDropZone("video");
  bindOperationSelectors();
  bindActionButtons();
  bindQualitySlider();
  bindResizeControls();
  refreshEngineStatus();
});

// ---------------------------------------------------------------------------
// Tab navigation
// ---------------------------------------------------------------------------

function bindTabNavigation() {
  document.getElementById("tabImageButton").addEventListener("click", () => switchToTab("image"));
  document.getElementById("tabVideoButton").addEventListener("click", () => switchToTab("video"));
}

function switchToTab(tabName) {
  const showImagePanel = tabName === "image";
  document.getElementById("imagePanel").classList.toggle("hidden", !showImagePanel);
  document.getElementById("videoPanel").classList.toggle("hidden", showImagePanel);
  const imageTab = document.getElementById("tabImageButton");
  const videoTab = document.getElementById("tabVideoButton");
  imageTab.className = showImagePanel ? ACTIVE_TAB_CLASSES : INACTIVE_TAB_CLASSES;
  videoTab.className = showImagePanel ? INACTIVE_TAB_CLASSES : ACTIVE_TAB_CLASSES;
}

// ---------------------------------------------------------------------------
// Engine health badge (mirrors the server's startup pre-flight)
// ---------------------------------------------------------------------------

async function refreshEngineStatus() {
  const badge = document.getElementById("engineBadge");
  const warningBanner = document.getElementById("healthWarning");
  try {
    const response = await fetch("/api/health");
    const health = await response.json();
    const noticeMessages = [];

    if (health.ffmpeg.available) {
      badge.textContent = `FFmpeg ready (${health.ffmpeg.source})`;
      badge.classList.remove("bg-gray-700", "text-gray-300");
      badge.classList.add("bg-green-700");
    } else {
      badge.textContent = "FFmpeg missing";
      badge.classList.remove("bg-gray-700", "text-gray-300");
      badge.classList.add("bg-red-600");
      noticeMessages.push("FFmpeg was not found - video tools are disabled. Install FFmpeg (or the 'imageio-ffmpeg' package) and restart the app.");
      applicationState.videoToolsEnabled = false;
      document.getElementById("videoProcessButton").disabled = true;
    }

    if (!health.vtracer.available) {
      const svgOption = document.querySelector('#imageOperation option[value="svg"]');
      if (svgOption) {
        svgOption.disabled = true;
        svgOption.textContent = "Trace bitmap to .svg (vtracer unavailable)";
      }
      noticeMessages.push("vtracer is unavailable - SVG tracing is disabled.");
    }

    if (noticeMessages.length > 0) {
      warningBanner.textContent = noticeMessages.join(" ");
      warningBanner.classList.remove("hidden");
    }
  } catch (healthError) {
    badge.textContent = "Server unreachable";
    badge.classList.remove("bg-gray-700", "text-gray-300");
    badge.classList.add("bg-red-600");
  }
}

// ---------------------------------------------------------------------------
// Drag & drop zones
// ---------------------------------------------------------------------------

function bindDropZone(kind) {
  const configuration = DROP_ZONE_CONFIGURATION[kind];
  const zone = document.getElementById(configuration.zoneId);
  const input = document.getElementById(configuration.inputId);

  zone.addEventListener("click", () => input.click());
  input.addEventListener("change", () => {
    if (input.files.length === 0) {
      return;
    }
    if (configuration.multiple) {
      acceptSelectedImages(Array.from(input.files));
    } else {
      acceptSelectedVideoFile(input.files[0]);
    }
    // Clearing the value allows picking the same file again later.
    input.value = "";
  });

  ["dragenter", "dragover"].forEach((eventName) => {
    zone.addEventListener(eventName, (event) => {
      event.preventDefault();
      zone.classList.add("drag-over");
    });
  });
  ["dragleave", "drop"].forEach((eventName) => {
    zone.addEventListener(eventName, (event) => {
      event.preventDefault();
      zone.classList.remove("drag-over");
    });
  });
  zone.addEventListener("drop", (event) => {
    const droppedFiles = Array.from(event.dataTransfer.files);
    if (droppedFiles.length === 0) {
      return;
    }
    if (configuration.multiple) {
      acceptSelectedImages(droppedFiles);
    } else {
      acceptSelectedVideoFile(droppedFiles[0]);
    }
  });
}

function acceptSelectedImages(candidateFiles) {
  // Mixed drops are allowed - anything that is not an image is skipped.
  const validImages = candidateFiles.filter((file) => isAcceptableFile("image", file));
  const skippedCount = candidateFiles.length - validImages.length;
  if (skippedCount > 0) {
    showAlert("image", `Skipped ${skippedCount} file(s) that are not images.`);
  } else {
    clearAlert("image");
  }
  if (validImages.length === 0) {
    return; // nothing usable - keep the drop zone visible
  }
  applicationState.imageFiles = validImages;
  hideResultPanel("image");
  hideProgress("image");
  // Swap the drop zone for the review card so the UI shows image info only.
  document.getElementById("imageDropZone").classList.add("hidden");
  document.getElementById("imageSelectionCard").classList.remove("hidden");
  renderImageSelectionCard();
  updateImageConvertButton();
}

function resetImageSelection() {
  applicationState.imageFiles = [];
  releaseImagePreviewObjectUrl();
  document.getElementById("imageSelectionCard").classList.add("hidden");
  document.getElementById("imageDropZone").classList.remove("hidden");
  hideResultPanel("image");
  hideProgress("image");
  clearAlert("image");
  updateImageConvertButton();
}

function updateImageConvertButton() {
  const convertButton = document.getElementById("imageConvertButton");
  const fileCount = applicationState.imageFiles.length;
  convertButton.disabled = fileCount === 0;
  convertButton.textContent = fileCount === 1 ? "Convert image" : `Convert ${fileCount} images`;
}

function acceptSelectedVideoFile(selectedFile) {
  if (!isAcceptableFile("video", selectedFile)) {
    showAlert("video", `Unsupported file type: ${selectedFile.name}`);
    return;
  }
  applicationState.videoFile = selectedFile;
  document.getElementById("videoFileMeta").textContent =
    `${selectedFile.name} - ${formatFileSize(selectedFile.size)}`;
  document.getElementById("videoProcessButton").disabled = false;
  clearAlert("video");
  hideResultPanel("video");
}

function renderImageSelectionCard() {
  releaseImagePreviewObjectUrl();
  const thumbnailElement = document.getElementById("imagePreviewThumbnail");
  const titleElement = document.getElementById("imageSelectionTitle");
  const subtitleElement = document.getElementById("imageSelectionSubtitle");
  const nameListElement = document.getElementById("imageFileNameList");
  nameListElement.textContent = "";
  nameListElement.classList.add("hidden");

  if (applicationState.imageFiles.length === 1) {
    // Single image: show a live preview plus pixel dimensions, nothing else.
    const singleFile = applicationState.imageFiles[0];
    titleElement.textContent = singleFile.name;
    subtitleElement.textContent = formatFileSize(singleFile.size);
    showSingleImagePreview(singleFile, thumbnailElement, subtitleElement);
    return;
  }

  // Bulk: show the count and total size, plus a capped list of file names.
  const selectedFiles = applicationState.imageFiles;
  const totalBytes = selectedFiles.reduce((byteSum, file) => byteSum + file.size, 0);
  titleElement.textContent = `${selectedFiles.length} images selected`;
  subtitleElement.textContent = `Total size: ${formatFileSize(totalBytes)}`;
  thumbnailElement.classList.add("hidden");
  thumbnailElement.removeAttribute("src");

  const displayLimit = Math.min(selectedFiles.length, IMAGE_LIST_DISPLAY_LIMIT);
  for (let fileIndex = 0; fileIndex < displayLimit; fileIndex += 1) {
    appendImageFileNameEntry(nameListElement, selectedFiles[fileIndex]);
  }
  if (selectedFiles.length > displayLimit) {
    appendImageFileNameEntry(nameListElement, null, selectedFiles.length - displayLimit);
  }
  nameListElement.classList.remove("hidden");
}

function showSingleImagePreview(imageFile, thumbnailElement, subtitleElement) {
  // An object URL lets the browser read the image locally - nothing is uploaded.
  imagePreviewObjectUrl = URL.createObjectURL(imageFile);
  const previewLoader = new Image();
  previewLoader.onload = () => {
    thumbnailElement.src = imagePreviewObjectUrl;
    thumbnailElement.classList.remove("hidden");
    subtitleElement.textContent =
      `${formatFileSize(imageFile.size)} - ${previewLoader.naturalWidth} x ${previewLoader.naturalHeight}px`;
  };
  previewLoader.onerror = () => thumbnailElement.classList.add("hidden");
  previewLoader.src = imagePreviewObjectUrl;
}

function releaseImagePreviewObjectUrl() {
  if (imagePreviewObjectUrl) {
    URL.revokeObjectURL(imagePreviewObjectUrl);
    imagePreviewObjectUrl = null;
  }
}

function appendImageFileNameEntry(nameListElement, imageFile, remainingCount) {
  const entryElement = document.createElement("li");
  entryElement.className = "flex items-center justify-between gap-3 text-xs";
  if (imageFile) {
    const nameSpan = document.createElement("span");
    nameSpan.className = "truncate text-gray-300";
    nameSpan.textContent = imageFile.name;
    const sizeSpan = document.createElement("span");
    sizeSpan.className = "ml-3 whitespace-nowrap text-gray-500";
    sizeSpan.textContent = formatFileSize(imageFile.size);
    entryElement.appendChild(nameSpan);
    entryElement.appendChild(sizeSpan);
  } else {
    const moreSpan = document.createElement("span");
    moreSpan.className = "text-indigo-300";
    moreSpan.textContent = `... and ${remainingCount} more`;
    entryElement.appendChild(moreSpan);
  }
  nameListElement.appendChild(entryElement);
}

function isAcceptableFile(kind, file) {
  const expectedPrefix = kind === "image" ? "image/" : "video/";
  if (file.type && file.type.startsWith(expectedPrefix)) {
    return true;
  }
  return ACCEPTED_FILE_PATTERNS[kind].test(file.name);
}

// ---------------------------------------------------------------------------
// Option visibility, sliders & action buttons
// ---------------------------------------------------------------------------

function bindOperationSelectors() {
  document.getElementById("imageOperation").addEventListener("change", updateImageOptionVisibility);
  document.getElementById("videoOperation").addEventListener("change", updateVideoOptionVisibility);
}

function updateImageOptionVisibility() {
  const selectedOperation = document.getElementById("imageOperation").value;
  const isConvertOperation = selectedOperation === "convert";
  document.getElementById("imageFormatGroup").classList.toggle("hidden", !isConvertOperation);
  document.getElementById("imageResizeGroup").classList.toggle("hidden", !isConvertOperation);
}

function updateVideoOptionVisibility() {
  const selectedOperation = document.getElementById("videoOperation").value;
  document.getElementById("videoCompressGroup").classList.toggle("hidden", selectedOperation !== "compress");
  document.getElementById("videoGifGroup").classList.toggle("hidden", selectedOperation !== "gif");
  document.getElementById("videoTrimGroup").classList.toggle("hidden", selectedOperation !== "trim");
}

function bindActionButtons() {
  document.getElementById("imageConvertButton").addEventListener("click", submitImageConversion);
  document.getElementById("imageChangeButton").addEventListener("click", resetImageSelection);
  document.getElementById("imageDownloadAllButton").addEventListener("click", downloadAllImageResults);
  document.getElementById("videoProcessButton").addEventListener("click", submitVideoProcessing);
}

function bindQualitySlider() {
  const qualityInput = document.getElementById("imageQuality");
  qualityInput.addEventListener("input", () => {
    document.getElementById("imageQualityValue").textContent = qualityInput.value;
  });
}

function bindResizeControls() {
  document.getElementById("imageResizeMode").addEventListener("change", updateResizeControlVisibility);
  document.getElementById("imageScalePercent").addEventListener("input", () => {
    const percentInput = document.getElementById("imageScalePercent");
    document.getElementById("imageScalePercentValue").textContent = percentInput.value;
  });
  document.getElementById("imageKeepAspectRatio").addEventListener("change", updateExactSizeWarning);
}

function updateResizeControlVisibility() {
  // Only the controls belonging to the selected resize mode stay visible.
  const resizeMode = document.getElementById("imageResizeMode").value;
  document.getElementById("imageDimensionsControls").classList.toggle("hidden", resizeMode !== "dimensions");
  document.getElementById("imagePercentageControl").classList.toggle("hidden", resizeMode !== "percentage");
  updateExactSizeWarning();
}

function updateExactSizeWarning() {
  // Stretching to an exact size needs both dimensions - warn as soon as the ratio is off.
  const resizeMode = document.getElementById("imageResizeMode").value;
  const keepRatio = document.getElementById("imageKeepAspectRatio").checked;
  const shouldWarn = resizeMode === "dimensions" && !keepRatio;
  document.getElementById("imageExactSizeWarning").classList.toggle("hidden", !shouldWarn);
}

// ---------------------------------------------------------------------------
// Generic UI helpers
// ---------------------------------------------------------------------------

function formatFileSize(byteCount) {
  if (byteCount < 1024) return `${byteCount} B`;
  if (byteCount < 1024 * 1024) return `${(byteCount / 1024).toFixed(1)} KB`;
  if (byteCount < 1024 * 1024 * 1024) return `${(byteCount / (1024 * 1024)).toFixed(1)} MB`;
  return `${(byteCount / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}

async function readApiError(response) {
  try {
    const payload = await response.json();
    if (Array.isArray(payload.detail)) {
      return payload.detail.map((item) => item.msg).join("; ");
    }
    if (typeof payload.detail === "string") {
      return payload.detail;
    }
    return JSON.stringify(payload);
  } catch (parseError) {
    return `Request failed (HTTP ${response.status})`;
  }
}

function appendNonEmptyValue(formData, fieldName, fieldValue) {
  if (fieldValue !== "" && fieldValue !== null) {
    formData.append(fieldName, fieldValue);
  }
}

function showAlert(kind, message) {
  const alertElement = document.getElementById(`${kind}Error`);
  alertElement.textContent = message;
  alertElement.classList.remove("hidden");
}

function clearAlert(kind) {
  const alertElement = document.getElementById(`${kind}Error`);
  alertElement.textContent = "";
  alertElement.classList.add("hidden");
}

function showIndeterminateProgress(kind, messageText) {
  const track = document.getElementById(`${kind}ProgressTrack`);
  const fill = document.getElementById(`${kind}ProgressFill`);
  const text = document.getElementById(`${kind}ProgressText`);
  track.classList.remove("hidden");
  fill.classList.add("is-indeterminate");
  fill.style.width = "35%";
  text.classList.remove("hidden");
  text.textContent = messageText;
}

function updateProgressPercentage(kind, percentage, messageText) {
  const fill = document.getElementById(`${kind}ProgressFill`);
  const text = document.getElementById(`${kind}ProgressText`);
  if (percentage <= 0) {
    // No measurable progress yet (duration unknown) - keep the indeterminate animation.
    fill.classList.add("is-indeterminate");
    fill.style.width = "35%";
  } else {
    fill.classList.remove("is-indeterminate");
    fill.style.width = `${percentage}%`;
  }
  text.textContent = messageText || `${percentage}%`;
}

function hideProgress(kind) {
  document.getElementById(`${kind}ProgressTrack`).classList.add("hidden");
  document.getElementById(`${kind}ProgressText`).classList.add("hidden");
}

function hideResultPanel(kind) {
  const resultPanel = document.getElementById(`${kind}Result`);
  resultPanel.classList.add("hidden");
  resultPanel.classList.remove("flex");
  if (kind === "image") {
    document.getElementById("imageResultList").textContent = "";
  }
}

function showResultPanel(kind, filename, sizeSummary, fileId) {
  document.getElementById(`${kind}ResultName`).textContent = filename;
  document.getElementById(`${kind}ResultSizes`).textContent = sizeSummary;
  document.getElementById(`${kind}DownloadLink`).href = `/api/download/${fileId}`;
  const resultPanel = document.getElementById(`${kind}Result`);
  resultPanel.classList.remove("hidden");
  resultPanel.classList.add("flex");
}

function setActionEnabled(kind, enabled) {
  const buttonId = kind === "image" ? "imageConvertButton" : "videoProcessButton";
  const button = document.getElementById(buttonId);
  if (enabled && kind === "video" && !applicationState.videoToolsEnabled) {
    button.disabled = true;
    return;
  }
  button.disabled = !enabled;
}

// ---------------------------------------------------------------------------
// Image conversion flow (fast - single request, indeterminate progress)
// ---------------------------------------------------------------------------

async function submitImageConversion() {
  const selectedFiles = applicationState.imageFiles;
  if (selectedFiles.length === 0) {
    showAlert("image", "Choose at least one image first.");
    return;
  }

  const selectedOperation = document.getElementById("imageOperation").value;
  const conversionOptions = readImageConversionOptions();
  const resizeValidationError = validateResizeOptions(conversionOptions);
  if (resizeValidationError) {
    showAlert("image", resizeValidationError);
    return;
  }

  clearAlert("image");
  hideResultPanel("image");
  setActionEnabled("image", false);
  document.getElementById("imageChangeButton").disabled = true;
  updateProgressPercentage("image", 0, `Converting 1 of ${selectedFiles.length}...`);

  // Sequential on purpose: the local CPU is shared and progress stays readable.
  const conversionResults = [];
  for (let fileIndex = 0; fileIndex < selectedFiles.length; fileIndex += 1) {
    const currentFile = selectedFiles[fileIndex];
    const completedCount = conversionResults.length;
    updateProgressPercentage(
      "image",
      Math.round((completedCount / selectedFiles.length) * 100),
      `Converting ${completedCount + 1} of ${selectedFiles.length}...`,
    );
    try {
      const requestBody = createImageConversionFormData(currentFile, selectedOperation, conversionOptions);
      const response = await fetch("/api/convert-image", { method: "POST", body: requestBody });
      if (!response.ok) {
        throw new Error(await readApiError(response));
      }
      conversionResults.push({ sourceFile: currentFile, payload: await response.json() });
    } catch (conversionError) {
      conversionResults.push({ sourceFile: currentFile, errorMessage: conversionError.message });
    }
  }

  hideProgress("image");
  renderImageResults(conversionResults);
  const failureCount = conversionResults.filter((result) => !result.payload).length;
  if (failureCount > 0) {
    showAlert("image", `${failureCount} of ${selectedFiles.length} image(s) failed - see the results list.`);
  }
  setActionEnabled("image", true);
  document.getElementById("imageChangeButton").disabled = false;
}

function readImageConversionOptions() {
  return {
    targetFormat: document.getElementById("imageTargetFormat").value,
    quality: document.getElementById("imageQuality").value,
    resizeMode: document.getElementById("imageResizeMode").value,
    maxWidth: document.getElementById("imageMaxWidth").value,
    maxHeight: document.getElementById("imageMaxHeight").value,
    keepAspectRatio: document.getElementById("imageKeepAspectRatio").checked,
    scalePercent: document.getElementById("imageScalePercent").value,
    stripMetadata: document.getElementById("imageStripMetadata").checked ? "true" : "false",
  };
}

function createImageConversionFormData(imageFile, selectedOperation, conversionOptions) {
  const formData = new FormData();
  formData.append("file", imageFile);
  formData.append("operation", selectedOperation);
  formData.append("strip_metadata", conversionOptions.stripMetadata);
  if (selectedOperation === "convert") {
    formData.append("target_format", conversionOptions.targetFormat);
    formData.append("quality", conversionOptions.quality);
    formData.append("resize_mode", conversionOptions.resizeMode);
    formData.append("keep_aspect_ratio", conversionOptions.keepAspectRatio ? "true" : "false");
    formData.append("scale_percent", conversionOptions.scalePercent);
    if (conversionOptions.resizeMode === "dimensions") {
      appendNonEmptyValue(formData, "max_width", conversionOptions.maxWidth);
      appendNonEmptyValue(formData, "max_height", conversionOptions.maxHeight);
    }
  }
  return formData;
}

function validateResizeOptions(conversionOptions) {
  // Returns an error message string, or null when the resize settings are usable.
  if (conversionOptions.resizeMode !== "dimensions") {
    return null;
  }
  const hasWidth = conversionOptions.maxWidth !== "";
  const hasHeight = conversionOptions.maxHeight !== "";
  if (!conversionOptions.keepAspectRatio && (!hasWidth || !hasHeight)) {
    return "Exact-size resize (aspect ratio off) needs both a width and a height.";
  }
  if (conversionOptions.keepAspectRatio && !hasWidth && !hasHeight) {
    return "Enter a max width, a max height, or both.";
  }
  return null;
}

function renderImageResults(conversionResults) {
  const resultPanel = document.getElementById("imageResult");
  const summaryElement = document.getElementById("imageResultSummary");
  const listElement = document.getElementById("imageResultList");
  const downloadAllButton = document.getElementById("imageDownloadAllButton");

  listElement.textContent = "";
  let successCount = 0;
  for (const conversionResult of conversionResults) {
    appendImageResultEntry(listElement, conversionResult);
    if (conversionResult.payload) {
      successCount += 1;
    }
  }
  const failureCount = conversionResults.length - successCount;
  if (failureCount === 0) {
    summaryElement.textContent = `${successCount} image(s) converted`;
  } else {
    summaryElement.textContent = `${successCount} converted - ${failureCount} failed`;
  }
  downloadAllButton.classList.toggle("hidden", successCount === 0);
  resultPanel.classList.remove("hidden");
}

function appendImageResultEntry(listElement, conversionResult) {
  const entryElement = document.createElement("li");
  entryElement.className = "flex items-center justify-between gap-3 rounded-lg bg-gray-800 px-3 py-2";

  const infoContainer = document.createElement("div");
  infoContainer.className = "min-w-0";
  const nameLine = document.createElement("p");
  nameLine.className = "truncate text-sm font-medium text-green-100";
  nameLine.textContent = conversionResult.payload
    ? conversionResult.payload.filename
    : conversionResult.sourceFile.name;

  const sizeLine = document.createElement("p");
  if (conversionResult.payload) {
    sizeLine.className = "mt-0.5 text-xs text-green-300";
    sizeLine.textContent =
      `${formatFileSize(conversionResult.payload.input_size)} -> ${formatFileSize(conversionResult.payload.output_size)}`;
  } else {
    sizeLine.className = "mt-0.5 text-xs text-red-300";
    sizeLine.textContent = conversionResult.errorMessage;
  }
  infoContainer.appendChild(nameLine);
  infoContainer.appendChild(sizeLine);
  entryElement.appendChild(infoContainer);

  if (conversionResult.payload) {
    const downloadLink = document.createElement("a");
    downloadLink.className =
      "whitespace-nowrap rounded-lg bg-green-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-green-500";
    downloadLink.textContent = "Download";
    downloadLink.href = `/api/download/${conversionResult.payload.file_id}`;
    entryElement.appendChild(downloadLink);
  }
  listElement.appendChild(entryElement);
}

function downloadAllImageResults() {
  // Browsers may ask permission for multiple automatic downloads - allow it once.
  const downloadLinks = Array.from(document.getElementById("imageResultList").querySelectorAll("a"));
  downloadLinks.forEach((downloadLink, linkIndex) => {
    // Stagger the clicks so each one-shot download is a separate request.
    setTimeout(() => downloadLink.click(), linkIndex * 500);
  });
}

// ---------------------------------------------------------------------------
// Video processing flow (job + polling for real-time progress, FR-UI-03)
// ---------------------------------------------------------------------------

async function submitVideoProcessing() {
  const selectedFile = applicationState.videoFile;
  if (!selectedFile) {
    showAlert("video", "Choose a video file first.");
    return;
  }
  if (!applicationState.videoToolsEnabled) {
    showAlert("video", "FFmpeg is missing - video tools are disabled.");
    return;
  }

  const formData = new FormData();
  formData.append("file", selectedFile);
  formData.append("operation", document.getElementById("videoOperation").value);
  formData.append("crf_preset", document.getElementById("videoCrfPreset").value);
  formData.append("start_time", document.getElementById("trimStart").value.trim());
  formData.append("end_time", document.getElementById("trimEnd").value.trim());
  formData.append("gif_fps", document.getElementById("gifFps").value);
  formData.append("gif_width", document.getElementById("gifWidth").value);
  formData.append("strip_metadata", document.getElementById("videoStripMetadata").checked ? "true" : "false");

  clearAlert("video");
  hideResultPanel("video");
  setActionEnabled("video", false);
  showIndeterminateProgress("video", "Uploading...");

  try {
    const response = await fetch("/api/process-video", { method: "POST", body: formData });
    if (!response.ok) {
      throw new Error(await readApiError(response));
    }
    const job = await response.json();
    startVideoProgressPolling(job.job_id, job.file_id, selectedFile.size);
  } catch (submissionError) {
    hideProgress("video");
    setActionEnabled("video", true);
    showAlert("video", submissionError.message);
  }
}

function startVideoProgressPolling(jobId, fileId, inputSize) {
  stopVideoProgressPolling();
  let attemptCount = 0;
  showIndeterminateProgress("video", "Waiting for FFmpeg...");

  activeVideoPollTimer = setInterval(async () => {
    attemptCount += 1;
    if (attemptCount > MAX_POLL_ATTEMPTS) {
      stopVideoProgressPolling();
      finishVideoWithError("Processing timed out - the job took too long. Check the server console.");
      return;
    }

    let progressState;
    try {
      const progressResponse = await fetch(`/api/progress/${jobId}`);
      if (progressResponse.status === 404) {
        return; // job state is not visible yet - keep polling
      }
      if (!progressResponse.ok) {
        throw new Error(await readApiError(progressResponse));
      }
      progressState = await progressResponse.json();
    } catch (pollError) {
      return; // transient hiccup - the next tick retries
    }

    if (progressState.status === "error") {
      stopVideoProgressPolling();
      finishVideoWithError(progressState.message);
      return;
    }
    if (progressState.status === "done") {
      stopVideoProgressPolling();
      finishVideoJob(progressState, fileId, inputSize);
      return;
    }
    updateProgressPercentage("video", Number(progressState.progress) || 0, progressState.message);
  }, POLL_INTERVAL_MS);
}

function stopVideoProgressPolling() {
  if (activeVideoPollTimer) {
    clearInterval(activeVideoPollTimer);
    activeVideoPollTimer = null;
  }
}

function finishVideoWithError(message) {
  hideProgress("video");
  setActionEnabled("video", true);
  showAlert("video", message);
}

function finishVideoJob(progressState, fileId, inputSize) {
  hideProgress("video");
  setActionEnabled("video", true);
  const outputSizeText = typeof progressState.output_size === "number"
    ? formatFileSize(progressState.output_size)
    : "n/a";
  const resultName = progressState.filename || "processed video";
  showResultPanel("video", resultName, `${formatFileSize(inputSize)} -> ${outputSizeText}`, fileId);
}


