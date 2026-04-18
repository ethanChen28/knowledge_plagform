const state = {
  summary: null,
  knowledgeBases: [],
  selectedId: null,
  selectedKnowledgeBase: null,
  documents: [],
  pollTimer: null,
};

const elements = {
  kbCount: document.getElementById('kb-count'),
  kbList: document.getElementById('knowledge-base-list'),
  createForm: document.getElementById('create-kb-form'),
  refreshButton: document.getElementById('refresh-kbs'),
  selectedName: document.getElementById('selected-kb-name'),
  selectedDescription: document.getElementById('selected-kb-description'),
  selectedMeta: document.getElementById('selected-kb-meta'),
  deleteButton: document.getElementById('delete-kb'),
  uploadForm: document.getElementById('upload-form'),
  uploadFiles: document.getElementById('upload-files'),
  uploadStatus: document.getElementById('upload-status'),
  queryForm: document.getElementById('query-form'),
  queryMode: document.getElementById('query-mode'),
  queryStatus: document.getElementById('query-status'),
  queryAnswer: document.getElementById('query-answer'),
  documentCount: document.getElementById('document-count'),
  documentList: document.getElementById('document-list'),
  serviceHealth: document.getElementById('service-health'),
  serviceHealthDetail: document.getElementById('service-health-detail'),
  storageSummary: document.getElementById('storage-summary'),
  storageDetail: document.getElementById('storage-detail'),
  modelSummary: document.getElementById('model-summary'),
  modelDetail: document.getElementById('model-detail'),
  toastRegion: document.getElementById('toast-region'),
};

async function request(path, options = {}) {
  const response = await fetch(path, options);
  const text = await response.text();
  let payload = null;

  if (text) {
    try {
      payload = JSON.parse(text);
    } catch (error) {
      payload = text;
    }
  }

  if (!response.ok) {
    const detail = typeof payload === 'object' && payload && 'detail' in payload ? payload.detail : text || `HTTP ${response.status}`;
    throw new Error(String(detail));
  }

  return payload;
}

function showToast(message, tone = 'info') {
  const toast = document.createElement('div');
  toast.className = 'toast';
  toast.dataset.tone = tone;
  toast.textContent = message;
  elements.toastRegion.appendChild(toast);
  window.setTimeout(() => toast.remove(), 3600);
}

function setPillStatus(element, tone, text) {
  element.className = 'pill';
  if (tone) {
    element.classList.add(tone);
  }
  element.textContent = text;
}

function setFormBusy(form, busy) {
  const controls = form.querySelectorAll('button, input, textarea, select');
  controls.forEach((control) => {
    control.disabled = busy;
  });
}

function formatDate(value) {
  if (!value) {
    return '未记录';
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return date.toLocaleString('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  });
}

function formatBytes(bytes) {
  if (!Number.isFinite(bytes) || bytes < 0) {
    return '-';
  }
  const units = ['B', 'KB', 'MB', 'GB'];
  let value = bytes;
  let unitIndex = 0;
  while (value >= 1024 && unitIndex < units.length - 1) {
    value /= 1024;
    unitIndex += 1;
  }
  return `${value.toFixed(value >= 10 || unitIndex === 0 ? 0 : 1)} ${units[unitIndex]}`;
}

function renderKnowledgeBases() {
  elements.kbCount.textContent = String(state.knowledgeBases.length);
  elements.kbList.innerHTML = '';

  if (state.knowledgeBases.length === 0) {
    elements.kbList.innerHTML = '<div class="empty-state">还没有知识库，先在左侧创建一个。</div>';
    return;
  }

  state.knowledgeBases.forEach((item) => {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = `kb-item ${item.id === state.selectedId ? 'active' : ''}`;
    button.innerHTML = `
      <div class="kb-item-header">
        <strong>${item.name}</strong>
        <span class="pill">${item.documents.length}</span>
      </div>
      <p>${item.description || '暂无描述'}</p>
      <p>更新于 ${formatDate(item.updated_at)}</p>
    `;
    button.addEventListener('click', async () => {
      state.selectedId = item.id;
      renderKnowledgeBases();
      await loadSelectedKnowledgeBase();
    });
    elements.kbList.appendChild(button);
  });
}

function renderSelectedKnowledgeBase() {
  const kb = state.selectedKnowledgeBase;
  const documents = state.documents;

  if (!kb) {
    elements.selectedName.textContent = '请选择一个知识库';
    elements.selectedDescription.textContent = '左侧创建或选择一个知识库后，这里会显示详情、文档列表和问答入口。';
    elements.selectedMeta.innerHTML = '';
    elements.documentCount.textContent = '0';
    elements.documentList.innerHTML = '<div class="empty-state">当前没有可展示的文档。</div>';
    elements.deleteButton.disabled = true;
    elements.uploadForm.querySelector('button').disabled = true;
    elements.queryForm.querySelector('button').disabled = true;
    elements.queryAnswer.textContent = '等待检索结果';
    return;
  }

  elements.selectedName.textContent = kb.name;
  elements.selectedDescription.textContent = kb.description || '暂无描述';
  elements.deleteButton.disabled = false;
  elements.uploadForm.querySelector('button').disabled = false;
  elements.queryForm.querySelector('button').disabled = false;

  const metaCards = [
    ['知识库 ID', kb.id],
    ['创建时间', formatDate(kb.created_at)],
    ['更新时间', formatDate(kb.updated_at)],
    ['文档数量', String(documents.length)],
  ];

  elements.selectedMeta.innerHTML = metaCards
    .map(([label, value]) => `<div class="meta-card"><span>${label}</span><strong>${value}</strong></div>`)
    .join('');

  elements.documentCount.textContent = String(documents.length);
  if (documents.length === 0) {
    elements.documentList.innerHTML = '<div class="empty-state">还没有上传文档。</div>';
    return;
  }

  elements.documentList.innerHTML = documents
    .map((doc) => {
      const tone = doc.status === 'completed' ? 'success' : doc.status === 'failed' ? 'error' : 'warning';
      return `
        <article class="document-item">
          <div class="document-header">
            <strong>${doc.original_filename}</strong>
            <span class="pill ${tone}">${doc.status}</span>
          </div>
          <p>大小 ${formatBytes(doc.size_bytes)} · 更新时间 ${formatDate(doc.updated_at)}</p>
          <p>${doc.error_message || (doc.doc_id ? `文档 ID: ${doc.doc_id}` : '等待后台处理')}</p>
        </article>
      `;
    })
    .join('');
}

function renderSummary() {
  if (!state.summary) {
    return;
  }

  elements.storageSummary.textContent = `${state.summary.vector_storage || '未设置'} / ${state.summary.graph_storage || '未设置'}`;
  elements.storageDetail.textContent = `默认检索模式 ${state.summary.default_query_mode}`;

  elements.modelSummary.textContent = state.summary.llm_model;
  elements.modelDetail.textContent = `视觉 ${state.summary.vision_model || '关闭'} · 向量 ${state.summary.embedding_model}`;
}

async function loadHealth() {
  try {
    const payload = await request('/health');
    elements.serviceHealth.textContent = payload.status === 'ok' ? '在线' : '异常';
    elements.serviceHealthDetail.textContent = '应用容器已响应健康检查';
  } catch (error) {
    elements.serviceHealth.textContent = '不可用';
    elements.serviceHealthDetail.textContent = error.message;
  }
}

async function loadSummary() {
  state.summary = await request('/api/v1/system-summary');
  renderSummary();
}

async function loadKnowledgeBases(options = {}) {
  const keepSelection = options.keepSelection !== false;
  state.knowledgeBases = await request('/api/v1/knowledge-bases');

  if (!keepSelection || !state.knowledgeBases.some((item) => item.id === state.selectedId)) {
    state.selectedId = state.knowledgeBases[0]?.id || null;
  }

  renderKnowledgeBases();
  await loadSelectedKnowledgeBase({ quiet: true });
}

async function loadSelectedKnowledgeBase(options = {}) {
  const quiet = options.quiet === true;

  if (!state.selectedId) {
    state.selectedKnowledgeBase = null;
    state.documents = [];
    renderSelectedKnowledgeBase();
    resetPoller();
    return;
  }

  try {
    const [knowledgeBase, documents] = await Promise.all([
      request(`/api/v1/knowledge-bases/${state.selectedId}`),
      request(`/api/v1/knowledge-bases/${state.selectedId}/documents`),
    ]);
    state.selectedKnowledgeBase = knowledgeBase;
    state.documents = documents;
    renderSelectedKnowledgeBase();
    ensurePoller();
  } catch (error) {
    if (!quiet) {
      showToast(`加载知识库失败：${error.message}`, 'error');
    }
  }
}

function ensurePoller() {
  resetPoller();
  state.pollTimer = window.setInterval(() => {
    loadSelectedKnowledgeBase({ quiet: true }).catch(() => undefined);
  }, 6000);
}

function resetPoller() {
  if (state.pollTimer) {
    window.clearInterval(state.pollTimer);
    state.pollTimer = null;
  }
}

async function handleCreateKnowledgeBase(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const formData = new FormData(form);
  const name = String(formData.get('name') || '').trim();
  const description = String(formData.get('description') || '').trim();
  const rawOverrides = String(formData.get('config_overrides') || '').trim();

  let configOverrides = {};
  if (rawOverrides) {
    try {
      configOverrides = JSON.parse(rawOverrides);
    } catch (error) {
      showToast('配置覆盖必须是合法 JSON', 'error');
      return;
    }
  }

  setFormBusy(form, true);
  try {
    const created = await request('/api/v1/knowledge-bases', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        name,
        description: description || null,
        config_overrides: configOverrides,
      }),
    });

    form.reset();
    state.selectedId = created.id;
    await loadKnowledgeBases({ keepSelection: true });
    showToast(`知识库 ${created.name} 已创建`, 'success');
  } catch (error) {
    showToast(`创建失败：${error.message}`, 'error');
  } finally {
    setFormBusy(form, false);
  }
}

async function handleDeleteKnowledgeBase() {
  if (!state.selectedKnowledgeBase) {
    return;
  }
  const confirmDelete = window.confirm(`确认删除知识库“${state.selectedKnowledgeBase.name}”？这会同时清理其数据目录。`);
  if (!confirmDelete) {
    return;
  }

  elements.deleteButton.disabled = true;
  try {
    await request(`/api/v1/knowledge-bases/${state.selectedKnowledgeBase.id}?purge_data=true`, {
      method: 'DELETE',
    });
    showToast(`已删除 ${state.selectedKnowledgeBase.name}`, 'success');
    state.selectedId = null;
    await loadKnowledgeBases({ keepSelection: false });
  } catch (error) {
    showToast(`删除失败：${error.message}`, 'error');
  } finally {
    elements.deleteButton.disabled = false;
  }
}

async function handleUpload(event) {
  event.preventDefault();
  if (!state.selectedKnowledgeBase) {
    showToast('请先选择知识库', 'error');
    return;
  }

  const files = elements.uploadFiles.files;
  if (!files || files.length === 0) {
    showToast('请选择至少一个文件', 'error');
    return;
  }

  setFormBusy(elements.uploadForm, true);
  setPillStatus(elements.uploadStatus, 'warning', '上传中');

  try {
    const payload = new FormData();
    Array.from(files).forEach((file) => {
      payload.append('files', file);
    });

    const result = await request(`/api/v1/knowledge-bases/${state.selectedKnowledgeBase.id}/documents/upload`, {
      method: 'POST',
      body: payload,
    });

    elements.uploadForm.reset();
    await loadKnowledgeBases({ keepSelection: true });
    setPillStatus(elements.uploadStatus, 'success', `已接收 ${result.accepted_count} 个文件`);
    showToast(`已提交 ${result.accepted_count} 个文件进入后台处理`, 'success');
  } catch (error) {
    setPillStatus(elements.uploadStatus, 'error', '上传失败');
    showToast(`上传失败：${error.message}`, 'error');
  } finally {
    setFormBusy(elements.uploadForm, false);
  }
}

async function handleQuery(event) {
  event.preventDefault();
  if (!state.selectedKnowledgeBase) {
    showToast('请先选择知识库', 'error');
    return;
  }

  const formData = new FormData(event.currentTarget);
  const payload = {
    query: String(formData.get('query') || '').trim(),
    mode: String(formData.get('mode') || '').trim() || null,
    system_prompt: String(formData.get('system_prompt') || '').trim() || null,
  };

  setFormBusy(elements.queryForm, true);
  setPillStatus(elements.queryStatus, 'warning', '检索中');
  elements.queryAnswer.textContent = '正在向知识库发起检索，请稍候...';

  try {
    const result = await request(`/api/v1/knowledge-bases/${state.selectedKnowledgeBase.id}/query`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(payload),
    });

    elements.queryAnswer.textContent = result.answer || '未返回内容';
    setPillStatus(elements.queryStatus, 'success', result.mode);
  } catch (error) {
    elements.queryAnswer.textContent = `查询失败：${error.message}`;
    setPillStatus(elements.queryStatus, 'error', '查询失败');
    showToast(`查询失败：${error.message}`, 'error');
  } finally {
    setFormBusy(elements.queryForm, false);
  }
}

async function bootstrap() {
  renderSelectedKnowledgeBase();
  elements.createForm.addEventListener('submit', handleCreateKnowledgeBase);
  elements.refreshButton.addEventListener('click', () => {
    loadKnowledgeBases({ keepSelection: true }).catch((error) => showToast(`刷新失败：${error.message}`, 'error'));
  });
  elements.deleteButton.addEventListener('click', handleDeleteKnowledgeBase);
  elements.uploadForm.addEventListener('submit', handleUpload);
  elements.queryForm.addEventListener('submit', handleQuery);

  try {
    await Promise.all([loadHealth(), loadSummary(), loadKnowledgeBases({ keepSelection: true })]);
  } catch (error) {
    showToast(`初始化失败：${error.message}`, 'error');
  }
}

window.addEventListener('beforeunload', resetPoller);
window.addEventListener('DOMContentLoaded', () => {
  bootstrap().catch((error) => {
    showToast(`页面启动失败：${error.message}`, 'error');
  });
});
