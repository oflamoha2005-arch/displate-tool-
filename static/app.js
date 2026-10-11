/* ── Displate One-Click Preparer · app.js v2 ─────────────────────────────── */

// ── Theme ───────────────────────────────────────────────────────────────────
const themeBtn   = $i('themeBtn');
const themeIcon  = $i('themeIcon');
const themeLabel = $i('themeLabel');

(function initTheme() {
  if (localStorage.getItem('theme') === 'light') applyLight();
})();

themeBtn.addEventListener('click', () => {
  if (document.body.classList.contains('light')) {
    document.body.classList.remove('light');
    themeIcon.textContent  = '☀️';
    themeLabel.textContent = 'Light';
    localStorage.setItem('theme', 'dark');
  } else {
    applyLight();
  }
});

function applyLight() {
  document.body.classList.add('light');
  themeIcon.textContent  = '🌙';
  themeLabel.textContent = 'Dark';
  localStorage.setItem('theme', 'light');
}

// ── Helpers ─────────────────────────────────────────────────────────────────
function $i(id)      { return document.getElementById(id); }
function delay(ms)   { return new Promise(r => setTimeout(r, ms)); }
function esc(v) {
  return String(v ?? '').replace(/[&<>"']/g, c =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;' }[c])
  );
}
function fmtDate(ts) {
  const d = new Date(ts * 1000);
  return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
    + ' ' + d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
}

// ── State ────────────────────────────────────────────────────────────────────
let files       = [];
let batchJobIds = [];   // for CSV export

// ── DOM refs ─────────────────────────────────────────────────────────────────
const uploadZone    = $i('uploadZone');
const fileInput     = $i('fileInput');
const previewStrip  = $i('previewStrip');
const processBtn    = $i('processBtn');
const processBtnLbl = $i('processBtnLabel');
const focusSelect   = $i('focusSelect');
const resizeModeSelect = $i('resizeModeSelect');

const progressPanel = $i('progressPanel');
const progressTitle = $i('progressTitle');
const progressBadge = $i('progressBadge');
const stepsList     = $i('stepsList');
const checksGrid    = $i('checksGrid');
const checksContainer = $i('checksContainer');
const blockedBox    = $i('blockedBox');

const autoUpscaleCheck   = $i('autoUpscaleCheck');
const upscaleOptionsRow  = $i('upscaleOptionsRow');
const upscaleModelSelect = $i('upscaleModelSelect');

if (autoUpscaleCheck && upscaleOptionsRow) {
  autoUpscaleCheck.addEventListener('change', () => {
    upscaleOptionsRow.classList.toggle('hidden', !autoUpscaleCheck.checked);
  });
}

const textureCheck          = $i('textureCheck');
const textureOptionsRow     = $i('textureOptionsRow');
const textureTypeSelect     = $i('textureTypeSelect');
const textureStrengthSelect = $i('textureStrengthSelect');

if (textureCheck && textureOptionsRow) {
  textureCheck.addEventListener('change', () => {
    textureOptionsRow.classList.toggle('hidden', !textureCheck.checked);
  });
}

const batchBarWrap  = $i('batchBarWrap');
const batchBarLabel = $i('batchBarLabel');
const batchBarFill  = $i('batchBarFill');

const dupPanel      = $i('dupPanel');
const riskPanel     = $i('riskPanel');
const resultPanel   = $i('resultPanel');
const batchResults  = $i('batchResults');
const batchList     = $i('batchList');
const batchExportRow= $i('batchExportRow');
const batchCsvBtn   = $i('batchCsvBtn');
const historyToggle = $i('historyToggle');
const historyPanel  = $i('historyPanel');
const historyList   = $i('historyList');

// ── Drag & Drop ──────────────────────────────────────────────────────────────
['dragenter', 'dragover'].forEach(e =>
  uploadZone.addEventListener(e, ev => { ev.preventDefault(); uploadZone.classList.add('drag'); })
);
['dragleave', 'drop'].forEach(e =>
  uploadZone.addEventListener(e, ev => { ev.preventDefault(); uploadZone.classList.remove('drag'); })
);
uploadZone.addEventListener('drop', e => addFiles([...e.dataTransfer.files]));
fileInput.addEventListener('change', () => { addFiles([...fileInput.files]); fileInput.value = ''; });

function addFiles(incoming) {
  const allowed = ['.jpg', '.jpeg', '.png', '.webp', '.avif'];
  incoming.forEach(f => {
    const ext = f.name.slice(f.name.lastIndexOf('.')).toLowerCase();
    if (!allowed.includes(ext) || files.length >= 5) return;
    if (!files.some(x => x.name === f.name && x.size === f.size)) files.push(f);
  });
  renderPreviews();
  updateBtn();
}

function removeFile(idx) { files.splice(idx, 1); renderPreviews(); updateBtn(); }

function renderPreviews() {
  previewStrip.innerHTML = '';
  files.forEach((f, i) => {
    const wrap = document.createElement('div');
    wrap.className = 'preview-thumb fade-in';
    const img = document.createElement('img');
    const btn = document.createElement('button');
    btn.className = 'remove-btn'; btn.textContent = '✕';
    btn.addEventListener('click', e => { e.stopPropagation(); removeFile(i); });
    const reader = new FileReader();
    reader.onload = ev => { img.src = ev.target.result; };
    reader.readAsDataURL(f);
    wrap.appendChild(img); wrap.appendChild(btn);
    previewStrip.appendChild(wrap);
  });
  if (files.length > 0 && files.length < 5) {
    const more = document.createElement('div');
    more.className = 'preview-count';
    more.textContent = `+ ${5 - files.length} more`;
    previewStrip.appendChild(more);
  }
}

function updateBtn() {
  if (!files.length) {
    processBtn.disabled = true;
    processBtnLbl.textContent = 'Select an image to begin';
  } else if (files.length === 1) {
    processBtn.disabled = false;
    processBtnLbl.textContent = 'Prepare for Displate';
  } else {
    processBtn.disabled = false;
    processBtnLbl.textContent = `Prepare ${files.length} images for Displate`;
  }
}

// ── Process Button ────────────────────────────────────────────────────────────
processBtn.addEventListener('click', async () => {
  if (!files.length) return;
  resetPanels();
  progressPanel.classList.add('visible', 'fade-in');
  window.scrollTo({ top: progressPanel.offsetTop - 20, behavior: 'smooth' });
  processBtn.disabled = true;
  processBtnLbl.textContent = 'Processing…';

  if (files.length === 1) {
    await processSingle(files[0]);
  } else {
    await processBatch([...files]);
  }

  processBtn.disabled = false;
  updateBtn();
  refreshHistory();
});

// ── Single image ──────────────────────────────────────────────────────────────
async function processSingle(file) {
  setBadge('running', 'Processing');
  progressTitle.textContent = `Processing: ${file.name}`;

  const isUpscaling = autoUpscaleCheck && autoUpscaleCheck.checked;
  const STEP_DEFS = [
    { label: 'Opening image' },
    ...(isUpscaling ? [{ label: 'AI Super-Resolution (Real-ESRGAN)' }] : []),
    { label: 'Running preflight checks' },
    { label: 'Cropping to 5:7' },
    { label: 'Resizing to Displate optimal (4000×5600)' },
    { label: 'Converting colour profile' },
    { label: 'Exporting JPEG (300 DPI)' },
    { label: 'Creating safe-area preview' },
    { label: 'Verifying on disk' },
    { label: 'Finalising package' },
  ];
  renderPendingSteps(STEP_DEFS);
  activateStep(0);

  const fd = new FormData();
  fd.append('image', file);
  fd.append('focus', focusSelect.value);
  if (resizeModeSelect) {
    fd.append('resize_mode', resizeModeSelect.value);
  }
  if (autoUpscaleCheck) {
    fd.append('auto_upscale', autoUpscaleCheck.checked);
    fd.append('upscale_model', upscaleModelSelect.value);
  }
  if (textureCheck) {
    fd.append('texture_enabled', textureCheck.checked);
    fd.append('texture_type', textureTypeSelect ? textureTypeSelect.value : 'fine_paper');
    fd.append('texture_strength', textureStrengthSelect ? textureStrengthSelect.value : 'very_subtle');
  }

  let data;
  try {
    const res = await fetch('/api/process', { method: 'POST', body: fd });
    data = await res.json();
    if (!res.ok) throw new Error(data.detail || 'Processing failed.');
  } catch (err) {
    setBadge('fail', 'Error');
    progressTitle.textContent = 'Error';
    markAllFail(STEP_DEFS.length);
    showChecks([]);
    showBlocked(err.message);
    return;
  }

  // Animate steps
  const stepCount = data.steps ? data.steps.length : STEP_DEFS.length;
  for (let i = 0; i < stepCount; i++) {
    const msg = data.steps ? data.steps[i].message : '';
    markStepDone(i, msg);
    if (i + 1 < STEP_DEFS.length) activateStep(i + 1);
    await delay(70);
  }

  const allChecks = data.report.checks_flat || (Array.isArray(data.report.checks) ? data.report.checks : Object.values(data.report.checks || {}).flat());
  showChecks(allChecks);
  showRiskScore(data.report.risk_score);

  // Duplicate warning
  const dupCheck = allChecks.find(c => (c.code === 'near_duplicate' || c.id === 'near_duplicate'));
  if (dupCheck && (dupCheck.status === 'warn' || dupCheck.status === 'WARNING') && dupCheck.value && dupCheck.value.length) {
    showDuplicateWarning(data.source_url, dupCheck.value[0]);
  }

  if (!data.prepared) {
    setBadge('fail', 'Blocked');
    progressTitle.textContent = 'Source blocked — resolution too low';
    showBlocked();
    return;
  }

  setBadge('done', 'Complete');
  progressTitle.textContent = 'Processing complete';
  showResult(data);
}

// ── Batch ─────────────────────────────────────────────────────────────────────
async function processBatch(fileList) {
  setBadge('running', 'Batch running');
  progressTitle.textContent = `Processing ${fileList.length} images…`;
  batchBarWrap.classList.remove('hidden');
  batchList.innerHTML = '';
  batchJobIds = [];

    const isUpscaling = autoUpscaleCheck && autoUpscaleCheck.checked;
    const STEP_DEFS = [
      { label: 'Opening image' },
      ...(isUpscaling ? [{ label: 'AI Super-Resolution' }] : []),
      { label: 'Running checks' }, { label: 'Cropping' },
      { label: 'Colour conversion' }, { label: 'Exporting JPEG' },
      { label: 'Safe-area preview' }, { label: 'Done' },
    ];

  batchResults.classList.add('visible');

  for (let i = 0; i < fileList.length; i++) {
    const file = fileList[i];
    batchBarLabel.textContent = `Image ${i + 1} of ${fileList.length}: ${file.name}`;
    batchBarFill.style.width  = `${(i / fileList.length) * 100}%`;
    progressTitle.textContent = `Processing ${i + 1}/${fileList.length}: ${file.name}`;

    renderPendingSteps(STEP_DEFS);
    activateStep(0);

    const biEl = makeBatchItem(file.name, 'processing');
    batchList.appendChild(biEl);

    const fd = new FormData();
    fd.append('image', file);
    fd.append('focus', focusSelect.value);
    if (resizeModeSelect) {
      fd.append('resize_mode', resizeModeSelect.value);
    }
    if (autoUpscaleCheck) {
      fd.append('auto_upscale', autoUpscaleCheck.checked);
      fd.append('upscale_model', upscaleModelSelect.value);
    }
    if (textureCheck) {
      fd.append('texture_enabled', textureCheck.checked);
      fd.append('texture_type', textureTypeSelect ? textureTypeSelect.value : 'fine_paper');
      fd.append('texture_strength', textureStrengthSelect ? textureStrengthSelect.value : 'very_subtle');
    }

    let data;
    try {
      const res = await fetch('/api/process', { method: 'POST', body: fd });
      data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Failed.');
    } catch (err) {
      markAllFail(STEP_DEFS.length);
      updateBatchItem(biEl, file.name, 'error', null, err.message);
      continue;
    }

    const stepCount = data.steps ? data.steps.length : STEP_DEFS.length;
    for (let s = 0; s < stepCount; s++) {
      markStepDone(s, data.steps ? data.steps[s].message : '');
      if (s + 1 < STEP_DEFS.length) activateStep(s + 1);
      await delay(50);
    }

    if (!data.prepared) {
      updateBatchItem(biEl, file.name, 'blocked', null, 'Resolution too low.');
    } else {
      updateBatchItem(biEl, file.name, 'ready', data, null);
      if (data.job_id) batchJobIds.push(data.job_id);
    }
  }

  batchBarFill.style.width  = '100%';
  batchBarLabel.textContent = `Done — ${fileList.length} images processed.`;
  setBadge('done', 'Batch complete');
  progressTitle.textContent = 'Batch complete';
  showChecks([]);
  if (batchJobIds.length) batchExportRow.style.display = 'flex';
}

function makeBatchItem(name, state) {
  const div = document.createElement('div');
  div.className = 'batch-item fade-in';
  div.innerHTML = `<span class="bi-name">${esc(name)}</span>
    <span class="bi-badge ${state}">${state === 'processing' ? '⏳ Processing…' : state}</span>`;
  return div;
}

function updateBatchItem(el, name, state, data, errMsg) {
  let badge = '', links = '';
  if (state === 'ready') {
    const risk = data.report?.risk_score;
    const riskBadge = risk
      ? `<span class="hi-risk ${risk.level}">${risk.score}% risk</span>` : '';
    badge = `<span class="bi-badge ready">✅ Ready</span>${riskBadge}`;
    links = `<a class="bi-dl" href="${data.final_url}" download>⬇ JPG</a>
             <a class="bi-dl" href="${data.zip_url}"   download>📦 ZIP</a>`;
  } else if (state === 'blocked') {
    badge = `<span class="bi-badge blocked">⛔ Blocked</span>`;
    links = `<span style="font-size:12px;color:var(--muted)">${esc(errMsg || 'Resolution too low')}</span>`;
  } else {
    badge = `<span class="bi-badge error">⚠ Error</span>`;
    links = `<span style="font-size:12px;color:var(--muted)">${esc(errMsg || 'Unknown error')}</span>`;
  }
  el.innerHTML = `<span class="bi-name">${esc(name)}</span>${badge}${links}`;
}

// ── Batch CSV export ──────────────────────────────────────────────────────────
batchCsvBtn.addEventListener('click', async () => {
  if (!batchJobIds.length) return;
  try {
    const res = await fetch('/api/batch-report', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ job_ids: batchJobIds }),
    });
    if (!res.ok) throw new Error('CSV export failed.');
    const blob = await res.blob();
    const url  = URL.createObjectURL(blob);
    const a    = document.createElement('a');
    a.href = url; a.download = 'displate_batch_report.csv'; a.click();
    URL.revokeObjectURL(url);
  } catch (err) {
    alert(err.message);
  }
});

// ── Steps rendering ───────────────────────────────────────────────────────────
function renderPendingSteps(defs) {
  stepsList.innerHTML = defs.map((d, i) => `
    <div class="step-item pending" id="step-${i}">
      <div class="step-icon">⬜</div>
      <div><div class="step-label">${esc(d.label)}</div>
      <div class="step-msg" id="step-msg-${i}"></div></div>
    </div>`).join('');
}
function activateStep(i) {
  const el = $i(`step-${i}`); if (!el) return;
  el.className = 'step-item running';
  el.querySelector('.step-icon').innerHTML = '<div class="spinner"></div>';
}
function markStepDone(i, msg) {
  const el = $i(`step-${i}`); if (!el) return;
  el.className = 'step-item done';
  el.querySelector('.step-icon').textContent = '✅';
  const m = $i(`step-msg-${i}`); if (m && msg) m.textContent = msg;
}
function markAllFail(count) {
  for (let i = 0; i < count; i++) {
    const el = $i(`step-${i}`); if (!el) continue;
    if (el.classList.contains('pending') || el.classList.contains('running')) {
      el.className = 'step-item fail';
      el.querySelector('.step-icon').textContent = '❌';
    }
  }
}

// ── Checks rendering ──────────────────────────────────────────────────────────
const ICONS = { pass: '✅', warn: '⚠️', fail: '❌' };

function showChecks(checks) {
  if (checks && !Array.isArray(checks)) {
    checks = Object.values(checks).flat();
  }
  if (!checks || !checks.length) { checksContainer.classList.remove('visible'); return; }
  checksGrid.innerHTML = checks.map(c => {
    const rawStatus = (c.status || '').toLowerCase();
    const isPass = rawStatus === 'pass';
    const isFail = rawStatus === 'fail';
    const isWarn = rawStatus === 'warn' || rawStatus === 'warning';
    const normStatus = isFail ? 'fail' : (isWarn ? 'warn' : (isPass ? 'pass' : 'info'));
    const icon = isFail ? '❌' : (isWarn ? '⚠️' : (isPass ? '✅' : 'ℹ️'));

    // Size badge chip for source_size
    let extra = '';
    const checkId = c.id || c.code;
    if ((checkId === 'source_size' || checkId === 'minimum_short_side') && c.details && c.details.badge) {
      const b = c.details.badge;
      const labels = { optimal: '⭐ Optimal', minimum: 'Minimum', blocked: 'Blocked' };
      extra = `<span class="size-badge-chip ${b}">${labels[b] || b}</span>`;
    }
    // Duplicate similarity badge
    if (checkId === 'near_duplicate' && isWarn && c.value && c.value.length) {
      extra = `<span class="size-badge-chip minimum">${c.value[0].similarity}% similar</span>`;
    }
    return `<div class="check-card ${normStatus}">
      <div class="check-dot">${icon}</div>
      <div>
        <span class="status-chip">${esc(c.status)}</span>${extra}
        <div class="check-label">${esc(c.name || c.label)}</div>
        <div class="check-msg">${esc(c.message)}</div>
      </div>
    </div>`;
  }).join('');
  checksContainer.classList.add('visible');
}

// ── Risk Score ────────────────────────────────────────────────────────────────
function showRiskScore(risk) {
  if (!risk) { riskPanel.classList.remove('visible'); return; }
  const el = $i('riskScore');
  el.textContent  = `${risk.score}%`;
  el.className    = `risk-score ${risk.level}`;
  const fill = $i('riskMeterFill');
  fill.style.width = `${risk.score}%`;
  fill.className   = `risk-meter-fill ${risk.level}`;
  const bd = $i('riskBreakdown');
  if (risk.breakdown && risk.breakdown.length) {
    bd.innerHTML = risk.breakdown.map(b => `
      <div class="risk-item">
        <span class="risk-item-label">${esc(b.label)}</span>
        <span class="risk-item-penalty ${b.status}">+${b.penalty} pts</span>
      </div>`).join('');
  } else {
    bd.innerHTML = '<div class="risk-item" style="color:var(--accent)">No risk factors — clean image ✅</div>';
  }
  riskPanel.classList.add('visible');
}

// ── Duplicate Warning ─────────────────────────────────────────────────────────
function showDuplicateWarning(currentUrl, match) {
  $i('dupCurrentImg').src  = currentUrl;
  $i('dupCurrentLabel').textContent = 'Current upload';
  $i('dupMatchImg').src    = match.preview_url || '';
  $i('dupSimilarity').textContent   = `${match.similarity}% similar · Hamming distance: ${match.distance}`;
  $i('dupSubtitle').textContent     =
    `This image is ${match.similarity}% similar to job "${match.job_id}" (${match.filename || ''}).`;
  dupPanel.classList.add('visible');
}

// ── Blocked Box ───────────────────────────────────────────────────────────────
function showBlocked(msg) {
  if (msg) {
    const p = blockedBox.querySelector('p');
    if (p) p.textContent = msg;
  }
  blockedBox.classList.add('visible');
}

// ── Result rendering ──────────────────────────────────────────────────────────
function showResult(data) {
  const f  = data.report.final;
  const ts = '?t=' + Date.now();
  $i('finalImg').src  = data.final_url   + ts;
  $i('safeImg').src   = data.preview_url + ts;
  $i('finalDl').href  = data.final_url;
  $i('safeDl').href   = data.preview_url;
  $i('zipDl').href    = data.zip_url;
  $i('reportDl').href = data.report_url;

  if (data.mask_preview_url && $i('maskImgCard')) {
    $i('maskImgCard').style.display = 'block';
    $i('maskImg').src = data.mask_preview_url + ts;
    $i('maskDl').href = data.mask_preview_url;
  } else if ($i('maskImgCard')) {
    $i('maskImgCard').style.display = 'none';
  }

  const badge = data.report.size_badge || 'minimum';
  const badgeLabels = { optimal: '⭐ Optimal', minimum: 'Minimum', blocked: 'Blocked' };
  const specs = [
    { value: `${f.width} × ${f.height}`, label: 'Final pixels',   accent: false },
    { value: '5 : 7',                    label: 'Aspect ratio',   accent: true  },
    { value: '300 DPI',                  label: 'Resolution',     accent: false },
    { value: 'sRGB',                     label: 'Colour profile', accent: false },
    { value: `${f.file_mb} MB`,          label: 'File size',      accent: false },
    { value: `Q${f.quality}`,            label: 'JPEG quality',   accent: false },
    { value: f.upscaled ? (f.upscale_info?.scale ? `YES (${f.upscale_info.scale}x AI)` : 'YES (AI)') : 'NO', label: 'Upscaled', accent: !!f.upscaled },
    { value: `${badgeLabels[badge]}`,    label: 'Size quality',   accent: badge === 'optimal' },
  ];

  if (data.report?.processing?.texture?.applied) {
    const tex = data.report.processing.texture;
    const typeLabel = tex.texture_type === 'fine_canvas' ? 'Canvas' : 'Paper';
    specs.push({
      value: `${typeLabel} (${tex.strength})`,
      label: 'Print Texture',
      accent: true,
    });
  }
  $i('specsGrid').innerHTML = specs.map(s =>
    `<div class="spec-card ${s.accent ? 'accent' : ''}">
      <div class="spec-value">${esc(s.value)}</div>
      <div class="spec-label">${esc(s.label)}</div>
    </div>`
  ).join('');

  resultPanel.classList.add('visible', 'fade-in');
  window.scrollTo({ top: resultPanel.offsetTop - 20, behavior: 'smooth' });
}

// ── History ───────────────────────────────────────────────────────────────────
historyToggle.addEventListener('click', () => {
  historyToggle.classList.toggle('open');
  historyPanel.classList.toggle('visible');
  if (historyPanel.classList.contains('visible')) refreshHistory();
});

async function refreshHistory() {
  try {
    const res  = await fetch('/api/jobs');
    const data = await res.json();
    renderHistory(data.jobs || []);
  } catch (_) {
    historyList.innerHTML = '<div class="history-empty">Could not load history.</div>';
  }
}

function renderHistory(jobs) {
  if (!jobs.length) {
    historyList.innerHTML = '<div class="history-empty">No recent jobs found.</div>';
    return;
  }
  historyList.innerHTML = jobs.map(j => {
    const status = j.prepared ? 'ready' : 'blocked';
    const dims   = (j.width && j.height) ? `${j.width}×${j.height}` : '—';
    return `<div class="history-item fade-in">
      <div class="hi-status ${status}"></div>
      <span class="hi-id">${esc(j.job_id)}</span>
      <span class="hi-dims">${dims}</span>
      ${j.risk_score !== undefined
        ? `<span class="hi-risk ${j.risk_level || 'low'}">${j.risk_score}% risk</span>`
        : ''}
      <span class="hi-date">${fmtDate(j.mtime)}</span>
      <div class="hi-actions">
        ${j.prepared
          ? `<a class="hi-dl" href="/download/${j.job_id}/displate_ready_package.zip" download>📦 ZIP</a>`
          : ''}
        <button class="hi-del" data-jid="${esc(j.job_id)}">🗑 Delete</button>
      </div>
    </div>`;
  }).join('');

  historyList.querySelectorAll('.hi-del').forEach(btn => {
    btn.addEventListener('click', async () => {
      const jid = btn.dataset.jid;
      if (!confirm(`Delete job ${jid}?`)) return;
      try {
        await fetch(`/api/jobs/${jid}`, { method: 'DELETE' });
        refreshHistory();
      } catch (_) {
        alert('Could not delete job.');
      }
    });
  });
}

// ── Panel helpers ─────────────────────────────────────────────────────────────
function setBadge(state, text) {
  progressBadge.className = `progress-badge ${state}`;
  progressBadge.textContent = text;
}

function resetPanels() {
  for (const el of [progressPanel, resultPanel, batchResults, dupPanel, riskPanel, blockedBox]) {
    el.classList.remove('visible', 'fade-in');
  }
  checksContainer.classList.remove('visible');
  batchBarWrap.classList.add('hidden');
  stepsList.innerHTML = '';
  checksGrid.innerHTML = '';
  batchList.innerHTML = '';
  $i('specsGrid').innerHTML = '';
  $i('riskBreakdown').innerHTML = '';
  batchExportRow.style.display = 'none';
  batchJobIds = [];
}

// ── On load: history pre-fetch (silent) ──────────────────────────────────────
window.addEventListener('load', () => {
  fetch('/api/jobs').then(r => r.json()).then(d => {
    if (d.jobs && d.jobs.length) renderHistory(d.jobs);
  }).catch(() => {});
});
