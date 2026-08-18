const form = document.querySelector('#extract-form');
const fileInput = document.querySelector('#document');
const fileLabel = document.querySelector('#file-label');
const method = document.querySelector('#method');
const providerSelect = document.querySelector('#provider');
const llmSettings = document.querySelector('#llm-settings');
const configArea = document.querySelector('#config-json');
const statusEl = document.querySelector('#status');
const submitButton = document.querySelector('#submit-button');
const resultsEl = document.querySelector('#results');
const dropzone = document.querySelector('#dropzone');

let scenarioConfig = null;

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>'"]/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[char]));
}

async function loadScenario() {
  const response = await fetch('/api/scenarios/procurement-compliance');
  scenarioConfig = await response.json();
  configArea.value = JSON.stringify(scenarioConfig, null, 2);
  method.value = scenarioConfig.method;
  syncMethod();
}

function syncMethod() {
  llmSettings.classList.toggle('hidden', method.value !== 'llm');
  if (method.value === 'llm') syncProvider();
}

function syncProvider() {
  if (providerSelect.value === 'kimi') {
    document.querySelector('#model').value = 'kimi-k3';
    document.querySelector('#base-url').value = 'https://api.moonshot.cn/v1';
  }
}

async function loadSettings() {
  const response = await fetch('/api/settings/llm');
  const settings = await response.json();
  if (!response.ok) throw new Error(settings.detail || '模型配置加载失败');
  providerSelect.value = settings.provider;
  document.querySelector('#model').value = settings.model;
  document.querySelector('#base-url').value = settings.base_url;
  document.querySelector('#key-status').textContent = settings.has_api_key
    ? 'API Key 已加密保存在数据库中；留空不会覆盖。推荐在 Semantica 主界面的“Documents → 模型配置”中维护。'
    : 'API Key 尚未配置；填写后会加密保存到 SQLite。推荐在 Semantica 主界面的“Documents → 模型配置”中维护。';
}

async function saveSettingsIfNeeded() {
  const apiKey = document.querySelector('#api-key').value.trim();
  if (!apiKey) return;
  const response = await fetch('/api/settings/llm', {
    method: 'PUT',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      provider: 'kimi',
      display_name: 'Kimi K3',
      model: document.querySelector('#model').value.trim(),
      base_url: document.querySelector('#base-url').value.trim(),
      api_key: apiKey,
    }),
  });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.detail || '模型配置保存失败');
}

function showStatus(message, isError = false) {
  statusEl.textContent = message;
  statusEl.classList.toggle('error', isError);
}

function wait(milliseconds) {
  return new Promise(resolve => window.setTimeout(resolve, milliseconds));
}

function elapsedLabel(seconds) {
  const minutes = Math.floor((seconds || 0) / 60);
  const remainder = Math.floor((seconds || 0) % 60);
  return minutes ? `${minutes} 分 ${String(remainder).padStart(2, '0')} 秒` : `${remainder} 秒`;
}

async function waitForBatchJob(jobId) {
  while (true) {
    const response = await fetch(`/api/extractions/${jobId}/status`);
    const progress = await response.json();
    if (!response.ok) throw new Error(progress.detail || '读取抽取进度失败');
    const current = progress.current_file ? `，当前：${progress.current_file}` : '';
    showStatus(`已处理 ${progress.completed}/${progress.total}（${progress.percent}%）${current}，用时 ${elapsedLabel(progress.elapsed_seconds)}`);
    if (['completed', 'partial', 'failed'].includes(progress.status)) {
      if (!progress.result_ready) throw new Error(progress.message || '任务未生成结果');
      const resultResponse = await fetch(`/api/extractions/${jobId}`);
      const result = await resultResponse.json();
      if (!resultResponse.ok) throw new Error(result.detail || '读取抽取结果失败');
      return {job_id: jobId, status: progress.status, items: progress.items, result};
    }
    await wait(1000);
  }
}

function evidenceText(metadata) {
  const evidence = metadata?.evidence?.[0];
  return evidence?.text || '—';
}

function renderResult(payload) {
  const result = payload.result;
  const stats = result.statistics;
  const byId = Object.fromEntries(result.entities.map(entity => [entity.id, entity]));
  document.querySelector('#metrics').innerHTML = [
    ['文档', stats.documents], ['文本分块', stats.chunks], ['实体节点', stats.entities], ['关系边', stats.relationships]
  ].map(([label, value]) => `<div class="metric"><strong>${value}</strong><span>${label}</span></div>`).join('');
  document.querySelector('#text-preview').textContent = result.documents?.[0]?.text_preview || '';
  document.querySelector('#entity-body').innerHTML = result.entities.map(entity => `
    <tr><td><span class="type-pill">${escapeHtml(entity.type)}</span></td><td>${escapeHtml(entity.text)}</td><td>${Number(entity.confidence).toFixed(2)}</td><td class="evidence">${escapeHtml(evidenceText(entity.metadata))}</td></tr>
  `).join('') || '<tr><td colspan="4">没有抽取到实体</td></tr>';
  document.querySelector('#relation-body').innerHTML = result.relationships.map(relation => `
    <tr><td>${escapeHtml(byId[relation.source]?.text || relation.source)}</td><td><span class="type-pill">${escapeHtml(relation.type)}</span></td><td>${escapeHtml(byId[relation.target]?.text || relation.target)}</td><td>${Number(relation.metadata?.confidence ?? relation.weight).toFixed(2)}</td><td class="evidence">${escapeHtml(evidenceText(relation.metadata))}</td></tr>
  `).join('') || '<tr><td colspan="5">没有抽取到关系</td></tr>';
  document.querySelector('#download-json').href = `/api/extractions/${payload.job_id}/export/json`;
  document.querySelector('#download-csv').href = `/api/extractions/${payload.job_id}/export/csv`;
  const warnings = document.querySelector('#warnings');
  warnings.classList.toggle('hidden', !result.warnings?.length);
  warnings.textContent = result.warnings?.join('\n') || '';
  resultsEl.classList.remove('hidden');
  resultsEl.scrollIntoView({behavior: 'smooth', block: 'start'});
}

method.addEventListener('change', syncMethod);
providerSelect.addEventListener('change', syncProvider);
fileInput.addEventListener('change', () => {
  fileLabel.textContent = fileInput.files?.length ? `已选择 ${fileInput.files.length} 个文件` : '选择或拖入多个文档';
});
['dragenter', 'dragover'].forEach(name => dropzone.addEventListener(name, event => { event.preventDefault(); dropzone.classList.add('drag'); }));
['dragleave', 'drop'].forEach(name => dropzone.addEventListener(name, event => { event.preventDefault(); dropzone.classList.remove('drag'); }));
dropzone.addEventListener('drop', event => {
  if (event.dataTransfer.files.length) {
    fileInput.files = event.dataTransfer.files;
    fileLabel.textContent = `已选择 ${event.dataTransfer.files.length} 个文件`;
  }
});

form.addEventListener('submit', async event => {
  event.preventDefault();
  if (!fileInput.files.length) return;
  submitButton.disabled = true;
  resultsEl.classList.add('hidden');
  showStatus('正在解析文档并抽取实体关系，LLM 模式可能需要数分钟…');
  try {
    const config = JSON.parse(configArea.value);
    config.method = method.value;
    if (method.value === 'llm') {
      config.provider = providerSelect.value;
      config.model = document.querySelector('#model').value.trim();
      config.base_url = document.querySelector('#base-url').value.trim() || null;
      await saveSettingsIfNeeded();
      delete config.api_key;
    }
    const body = new FormData();
    Array.from(fileInput.files).forEach(file => body.append('files', file));
    body.append('config_json', JSON.stringify(config));
    const response = await fetch('/api/extractions/batch', {method: 'POST', body});
    const submitted = await response.json();
    if (!response.ok) throw new Error(submitted.detail || '抽取失败');
    const payload = await waitForBatchJob(submitted.job_id);
    renderResult(payload);
    showStatus('抽取完成，可以核对结果并下载');
  } catch (error) {
    showStatus(error.message || String(error), true);
  } finally {
    document.querySelector('#api-key').value = '';
    submitButton.disabled = false;
  }
});

Promise.all([loadScenario(), loadSettings()]).catch(error => showStatus(`配置加载失败：${error.message}`, true));
