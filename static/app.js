'use strict';

const $ = (selector, root = document) => root.querySelector(selector);
const state = { view: 'repositories', overview: null, detail: null, path: '', tab: 'files', setup: false, generation: 0 };
const titles = { settings: ['系统设置', '管理仓库保存位置与客户端连接地址。', '刷新设置'], repositories: ['仓库管理', '让每一次修改，都有迹可循。', '＋ 新建仓库'], users: ['SVN 账户', '管理用于 SVN 客户端连接和提交的独立账户。', '＋ 新建账户'], backups: ['仓库备份', '保存完整版本历史，为你的项目多一份保障。', '刷新备份'] };
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const segment = value => encodeURIComponent(value);
const appUrl = path => {
  const url = new URL(path.replace(/^\//, ''), document.baseURI);
  return url.pathname + url.search;
};
const repoApi = name => `/api/repositories/${segment(name)}`;
const content = () => $('#view-content');
let toastTimer;

const iconPaths = {"grid":"<rect x=\"3\" y=\"3\" width=\"7\" height=\"7\" rx=\"1\"/><rect x=\"14\" y=\"3\" width=\"7\" height=\"7\" rx=\"1\"/><rect x=\"3\" y=\"14\" width=\"7\" height=\"7\" rx=\"1\"/><rect x=\"14\" y=\"14\" width=\"7\" height=\"7\" rx=\"1\"/>","users":"<circle cx=\"9\" cy=\"8\" r=\"3\"/><path d=\"M3 21v-3a6 6 0 0 1 12 0v3M16 5a3 3 0 0 1 0 6m2 3a5 5 0 0 1 3 5v2\"/>","folder":"<path d=\"M3 7a2 2 0 0 1 2-2h5l2 3h7a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z\"/>","backup":"<path d=\"M3 11a9 9 0 1 1 3 8M3 4v7h7M12 7v5l3 2\"/>","settings":"<path d=\"m9 3-1 3-3 1-2 4 2 2v4l4 2 3-1 3 1 4-2v-4l2-2-2-4-3-1-1-3Z\"/><circle cx=\"12\" cy=\"11\" r=\"3\"/>","search":"<circle cx=\"10.5\" cy=\"10.5\" r=\"6.5\"/><path d=\"m16 16 5 5\"/>","link":"<path d=\"m10 13 4-4M8 16l-1 1a4 4 0 0 1-6-6l5-5a4 4 0 0 1 6 0m0 2 1-1a4 4 0 0 1 6 6l-5 5a4 4 0 0 1-6 0\"/>","copy":"<rect x=\"8\" y=\"8\" width=\"12\" height=\"13\" rx=\"2\"/><path d=\"M15 8V3H3v13h5\"/>","file":"<path d=\"M14 3H5v18h14V8ZM14 3v5h5M8 13h8M8 17h6\"/>","arrow":"<path d=\"M5 12h14m-5-5 5 5-5 5\"/>","shield":"<path d=\"m12 3 8 3v6c0 5-8 9-8 9s-8-4-8-9V6Z\"/><path d=\"m8 12 3 3 5-6\"/>"};
function icon(name) { return '<svg class="icon" viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">' + (iconPaths[name] || iconPaths.folder) + '</svg>'; }
document.querySelectorAll('[data-icon]').forEach(node => { node.innerHTML = icon(node.dataset.icon); });

function toast(message, error = false) {
  const node = $('#toast');
  node.textContent = message;
  node.classList.toggle('error', error);
  node.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { node.hidden = true; }, 4500);
}

async function api(url, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (options.method && options.method !== 'GET') headers['X-FnSVN-Request'] = '1';
  if (options.body !== undefined) headers['Content-Type'] = 'application/json';
  const response = await fetch(appUrl(url), { ...options, headers, credentials: 'same-origin', body: options.body === undefined ? undefined : JSON.stringify(options.body) });
  let data;
  try { data = await response.json(); } catch { data = {}; }
  if (!response.ok) {
    if (response.status === 401 && !['/api/login', '/api/setup'].includes(url)) showAuth(false);
    throw new Error(data.error || `请求失败（${response.status}）`);
  }
  return data;
}

function showAuth(setup) {
  state.setup = setup;
  $('#auth-form').querySelectorAll('input').forEach(input => { input.disabled = false; });
  state.generation++;
  $('#app').hidden = true;
  $('#loading-screen').hidden = true;
  $('#auth-screen').hidden = false;
  if ($('#modal').open) $('#modal').close();
  $('#auth-title').textContent = setup ? '开始你的版本旅程' : '欢迎回来';
  $('#auth-description').textContent = setup ? '首次使用，请先创建一个 Web 管理员账户。' : '登录 Web 管理控制台，管理你的版本资产。';
  $('#auth-submit').innerHTML = setup ? '创建管理员账户 <span>→</span>' : '登录控制台 <span>→</span>';
  const password = $('#auth-form input[name="password"]');
  password.minLength = setup ? 12 : 1;
  password.maxLength = 128;
  password.autocomplete = setup ? 'new-password' : 'current-password';
  password.placeholder = setup ? '12–128 个字符，首尾不能有空格' : '请输入密码';
  $('#auth-error').textContent = '';
}

async function startApp() {
  const overview = await api('/api/overview');
  state.overview = overview;
  $('#auth-form').reset();
  $('#auth-form').querySelectorAll('input').forEach(input => { input.value = ''; input.disabled = true; });
  $('#auth-screen').hidden = true;
  $('#loading-screen').hidden = true;
  $('#app').hidden = false;
  updateStatus();
  await navigate('repositories');
}

function updateStatus() {
  const data = state.overview;
  $('#repo-count').textContent = data.repositories.length;
  $('#logout').hidden = data.webAuthRequired === false;
  $('#service-dot').classList.toggle('offline', !data.svnAvailable);
  $('#service-status').innerHTML = `${data.svnAvailable ? 'SVN 工具已就绪' : 'SVN 工具不可用'}<small>SVN · 端口 ${esc(data.svnPort || 3690)}</small>`;
  $('#service-warning').hidden = !!data.svnAvailable;
}

async function refreshOverview() {
  state.overview = await api('/api/overview');
  updateStatus();
}

async function navigate(view) {
  if (state.repositoryBusy) { toast('正在处理仓库，请等待完成'); return; }
  if (state.settingsSaving) { toast('正在保存设置，请等待完成'); return; }
  const generation = ++state.generation;
  state.view = view;
  state.detail = null;
  const [title, description, action] = titles[view];
  $('#page-title').textContent = title;
  $('#breadcrumb-current').textContent = title;
  $('#page-description').textContent = description;
  $('#page-action').textContent = action;
  $('#global-search').value = '';
  $('#page-action').hidden = view === 'settings';
  document.querySelectorAll('.nav-item').forEach(node => node.classList.toggle('active', node.dataset.view === view));
  if (view === 'repositories') renderRepositories();
  else if (view === 'users') renderUsers();
  else if (view === 'settings') {
    content().innerHTML = '<div class="inline-loading"><span class="spinner"></span>正在读取设置…</div>';
    try { const data = await api('/api/settings'); if (generation === state.generation) renderSettings(data); }
    catch (error) { if (generation === state.generation) renderError(error); }
  }
  else {
    content().innerHTML = '<div class="inline-loading"><span class="spinner"></span>正在读取备份…</div>';
    try {
      const data = await api('/api/backups');
      if (generation === state.generation) renderBackups(data.backups);
    } catch (error) { if (generation === state.generation) renderError(error); }
  }
}

function svnServerUrl() {
  const rawHost = (state.overview.svnHost || location.hostname).replace(/^\[|\]$/g, '');
  const hostname = rawHost.includes(':') ? `[${rawHost}]` : rawHost;
  const port = Number(state.overview.svnPort || 3690);
  return `svn://${hostname}${port !== 3690 ? `:${port}` : ''}`;
}

function svnUrl(name) {
  return `${svnServerUrl()}/${encodeURIComponent(name)}`;
}

function urlBox(name) {
  const url = svnUrl(name);
  return `<div class="repo-url"><code title="${esc(url)}">${esc(url)}</code><button class="copy-button" data-copy="${esc(url)}" title="复制 SVN 地址" aria-label="复制 SVN 地址">${icon("copy")}<span>复制地址</span></button></div>`;
}

function emptyState(symbol, title, description, action = '') {
  return `<div class="empty-state"><span class="empty-symbol">${icon(symbol === "♙" ? "users" : symbol === "◷" ? "backup" : "folder")}</span><h3>${title}</h3><p class="muted">${description}</p>${action}</div>`;
}

function storageInfo(data) {
  return `<div class="storage-bar"><div class="storage-location"><span class="storage-label">仓库存放目录</span><code id="repository-storage-path">${esc(data.repositoriesPath || '未提供目录信息')}</code></div><div class="storage-port"><span class="storage-label">SVN 端口</span><strong id="active-svn-port">${esc(data.svnPort || 3690)}</strong></div><button class="text-button" data-view="settings">设置保存目录 ${icon("arrow")}</button></div>`;
}

function renderRepositories() {
  const data = state.overview;
  content().innerHTML = `<div class="stats-grid"><div class="stat-card"><div><p>版本仓库</p><strong>${data.repositories.length}</strong></div><span class="stat-icon">${icon("folder")}</span></div><div class="stat-card"><div><p>SVN 账户</p><strong>${data.users.length}</strong></div><span class="stat-icon">${icon("users")}</span></div><div class="stat-card"><div><p>连接协议</p><strong class="text-value server-address" title="${esc(svnServerUrl())}">${esc(svnServerUrl())}</strong></div><span class="stat-icon">${icon("link")}</span></div></div><div class="section-toolbar"><h2>我的仓库 <span>${data.repositories.length} 个仓库</span></h2><div class="repository-tools"><button class="button small" data-action="scan-repositories">扫描已有仓库</button><label class="search-box"><input id="repo-search" type="search" aria-label="搜索仓库" placeholder="搜索仓库名称…"></label></div></div><div class="repo-grid" id="repository-list"></div><div class="notice">连接你的工作流：复制仓库地址，在 TortoiseSVN、命令行或其他 SVN 客户端中检出。使用独立的 SVN 账户进行身份验证。</div>`;
  renderRepositoryCards('');
  $('.stats-grid').insertAdjacentHTML('afterend', storageInfo(data));
  $('#repo-search').addEventListener('input', event => { $('#global-search').value = event.target.value; renderRepositoryCards(event.target.value); });
  content().insertAdjacentHTML('beforeend', authHelp());
}

function renderRepositoryCards(filter) {
  const repos = state.overview.repositories.filter(repo => repo.name.toLowerCase().includes(filter.toLowerCase()));
  const target = $('#repository-list');
  if (!repos.length) {
    target.innerHTML = emptyState('▱', filter ? '没有匹配的仓库' : '从第一个仓库开始', filter ? '试试其他名称。' : '为项目创建专属空间，记录每一个版本。', filter ? '' : '<button class="button primary" data-action="create-repo">＋ 新建仓库</button>');
    target.classList.add('is-empty');
    return;
  }
  target.classList.remove('is-empty');
  target.innerHTML = repos.map(repo => `<article class="repo-card" tabindex="0" role="link" data-card-repo="${esc(repo.name)}" aria-label="管理仓库 ${esc(repo.name)}"><div class="repo-card-top"><span class="folder-icon">${icon("folder")}</span><span class="revision-badge">r${esc(repo.revision)}</span></div><h3>${esc(repo.name)}</h3><p class="repo-description">SUBVERSION REPOSITORY</p>${urlBox(repo.name)}<div class="repo-actions"><button class="text-button" data-repo="${esc(repo.name)}">浏览仓库 <span>→</span></button><button class="text-button" data-permissions="${esc(repo.name)}">访问权限</button><button class="text-button" data-rename-repo="${esc(repo.name)}">改名</button><button class="text-button danger-text" data-delete-repo="${esc(repo.name)}">删除</button></div></article>`).join('');
}

function renderUsers() {
  const users = state.overview.users;
  content().innerHTML = `<div class="notice">SVN 账户用于客户端检出、更新和提交，与 Web 管理员账户独立。创建账户后，请在仓库的「访问权限」中授予读取或读写权限。</div><div class="section-toolbar"><h2>客户端账户 <span>${users.length} 个账户</span></h2><span class="muted">按仓库独立授权</span></div>${users.length ? `<div class="data-panel table-scroll"><table><thead><tr><th>账户名称</th><th>账户类型</th><th>操作</th></tr></thead><tbody>${users.map(user => `<tr><td><span class="user-symbol">${esc(user.name.slice(0, 1).toUpperCase())}</span><strong>${esc(user.name)}</strong></td><td><span class="tag">SVN 客户端</span></td><td><button class="text-button user-password-button" data-reset-password="${esc(user.name)}">修改密码</button><button class="text-button" data-delete-user="${esc(user.name)}">删除账户</button></td></tr>`).join('')}</tbody></table></div>` : emptyState('♙', '还没有 SVN 账户', '为需要访问仓库的成员创建账户。', '<button class="button primary" data-action="create-user">＋ 新建账户</button>')}`;
}

function formatSize(size) {
  const value = Number(size);
  if (!Number.isFinite(value)) return '—';
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / 1024 / 1024).toFixed(1)} MB`;
}

function renderBackups(backups) {
  content().innerHTML = `<div class="notice">在仓库详情中创建备份。备份文件包含仓库的 SVN dump 版本历史，可下载到其他设备保存；备份不包含账户密码和访问权限配置。</div><div class="section-toolbar"><h2>备份文件 <span>${backups.length} 个文件</span></h2></div>${backups.length ? `<div class="data-panel table-scroll"><table><thead><tr><th>文件名称</th><th>文件大小</th><th>操作</th></tr></thead><tbody>${backups.map(backup => `<tr><td><strong>${esc(backup.filename)}</strong></td><td>${formatSize(backup.size)}</td><td><a class="button small" href="${appUrl(`/api/backups/${segment(backup.filename)}`)}" download>下载备份</a></td></tr>`).join('')}</tbody></table></div>` : emptyState('◷', '尚无备份', '打开一个仓库，点击「创建备份」保存完整版本历史。')}`;
}

async function openRepository(name, tab = 'files', path = '') {
  if (state.repositoryBusy) { toast('正在处理仓库，请等待完成'); return; }
  const generation = ++state.generation;
  state.path = path;
  state.tab = tab;
  content().innerHTML = '<div class="inline-loading"><span class="spinner"></span>正在读取仓库…</div>';
  try {
    const detail = await api(`${repoApi(name)}?path=${encodeURIComponent(path)}`);
    if (generation !== state.generation) return;
    state.detail = detail;
    $('#breadcrumb-current').textContent = `仓库管理 / ${detail.name}`;
    renderDetail();
  } catch (error) { if (generation === state.generation) renderError(error); }
}

function renderDetail() {
  const detail = state.detail;
  content().innerHTML = `<div class="detail-toolbar"><div class="detail-heading"><span class="folder-icon">${icon("folder")}</span><div><h2>${esc(detail.name)}</h2><div class="detail-meta"><span>最新版本 r${esc(detail.revision)}</span><span>UUID ${esc(detail.uuid)}</span></div></div></div><div class="detail-actions"><button class="button small" data-action="back">← 所有仓库</button><button class="button small" data-backup="${esc(detail.name)}">创建备份</button><button class="button small" data-rename-repo="${esc(detail.name)}">改名</button><button class="button small danger" data-delete-repo="${esc(detail.name)}">删除仓库</button></div></div><div class="detail-url">${urlBox(detail.name)}</div><div class="tabs"><button class="tab ${state.tab === 'files' ? 'active' : ''}" data-tab="files">文件浏览</button><button class="tab ${state.tab === 'log' ? 'active' : ''}" data-tab="log">提交日志</button><button class="tab ${state.tab === 'activity' ? 'active' : ''}" data-tab="activity">提交热力图</button><button class="tab ${state.tab === 'permissions' ? 'active' : ''}" data-tab="permissions">访问权限</button></div><div id="detail-content"></div>`;
  if (state.tab === 'files') renderFiles();
  else if (state.tab === 'log') renderLog();
  else if (state.tab === 'activity') renderActivity();
  else renderPermissions();
}

function renderFiles() {
  const entries = state.detail.entries || [];
  const parts = state.path.split('/').filter(Boolean);
  const breadcrumbs = ['<button class="path-button" data-path="">根目录</button>', ...parts.map((part, index) => `<span> / </span><button class="path-button" data-path="${esc(parts.slice(0, index + 1).join('/'))}">${esc(part)}</button>`)];
  $('#detail-content').innerHTML = `<div class="data-panel"><div class="path-bar">${breadcrumbs.join('')}</div><div class="table-scroll"><table><thead><tr><th>名称</th><th>类型</th></tr></thead><tbody>${state.path ? `<tr><td><button class="entry-name" data-path="${esc(parts.slice(0, -1).join('/'))}"><span class="entry-kind">${icon("arrow")}</span> 返回上一级</button></td><td>目录</td></tr>` : ''}${entries.map(entry => `<tr><td>${entry.kind === 'dir' ? `<button class="entry-name" data-path="${esc([...parts, entry.name.replace(/\/$/, '')].join('/'))}"><span class="entry-kind">${icon("folder")}</span>${esc(entry.name)}</button>` : `<span class="entry-name"><span class="entry-kind">${icon("file")}</span>${esc(entry.name)}</span>`}</td><td>${entry.kind === 'dir' ? '目录' : '文件'}</td></tr>`).join('')}${!entries.length ? '<tr><td class="table-empty" colspan="2">此目录暂无文件。通过 SVN 客户端提交后，即可在此浏览。</td></tr>' : ''}</tbody></table></div></div><p class="field-help browser-help">此处提供目录浏览。文件下载、修改与提交请使用 SVN 客户端。</p>`;
}

function formatDate(date) {
  if (!date) return '—';
  const parsed = new Date(date);
  return Number.isNaN(parsed.getTime()) ? date : parsed.toLocaleString('zh-CN', { hour12: false });
}

function renderLog() {
  const panel = $('#detail-content');
  panel.innerHTML = `<form id="history-filter" class="data-panel history-filter"><div class="history-filter-heading"><h3>筛选提交</h3><span>按作者和日期查找提交记录</span></div><div class="history-filter-fields"><label>提交作者<input name="author" placeholder="输入作者名称" autocomplete="off"></label><label>开始日期<input name="from" type="date"></label><label>结束日期<input name="to" type="date"></label><div class="history-filter-actions"><button type="submit" class="button primary">搜索</button><button type="button" class="button" data-history-reset>重置</button></div></div></form><div id="history-results"></div><div id="history-detail"></div>`;
  const form = $('#history-filter', panel), results = $('#history-results', panel), detail = $('#history-detail', panel);
  let pages = [], page = 0, request = 0;
  async function load(before = null) {
    const current = ++request; results.textContent = '正在读取提交历史…';
    const params = new URLSearchParams(new FormData(form)); if (before !== null) params.set('before', before);
    try {
      const data = await api(`${repoApi(state.detail.name)}/history?${params}`);
      if (current !== request || !panel.isConnected) return;
      pages[page] = {before, next: data.nextBefore};
      results.innerHTML = `<div class="data-panel table-scroll"><table><thead><tr><th>版本</th><th>提交人</th><th>时间</th><th>提交说明</th></tr></thead><tbody>${data.entries.map(log => `<tr><td><button class="text-button" data-history-revision="${log.revision}">r${log.revision}</button></td><td>${esc(log.author || '—')}</td><td>${esc(formatDate(log.date))}</td><td>${esc(log.message || '未填写提交说明')}</td></tr>`).join('') || '<tr><td colspan="4">没有匹配的提交。</td></tr>'}</tbody></table><button class="button small" data-history-prev ${page === 0 ? 'disabled' : ''}>上一页</button> 第 ${page + 1} 页 <button class="button small" data-history-next ${data.nextBefore === null ? 'disabled' : ''}>下一页</button></div>`;
      $('[data-history-prev]', results).onclick = () => { page--; load(pages[page].before); };
      $('[data-history-next]', results).onclick = () => { page++; load(data.nextBefore); };
      results.querySelectorAll('[data-history-revision]').forEach(button => button.onclick = async () => {
        const revision = Number(button.dataset.historyRevision); detail.textContent = '正在读取提交文件…';
        try {
          const files = await api(`${repoApi(state.detail.name)}/history/${revision}`); if (!panel.isConnected) return;
          detail.innerHTML = `<section class="data-panel"><h3>r${revision} 的变更文件</h3>${files.files.map(file => `<div>${esc(file.action)} ${esc(file.path)} ${file.kind !== 'dir' ? `<button class="text-button" data-compare-path="${esc(file.path)}">比较文本</button>` : ''}</div>`).join('') || '没有文件变更'}<p>比较版本：<input id="history-old" type="number" min="0" value="${revision - 1}" aria-label="原版本"> 至 <input id="history-new" type="number" min="0" value="${revision}" aria-label="新版本"></p><pre id="history-diff" style="white-space:pre-wrap;overflow-wrap:anywhere"></pre></section>`;
          detail.querySelectorAll('[data-compare-path]').forEach(compare => compare.onclick = async () => {
            const output = $('#history-diff', detail); output.textContent = '正在比较…';
            try { const q = new URLSearchParams({path: compare.dataset.comparePath, old: $('#history-old', detail).value, new: $('#history-new', detail).value}); const result = await api(`${repoApi(state.detail.name)}/compare?${q}`); output.textContent = result.diff || '没有文本差异'; } catch (error) { output.textContent = error.message; }
          });
        } catch (error) { detail.textContent = error.message; }
      });
    } catch (error) { if (current === request && panel.isConnected) results.textContent = error.message; }
  }
  function applyFilter() { pages = []; page = 0; detail.innerHTML = ''; load(); }
  form.onsubmit = event => { event.preventDefault(); applyFilter(); };
  $('[data-history-reset]', form).onclick = () => { form.reset(); applyFilter(); };
  load();
}

function renderActivity() {
  const panel = $('#detail-content');
  const repo = state.detail.name;
  panel.innerHTML = `<section class="data-panel activity-panel"><div class="activity-header"><div><h2>提交热力图</h2><p class="muted">最近 365 天的提交次数，按 UTC 日期统计。</p></div><label class="activity-author">提交人 <select aria-label="筛选提交人"><option value="">全部提交人</option></select></label></div><div class="activity-result" role="status">正在读取提交活动…</div></section>`;
  const select = $('.activity-author select', panel), result = $('.activity-result', panel);
  let request = 0;
  async function load() {
    const current = ++request, author = select.value;
    result.textContent = '正在读取提交活动…';
    try {
      const params = new URLSearchParams();
      if (author) params.set('author', author);
      const data = await api(`${repoApi(repo)}/activity${params.toString() ? `?${params}` : ''}`);
      if (!panel.isConnected || current !== request) return;
      select.innerHTML = `<option value="">全部提交人</option>${(data.authors || []).map(name => `<option value="${esc(name)}"${name === author ? ' selected' : ''}>${esc(name)}</option>`).join('')}`;
      const counts = new Map((data.entries || []).map(entry => [entry.date, entry.count]));
      const start = new Date(`${data.startDate}T00:00:00Z`), end = new Date(`${data.endDate}T00:00:00Z`);
      if (Number.isNaN(start.getTime()) || Number.isNaN(end.getTime()) || end < start || (end - start) / 86400000 > 366) throw new Error('提交活动日期范围无效');
      const cells = Array(start.getUTCDay()).fill('<span class="activity-spacer" aria-hidden="true"></span>');
      let total = 0;
      for (let time = start.getTime(); time <= end.getTime(); time += 86400000) {
        const day = new Date(time).toISOString().slice(0, 10), count = Number(counts.get(day)) || 0;
        total += count;
        const level = count === 0 ? 0 : count < 3 ? 1 : count < 6 ? 2 : count < 10 ? 3 : 4;
        const label = `${day}：${count} 次提交`;
        cells.push(`<span class="activity-cell activity-level-${level}" tabindex="0" title="${esc(label)}" aria-label="${esc(label)}"></span>`);
      }
      result.innerHTML = `<p class="activity-summary">${esc(data.startDate)} 至 ${esc(data.endDate)} · 共 ${total} 次提交${total ? '' : ' · 此期间暂无提交'}</p><div class="activity-scroll" role="region" aria-label="每日提交热力图，可横向滚动" tabindex="0"><div class="activity-grid">${cells.join('')}</div></div><div class="activity-legend"><span>较少</span>${[0, 1, 2, 3, 4].map(n => `<span class="activity-cell activity-level-${n}" aria-hidden="true"></span>`).join('')}<span>较多</span></div>`;
    } catch (error) {
      if (panel.isConnected && current === request) result.textContent = `无法读取提交活动：${error.message}`;
    }
  }
  select.addEventListener('change', load);
  load();
}

function renderPermissions() {
  const users = state.overview.users;
  const permissions = state.detail.permissions || {};
  const groups = structuredClone(state.detail.groups || {});
  const paths = structuredClone(state.detail.pathPermissions || {});
  const panel = $('#detail-content');
  const permOptions = value => `<option value=""${value === '' ? ' selected' : ''}>无权限</option><option value="r"${value === 'r' ? ' selected' : ''}>只读</option><option value="rw"${value === 'rw' ? ' selected' : ''}>读写</option>`;
  const principalLabel = name => name.startsWith('@') ? `用户组 ${name}` : `账户 ${name}`;
  panel.innerHTML = `<div class="notice">根目录权限适用于整个仓库；子目录可单独配置。未授权账户和匿名访问默认被拒绝。</div>
    <section class="access-card data-panel"><div class="access-card-heading"><div><h2>仓库根目录权限</h2><p>设置账户和用户组在仓库根目录的权限。</p></div></div>${users.length ? `<form id="permissions-form"><div class="table-scroll"><table><thead><tr><th>账户或用户组</th><th>权限</th></tr></thead><tbody>${users.map(u => `<tr><td><strong>${esc(u.name)}</strong><span class="access-badge">账户</span></td><td><select data-permission-user="${esc(u.name)}" aria-label="${esc(u.name)} 的根目录权限">${permOptions(permissions[u.name] || '')}</select></td></tr>`).join('')}${Object.keys(groups).map(g => `<tr><td><strong>@${esc(g)}</strong><span class="access-badge">用户组</span></td><td><select data-permission-user="@${esc(g)}" aria-label="${esc(g)} 用户组的根目录权限">${permOptions(permissions['@' + g] || '')}</select></td></tr>`).join('')}</tbody></table></div><div class="access-actions"><button class="button primary" type="submit">保存权限设置</button></div></form>` : `<div class="access-empty"><strong>先创建 SVN 账户</strong><br>添加账户后即可设置访问权限。 <button class="text-button" data-action="goto-users">管理 SVN 账户 →</button></div>`}</section>
    <form id="advanced-access" class="access-card data-panel"><div class="access-card-heading"><div><h2>用户组与子目录授权</h2><p>用户组在所有仓库共享；子目录权限仅应用于当前仓库。</p></div></div><div class="access-section"><div class="access-section-heading"><div><h3>用户组</h3><p>将账户加入用户组，便于统一授权。</p></div><button class="button small" type="button" data-add-group>＋ 新建用户组</button></div><div id="group-list"></div></div><div class="access-section"><div class="access-section-heading"><div><h3>子目录授权</h3><p>指定以 / 开头的仓库目录，以及账户或用户组的权限。</p></div><button class="button small" type="button" data-add-path>＋ 添加目录</button></div><div id="path-list"></div></div><div class="access-actions"><span class="access-unsaved">更改后请保存，才会影响实际访问权限。</span><button class="button primary" type="submit">保存用户组与目录授权</button></div></form>
    <form id="access-preview" class="access-card data-panel"><div class="access-card-heading"><div><h2>实际生效权限预览</h2><p>按已保存的配置查询，包含根目录、子目录与用户组规则。</p></div></div><div class="access-preview-fields"><label>SVN 账户<select name="user" required>${users.map(u => `<option value="${esc(u.name)}">${esc(u.name)}</option>`).join('')}</select></label><label>仓库路径<input name="path" value="/" placeholder="例如 /trunk" required></label><button class="button" type="submit"${users.length ? '' : ' disabled'}>查询权限</button></div><p id="access-result" class="access-result" role="status">输入路径后查询已保存的权限。</p></form>`;
  const advanced = $('#advanced-access', panel), groupList = $('#group-list', panel), pathList = $('#path-list', panel);
  function drawGroups() {
    const names = Object.keys(groups);
    groupList.innerHTML = names.length ? names.map(g => `<div class="access-item"><div class="access-item-head"><strong>@${esc(g)}</strong><button class="text-button danger-text" type="button" data-delete-group="${esc(g)}">删除用户组</button></div><div class="access-members">${users.length ? users.map(u => `<label class="access-member"><input type="checkbox" data-group="${esc(g)}" value="${esc(u.name)}"${groups[g].includes(u.name) ? ' checked' : ''}>${esc(u.name)}</label>`).join('') : '<span class="access-muted">暂无 SVN 账户</span>'}</div></div>`).join('') : '<div class="access-empty">还没有用户组。创建用户组后，可以为一组账户统一授权。</div>';
    groupList.querySelectorAll('[data-group]').forEach(input => input.addEventListener('change', () => { const members = groups[input.dataset.group]; if (input.checked) { if (!members.includes(input.value)) members.push(input.value); } else groups[input.dataset.group] = members.filter(u => u !== input.value); }));
    groupList.querySelectorAll('[data-delete-group]').forEach(button => button.addEventListener('click', () => { const group = button.dataset.deleteGroup; if (Object.keys(paths).some(path => Object.hasOwn(paths[path], '@' + group)) || Object.hasOwn(state.detail.permissions || {}, '@' + group)) { toast('该用户组仍有授权，请先移除其根目录和子目录授权并保存', true); return; } delete groups[group]; drawGroups(); drawPaths(); }));
  }
  function drawPaths() {
    const names = Object.keys(paths);
    pathList.innerHTML = names.length ? names.map(path => `<div class="access-item"><div class="access-item-head"><strong class="access-path">${esc(path)}</strong><button class="text-button danger-text" type="button" data-delete-path="${esc(path)}">移除目录</button></div><div class="access-rules">${Object.entries(paths[path]).map(([principal, permission]) => `<div class="access-rule"><span>${esc(principalLabel(principal))}</span><select data-rule-path="${esc(path)}" data-rule-principal="${esc(principal)}" aria-label="${esc(path)} 中 ${esc(principal)} 的权限">${permOptions(permission)}</select><button class="text-button danger-text" type="button" data-delete-rule-path="${esc(path)}" data-delete-rule-principal="${esc(principal)}">移除</button></div>`).join('') || '<div class="access-muted">尚未添加授权对象，将继承上级目录权限。</div>'}</div><div class="access-add-rule"><label>授权对象<select data-principal-for="${esc(path)}">${users.map(u => `<option value="${esc(u.name)}">账户 ${esc(u.name)}</option>`).join('')}${Object.keys(groups).map(g => `<option value="@${esc(g)}">用户组 @${esc(g)}</option>`).join('')}</select></label><button class="button small" type="button" data-add-rule="${esc(path)}"${users.length || Object.keys(groups).length ? '' : ' disabled'}>添加授权</button></div></div>`).join('') : '<div class="access-empty">还没有子目录授权。未单独设置的目录继承上级权限。</div>';
    pathList.querySelectorAll('[data-delete-path]').forEach(b => b.onclick = () => { delete paths[b.dataset.deletePath]; drawPaths(); });
    pathList.querySelectorAll('[data-rule-path]').forEach(s => s.onchange = () => { paths[s.dataset.rulePath][s.dataset.rulePrincipal] = s.value; });
    pathList.querySelectorAll('[data-delete-rule-path]').forEach(b => b.onclick = () => { delete paths[b.dataset.deleteRulePath][b.dataset.deleteRulePrincipal]; drawPaths(); });
    pathList.querySelectorAll('[data-add-rule]').forEach(b => b.onclick = () => { const path = b.dataset.addRule, select = [...pathList.querySelectorAll('[data-principal-for]')].find(s => s.dataset.principalFor === path), principal = select.value; if (Object.hasOwn(paths[path], principal)) return toast('该对象已有授权，请直接修改权限', true); paths[path][principal] = 'r'; drawPaths(); });
  }
  advanced.querySelector('[data-add-group]').onclick = () => openModal('新建用户组', '<label>用户组名称<input name="group" required maxlength="64" pattern="[A-Za-z][A-Za-z0-9_\\-]{0,63}" placeholder="例如 developers"></label><p class="field-help">以英文字母开头，可包含字母、数字、连字符和下划线。</p>', async values => { const group = values.group.trim(); if (Object.hasOwn(groups, group)) throw new Error('用户组已存在'); groups[group] = []; drawGroups(); drawPaths(); });
  advanced.querySelector('[data-add-path]').onclick = () => openModal('添加子目录', '<label>仓库目录路径<input name="path" required placeholder="例如 /trunk/project"></label><p class="field-help">从仓库根目录开始，以 / 开头。</p>', async values => { const path = values.path.trim(); if (!path.startsWith('/') || path === '/' || path.endsWith('/') || /[\[\]\\\r\n]/.test(path) || path.slice(1).split('/').some(x => !x || x === '.' || x === '..')) throw new Error('请输入有效的子目录路径，例如 /trunk/project'); if (Object.hasOwn(paths, path)) throw new Error('该目录已存在'); paths[path] = {}; drawPaths(); });
  advanced.addEventListener('submit', async event => { event.preventDefault(); const button = advanced.querySelector('[type="submit"]'); button.disabled = true; try { const rootValues = Object.fromEntries([...panel.querySelectorAll('[data-permission-user]')].map(s => [s.dataset.permissionUser, s.value])); await api(`${repoApi(state.detail.name)}/access-rules`, { method: 'PUT', body: {groups, paths} }); state.detail.groups = groups; state.detail.pathPermissions = paths; renderPermissions(); panel.querySelectorAll('[data-permission-user]').forEach(s => { if (Object.hasOwn(rootValues, s.dataset.permissionUser)) s.value = rootValues[s.dataset.permissionUser]; }); toast('用户组与子目录授权已保存'); } catch (error) { toast(error.message, true); } finally { button.disabled = false; } });
  $('#access-preview', panel).addEventListener('submit', async event => { event.preventDefault(); const output = $('#access-result', panel); output.textContent = '正在查询…'; try { const q = new URLSearchParams({user: event.target.elements.user.value, path: event.target.elements.path.value}); const r = await api(`${repoApi(state.detail.name)}/access-preview?${q}`); output.textContent = `实际权限：${r.permission === 'rw' ? '读写' : r.permission === 'r' ? '只读' : '无权限'} · 生效路径：${r.source}`; } catch (error) { output.textContent = error.message; } });
  const form = $('#permissions-form', panel);
  if (form) form.addEventListener('submit', async event => { event.preventDefault(); const button = form.querySelector('[type="submit"]'), values = Object.create(null); form.querySelectorAll('[data-permission-user]').forEach(select => { if (select.value) values[select.dataset.permissionUser] = select.value; }); button.disabled = true; try { await api(`${repoApi(state.detail.name)}/permissions`, {method: 'PUT', body: {permissions: values}}); state.detail.permissions = values; toast('仓库权限已保存'); } catch (error) { toast(error.message, true); } finally { button.disabled = false; } });
  drawGroups(); drawPaths();
}

function renderError(error) {
  content().innerHTML = emptyState('!', '暂时无法读取', esc(error.message), '<button class="button" data-action="refresh">重新加载</button>');
}

function openModal(title, body, onSubmit, accountEditor = false) {
  const container = accountEditor ? 'section role="group" aria-label="SVN 账户设置"' : 'form';
  const endTag = accountEditor ? 'section' : 'form';
  const submitType = accountEditor ? 'button' : 'submit';
  $('#modal-title').textContent = title;
  $('#modal-body').innerHTML = `<${container} id="modal-form">${body}<p class="form-error" id="modal-error" role="alert"></p><div class="modal-actions"><button type="button" class="button" data-close-modal>取消</button><button type="${submitType}" data-modal-submit class="button primary">确认创建</button></div></${endTag}>`;
  $('#modal').showModal();
  const form = $('#modal-form');
  let submitting = false;
  const handleSubmit = async event => {
    event.preventDefault();
    if (submitting || !form.isConnected || !$('#modal').open) return;
    if (![...form.querySelectorAll('input')].every(input => input.reportValidity())) return;
    submitting = true;
    const submit = $('[data-modal-submit]', form);
    submit.disabled = true;
    $('#modal-error').textContent = '';
    try {
      const values = accountEditor ? Object.fromEntries([...form.querySelectorAll('input[name]')].map(input => [input.name, input.value])) : Object.fromEntries(new FormData(form));
      await onSubmit(values);
      $('#modal').close();
    } catch (error) { $('#modal-error').textContent = error.message; }
    finally { submitting = false; submit.disabled = false; }
  };
  if (accountEditor) {
    $('[data-modal-submit]', form).addEventListener('click', handleSubmit);
    form.addEventListener('keydown', event => { if (event.key === 'Enter' && event.target.matches('input')) handleSubmit(event); });
  } else form.addEventListener('submit', handleSubmit);
}

function createRepository() {
  openModal('新建版本仓库', '<label>仓库名称<input name="name" placeholder="例如 my-project" required maxlength="64" pattern="[A-Za-z][A-Za-z0-9_\\-]{0,63}" autocomplete="off"></label><p class="field-help">以英文字母开头，可包含字母、数字、连字符和下划线，最多 64 个字符。创建后需为 SVN 账户设置访问权限。</p>', async values => {
    await api('/api/repositories', { method: 'POST', body: values });
    await refreshOverview();
    await navigate('repositories');
    toast('仓库已创建，请设置访问权限');
    await openRepository(values.name, 'permissions');
  });
}

function setRepositoryBusy(busy) {
  state.repositoryBusy = busy;
  $('#modal').querySelectorAll('button, input').forEach(node => { node.disabled = busy || node.hasAttribute('data-managed'); });
  $('#page-action').disabled = busy;
}

function renameRepository(name) {
  openModal('仓库改名', `<div class="notice warning">改名会更改 SVN 连接地址。已有工作副本请在 TortoiseSVN 中使用「重新定位 / Relocate」更新地址；版本历史和访问权限会保留。</div><label>新的仓库名称<input name="name" value="${esc(name)}" required maxlength="64" pattern="[A-Za-z][A-Za-z0-9_\\-]{0,63}" autocomplete="off"></label><p class="field-help">以英文字母开头，可包含字母、数字、连字符和下划线，最多 64 个字符。</p><p class="field-help" id="repository-rename-status" role="status"></p>`, async values => {
    const newName = values.name.trim();
    if (newName === name) throw new Error('请输入不同的新仓库名称');
    setRepositoryBusy(true);
    $('#repository-rename-status').textContent = '正在修改仓库名称，请勿关闭应用…';
    try {
      const result = await api(`${repoApi(name)}/rename`, { method: 'POST', body: { name: newName } });
      await refreshOverview();
      setRepositoryBusy(false);
      await openRepository(result.name);
      toast('仓库已改名，请使用新的连接地址');
    } finally { setRepositoryBusy(false); }
  });
  $('#modal-form [data-modal-submit]').textContent = '确认改名';
}

function deleteRepository(name) {
  openModal('删除仓库', `<div class="notice warning">此操作将暂停 SVN 服务，将「${esc(name)}」从服务移除并撤销账户授权。版本历史副本会保留在数据目录的 deleted 回收区；已有 dump 备份不受影响。</div><p class="muted">已有客户端将无法继续访问此仓库。请输入完整仓库名称以确认。</p><label class="delete-confirm-field">仓库名称<input name="confirmName" required autocomplete="off" placeholder="${esc(name)}"></label><p class="field-help" id="repository-delete-status" role="status"></p>`, async values => {
    if (values.confirmName !== name) throw new Error('仓库名称不一致，请输入完整名称');
    setRepositoryBusy(true);
    $('#repository-delete-status').textContent = '正在移除仓库并保留历史副本，请勿关闭应用…';
    try {
      const result = await api(repoApi(name), { method: 'DELETE', body: { confirmName: name } });
      await refreshOverview();
      setRepositoryBusy(false);
      await navigate('repositories');
      toast(`仓库已移除，历史副本保存在 ${result.archivedPath}${result.warning ? `。${result.warning}` : ''}`, !!result.warning);
      if (result.warning) content().insertAdjacentHTML('afterbegin', `<div class="notice warning" role="alert">${esc(result.warning)}。历史副本位置：${esc(result.archivedPath)}</div>`);
    } finally { setRepositoryBusy(false); }
  });
  const button = $('#modal-form button[type="submit"]');
  button.textContent = '确认删除仓库';
  button.className = 'button danger';
}

function scanRepositories() {
  $('#modal-title').textContent = '扫描本地已有仓库';
  $('#modal').classList.add('scan-dialog');
  $('#modal-body').innerHTML = `<form id="scan-form"><label>扫描目录<input name="path" required value="${esc(state.overview.repositoriesPath || '')}" placeholder="例如 /vol1/1000/old-svn"></label><p class="field-help">填写飞牛系统上的绝对路径，扫描本目录及 3 层子目录中的 SVN 服务端仓库。客户端工作副本（.svn）不能接管；应用需要读取目录的权限。</p><div class="modal-actions"><button type="button" class="button" data-close-modal>关闭</button><button type="submit" class="button primary">开始扫描</button></div></form><div id="scan-status" class="field-help scan-status" role="status"></div><div id="scan-results"></div>`;
  $('#modal').showModal();
  $('#scan-form').addEventListener('submit', async event => {
    event.preventDefault();
    if (state.repositoryBusy) return;
    const path = new FormData(event.currentTarget).get('path').trim();
    setRepositoryBusy(true);
    $('#scan-status').textContent = '正在扫描，请稍候…';
    $('#scan-results').innerHTML = '';
    try {
      const result = await api('/api/repositories/scan', { method: 'POST', body: { path, depth: 3 } });
      renderScanResults(result);
      $('#scan-status').textContent = `找到 ${result.repositories.length} 个仓库${result.truncated ? '，扫描已达到数量或目录限制，可缩小路径后继续扫描' : ''}。`;
    } catch (error) { $('#scan-status').textContent = `扫描失败：${error.message}`; }
    finally { setRepositoryBusy(false); }
  });
}

function renderScanResults(result) {
  const target = $('#scan-results');
  target.innerHTML = `${(result.warnings || []).length ? `<div class="notice warning">${result.warnings.map(esc).join('<br>')}</div>` : ''}<div class="notice">接管会复制仓库到当前保存目录，原仓库保留。接管后使用 FnSVN 账户认证，请重新授予访问权限；原仓库后续新增提交不会自动同步。</div>${result.repositories.length ? result.repositories.map((repo, index) => `<form class="scan-repository" data-scan-index="${index}"><div class="scan-repository-heading"><strong>${esc(repo.name)}</strong><span class="tag">${repo.managed ? '已纳入管理' : `r${esc(repo.revision)}`}</span></div><code class="scan-path">${esc(repo.path)}</code><p class="scan-uuid">UUID ${esc(repo.uuid)}</p><div class="scan-import-actions"><label>接管后的名称<input name="name" value="${esc(repo.suggestedName || repo.name)}" required maxlength="64" pattern="[A-Za-z][A-Za-z0-9_\\-]{0,63}" autocomplete="off" ${repo.managed ? 'disabled data-managed' : ''}></label><button class="button small primary" type="submit" ${repo.managed ? 'disabled data-managed' : ''}>${repo.managed ? '已管理' : '复制并接管'}</button></div><p class="field-help scan-row-status" role="status"></p></form>`).join('') : '<p class="muted">没有找到可识别的 SVN 服务端仓库。请检查目录是否正确，以及 FnSVN 是否有权限读取；工作副本目录不能用作服务器仓库。</p>'}`;
  target.querySelectorAll('form').forEach(form => form.addEventListener('submit', async event => {
    event.preventDefault();
    if (state.repositoryBusy) return;
    const repo = result.repositories[Number(form.dataset.scanIndex)];
    if (repo.managed) return;
    const name = new FormData(form).get('name').trim();
    const rowStatus = $('.scan-row-status', form);
    const button = $('button[type="submit"]', form);
    setRepositoryBusy(true);
    button.textContent = '正在复制…';
    rowStatus.textContent = '正在复制版本历史并接管，请勿关闭应用。SVN 服务可能短暂中断。';
    try {
      await api('/api/repositories/import', { method: 'POST', body: { path: repo.path, name } });
      repo.managed = true;
      form.querySelectorAll('input, button').forEach(node => node.setAttribute('data-managed', ''));
      button.textContent = '已接管';
      rowStatus.textContent = `已接管为 ${name}。请关闭此窗口后进入仓库「访问权限」，为 FnSVN 账户授权。`;
      await refreshOverview();
      if (state.view === 'repositories') renderRepositories();
    } catch (error) {
      rowStatus.textContent = `接管未完成：${error.message}`;
      button.textContent = '重试接管';
    } finally { setRepositoryBusy(false); }
  }));
}

function createUser() {
  openModal('新建 SVN 账户', '<label>账户名称<input name="svnAccountName" data-lpignore="true" data-1p-ignore placeholder="例如 developer" required maxlength="64" pattern="[A-Za-z][A-Za-z0-9_\\-]{0,63}" autocomplete="off"></label><p class="field-help">以英文字母开头，可包含字母、数字、连字符和下划线，最多 64 个字符。</p><label>账户密码<input name="svnAccountSecret" data-lpignore="true" data-1p-ignore type="password" autocomplete="new-password" placeholder="不限制长度，可使用短密码"></label><p class="field-help">此账户仅用于 SVN 客户端。创建后到仓库中配置访问权限。</p>', async values => {
    validatePassword(values.svnAccountSecret);
    if (state.overview.users.some(user => user.name === values.svnAccountName)) throw new Error('该账户已存在；如需修改密码，请使用账户列表中的「修改密码」。');
    await api('/api/users', { method: 'POST', body: { username: values.svnAccountName, password: values.svnAccountSecret } });
    await refreshOverview();
    await navigate('users');
    toast('SVN 账户已创建');
  }, true);
}

function resetPassword(username) {
  openModal('修改 SVN 账户密码', `<p class="muted">为账户「${esc(username)}」设置新密码。保存后，SVN 客户端需要使用新密码重新验证。</p><label class="password-reset-field">新密码<input name="svnAccountSecret" data-lpignore="true" data-1p-ignore type="password" autocomplete="new-password" placeholder="不限制长度，可使用短密码"></label>`, async values => {
    validatePassword(values.svnAccountSecret);
    await api('/api/users', { method: 'POST', body: { username, password: values.svnAccountSecret } });
    toast('账户密码已修改');
  }, true);
  $('#modal-form [data-modal-submit]').textContent = '确认修改密码';
}

function validatePassword(password) {
  if (password !== password.trim() || /[\r\n\0]/.test(password)) throw new Error('密码首尾不能有空格，也不能包含换行或空字符');
}

function authHelp() {
  return `<section class="help-card"><div class="help-title">${icon('shield')}<h3>客户端连接与认证</h3></div><div class="help-grid"><div><strong>提交需要读写权限</strong><p>能浏览不代表能提交。请在仓库「访问权限」中为 SVN 账户选择「读写 · 允许提交」。</p></div><div><strong>使用同一个连接地址</strong><p>IP 与域名会被客户端视为不同服务器。统一使用这里复制的地址，并在客户端勾选「保存认证」。</p></div><div><strong>Web 登录与 SVN 登录独立</strong><p>客户端请使用「SVN 账户」。已保存密码仍反复提示时，清除该服务器的旧认证记录后重新登录。</p></div></div></section>`;
}

function renderSettings(data) {
  content().innerHTML = `<div class="settings-layout"><form id="settings-form" class="settings-card"><div class="card-heading">${icon('settings')}<div><h2>存储与连接</h2><p class="muted">保存后更新仓库地址与存放设置</p></div></div><label>仓库保存目录<input name="repositoriesPath" id="settings-repositories-path" value="${esc(data.repositoriesPath)}" required placeholder="例如 /vol1/1000/SVN"></label><p class="field-help">填写飞牛系统上的绝对路径，应用需要拥有该目录的读写权限。</p><div class="notice" id="storage-change-help">修改保存目录时，应用会暂停 SVN 服务，复制所有仓库到新目录后切换。原目录保留，不会删除旧数据。请先备份，并确认目标空间充足、无同名仓库。</div><label>SVN 连接 IP 或域名<input name="svnHost" id="settings-svn-host" value="${esc(data.svnHost || '')}" placeholder="例如 192.168.1.100 或 svn.example.com"></label><p class="field-help">仅填写主机名或 IP，不包含 svn://、端口和路径。留空使用当前访问地址的主机。此项更新复制的连接地址；域名解析和网络转发需自行配置。</p><label>SVN 服务端口<input name="svnPort" value="${esc(data.svnPort)}" readonly aria-readonly="true"></label><p class="field-help">端口由飞牛应用的配置向导设置；此处仅显示当前端口。</p><label class="checkbox-field"><input type="checkbox" name="webAuthRequired" id="settings-web-auth" ${data.webAuthRequired !== false ? 'checked' : ''}><span>启用 Web 登录验证</span></label><p class="field-help">默认开启。关闭后，任何能访问管理端口的人都无需登录即可管理仓库；不会自动继承飞牛登录会话。此设置只影响 Web 控制台，SVN 客户端仍需账户认证。</p><div class="settings-actions"><p id="settings-status" class="field-help" role="status"></p><button type="submit" class="button primary">保存设置</button></div></form><aside class="settings-aside"><div class="settings-card"><span class="setting-illustration">${icon('folder')}</span><h3>项目的位置，由你决定</h3><p class="muted">仓库目录存放完整的版本历史。更改位置前，请确认目标存储空间和目录权限。</p></div>${authHelp()}</aside></div>`;
  $('#settings-form').addEventListener('submit', async event => {
    event.preventDefault();
    if (state.settingsSaving) return;
    const form = event.currentTarget;
    const values = Object.fromEntries(new FormData(form));
    delete values.svnPort;
    values.webAuthRequired = $('#settings-web-auth', form).checked;
    const continueSave = async () => {
    const migrating = values.repositoriesPath.trim() !== data.repositoriesPath && state.overview.repositories.length > 0;
    if (migrating) {
      $('#modal-title').textContent = '迁移仓库保存目录';
      $('#modal-body').innerHTML = '<p class="muted">应用将暂时中断 SVN 服务，将所有仓库复制到新目录后切换。目标目录需有足够空间，且不能存在同名仓库。原目录保留；成功后，请让 SVN 客户端重新连接。</p><div class="notice warning">请先备份。复制可能耗时较长，迁移过程中请勿关闭应用或重复保存。</div><div class="modal-actions"><button class="button" data-close-modal>取消</button><button class="button primary" id="confirm-migrate">迁移并保存</button></div><p class="field-help" id="migration-status" role="status"></p>';
      $('#modal').showModal();
      $('#confirm-migrate').onclick = () => saveSettings(values, true, form);
    } else await saveSettings(values, false, form);
    };
    if (data.webAuthRequired !== false && !values.webAuthRequired) {
      $('#modal-title').textContent = '关闭 Web 登录验证';
      $('#modal-body').innerHTML = '<div class="notice warning">关闭后，任何能够直接访问 FnSVN 管理端口的人都可管理仓库、账户与设置，无需提供密码。飞牛桌面登录不会自动保护这个管理端口。</div><p class="muted">SVN 客户端的账户认证保持开启。确定关闭 Web 登录验证吗？</p><div class="modal-actions"><button class="button" data-close-modal>取消</button><button class="button danger" id="confirm-disable-web-auth">确认关闭并继续保存</button></div>';
      $('#modal').showModal();
      $('#confirm-disable-web-auth').onclick = async event => {
        event.currentTarget.disabled = true;
        values.confirmDisableAuth = true;
        $('#modal').close();
        await continueSave();
      };
    } else await continueSave();
  });
}

async function saveSettings(values, migrating, form) {
  if (state.settingsSaving) return;
  state.settingsSaving = true;
  const button = $('button[type="submit"]', form);
  const closeButton = $('#modal-close');
  const preventClose = event => event.preventDefault();
  button.disabled = true;
  button.textContent = migrating ? '正在复制仓库，请勿关闭…' : '正在保存…';
  if ($('#settings-status')) $('#settings-status').textContent = migrating ? '正在复制仓库，SVN 服务暂时不可用。请勿关闭应用。' : '正在保存，请稍候…';
  if (migrating) {
    $('#modal').querySelectorAll('button').forEach(node => { node.disabled = true; });
    $('#confirm-migrate').textContent = '正在复制仓库，请勿关闭…';
    $('#migration-status').textContent = '完成复制后将自动切换目录，请耐心等待。';
    $('#modal').addEventListener('cancel', preventClose);
  }
  try {
    const wasAuthRequired = state.overview.webAuthRequired !== false;
    const result = await api('/api/settings', { method: 'PUT', body: values });
    if (!wasAuthRequired && result.webAuthRequired !== false) {
      showAuth(!!result.setupRequired);
      toast(result.setupRequired ? '已启用 Web 登录验证，请创建管理员账户' : '已启用 Web 登录验证，请重新登录');
      return;
    }
    await refreshOverview();
    if (migrating) $('#modal').close();
    if (state.view === 'settings') renderSettings(result);
    toast(migrating ? '仓库已迁移，原目录保留。SVN 客户端可以重新连接。' : '设置已保存，仓库地址已更新');
  } catch (error) {
    if ($('#settings-status')) $('#settings-status').textContent = error.message;
    if (migrating && $('#migration-status')) $('#migration-status').textContent = error.message;
    toast(error.message, true);
  } finally {
    state.settingsSaving = false;
    button.disabled = false;
    button.textContent = '保存设置';
    closeButton.disabled = false;
    if (migrating) {
      $('#modal').removeEventListener('cancel', preventClose);
      $('#modal').querySelectorAll('button').forEach(node => { node.disabled = false; });
      if ($('#confirm-migrate')) $('#confirm-migrate').textContent = '重试迁移并保存';
    }
  }
}

async function copyText(value) {
  try {
    if (navigator.clipboard && window.isSecureContext) await navigator.clipboard.writeText(value);
    else {
      const textarea = document.createElement('textarea');
      textarea.value = value;
      textarea.className = 'clipboard-helper';
      document.body.append(textarea);
      textarea.select();
      const copied = document.execCommand('copy');
      textarea.remove();
      if (!copied) throw new Error('copy unavailable');
    }
    toast('SVN 地址已复制');
  } catch {
    $('#modal-title').textContent = '复制仓库地址';
    $('#modal-body').innerHTML = `<p class="muted">浏览器限制了自动复制。点击「全选地址」，然后按 Ctrl+C（Mac 为 ⌘C）或长按复制。</p><label class="copy-field">SVN 连接地址<input id="manual-copy-value" value="${esc(value)}" readonly></label><div class="modal-actions"><button class="button primary" id="select-copy-value">全选地址</button><button class="button" data-close-modal>完成</button></div><p class="field-help" id="copy-feedback" role="status"></p>`;
    $('#modal').showModal();
    const selectAddress = () => { $('#manual-copy-value').focus(); $('#manual-copy-value').select(); $('#copy-feedback').textContent = '地址已全选，请按 Ctrl+C / ⌘C 复制。'; };
    $('#select-copy-value').onclick = selectAddress;
    $('#manual-copy-value').onclick = selectAddress;
    $('#manual-copy-value').addEventListener('copy', () => { $('#copy-feedback').textContent = '已复制地址，可粘贴到 SVN 客户端。'; });
    selectAddress();
  }
}

$('#global-search').addEventListener('input', async event => {
  if (state.repositoryBusy) { toast('正在处理仓库，请等待完成'); return; }
  if (state.settingsSaving) { toast('正在保存设置，请等待完成'); return; }
  const value = event.target.value;
  if (state.view !== 'repositories' || state.detail) { await navigate('repositories'); $('#global-search').value = value; }
  $('#repo-search').value = value;
  renderRepositoryCards(value);
});

document.addEventListener('click', async event => {
  const target = event.target.closest('button, a[data-action]');
  if (state.repositoryBusy) { if (target) toast('正在处理仓库，请等待完成'); return; }
  if (!target) {
    const card = event.target.closest('[data-card-repo]');
    if (card && !event.target.closest('input, select, a')) {
      try { await openRepository(card.dataset.cardRepo); } catch (error) { toast(error.message, true); }
    }
    return;
  }
  try {
    if (state.settingsSaving && (target.dataset.view || target.id === 'logout')) { toast('正在保存设置，请等待完成'); return; }
    if (target.dataset.view) await navigate(target.dataset.view);
    else if (target.dataset.repo !== undefined) await openRepository(target.dataset.repo);
    else if (target.dataset.permissions !== undefined) await openRepository(target.dataset.permissions, 'permissions');
    else if (target.dataset.renameRepo !== undefined) renameRepository(target.dataset.renameRepo);
    else if (target.dataset.deleteRepo !== undefined) deleteRepository(target.dataset.deleteRepo);
    else if (target.dataset.copy !== undefined) await copyText(target.dataset.copy);
    else if (target.dataset.path !== undefined && state.detail) await openRepository(state.detail.name, 'files', target.dataset.path);
    else if (target.dataset.tab && state.detail) { state.tab = target.dataset.tab; renderDetail(); }
    else if (target.dataset.backup !== undefined) {
      target.disabled = true;
      target.textContent = '正在创建备份…';
      try { const result = await api(`${repoApi(target.dataset.backup)}/backup`, { method: 'POST' }); toast(`备份已创建：${result.filename}`); }
      finally { target.disabled = false; target.textContent = '创建备份'; }
    } else if (target.dataset.resetPassword !== undefined) resetPassword(target.dataset.resetPassword);
    else if (target.dataset.deleteUser !== undefined) {
      const name = target.dataset.deleteUser;
      $('#modal-title').textContent = '删除 SVN 账户';
      $('#modal-body').innerHTML = `<p class="muted">确定删除账户「${esc(name)}」？此账户将无法继续通过 SVN 客户端访问仓库。</p><div class="modal-actions"><button class="button" data-close-modal>取消</button><button class="button danger" id="confirm-delete">删除账户</button></div><p class="form-error" id="modal-error" role="alert"></p>`;
      $('#modal').showModal();
      $('#confirm-delete').onclick = async event => {
        event.currentTarget.disabled = true;
        try { await api(`/api/users/${segment(name)}`, { method: 'DELETE' }); await refreshOverview(); $('#modal').close(); renderUsers(); toast('SVN 账户已删除'); }
        catch (error) { $('#modal-error').textContent = error.message; $('#confirm-delete').disabled = false; }
      };
    } else if (target.hasAttribute('data-close-modal') || target.id === 'modal-close') $('#modal').close();
    else if (target.dataset.action === 'create-repo') createRepository();
    else if (target.dataset.action === 'scan-repositories') scanRepositories();
    else if (target.dataset.action === 'create-user') createUser();
    else if (target.dataset.action === 'back') await navigate('repositories');
    else if (target.dataset.action === 'goto-users') await navigate('users');
    else if (target.dataset.action === 'refresh') { await refreshOverview(); await navigate(state.view); }
  } catch (error) { toast(error.message, true); }
});

$('#page-action').addEventListener('click', async () => {
  if (state.repositoryBusy) { toast('正在处理仓库，请等待完成'); return; }
  if (state.view === 'repositories') createRepository();
  else if (state.view === 'users') createUser();
  else await navigate('backups');
});

$('#auth-form').addEventListener('submit', async event => {
  event.preventDefault();
  const button = $('#auth-submit');
  button.disabled = true;
  $('#auth-error').textContent = '';
  try {
    const values = Object.fromEntries(new FormData(event.currentTarget));
    if (state.setup) validatePassword(values.password);
    await api(state.setup ? '/api/setup' : '/api/login', { method: 'POST', body: values });
    event.target.reset();
    await startApp();
  } catch (error) { $('#auth-error').textContent = error.message; }
  finally { button.disabled = false; }
});

$('#logout').addEventListener('click', async () => {
  if (state.repositoryBusy) { toast('正在处理仓库，请等待完成'); return; }
  if (state.settingsSaving) { toast('正在保存设置，请等待完成'); return; }
  try { await api('/api/logout', { method: 'POST' }); showAuth(false); }
  catch (error) { toast(error.message, true); }
});

document.addEventListener('keydown', async event => {
  if (event.key !== 'Enter' || !event.target.matches('[data-card-repo]') || state.repositoryBusy) return;
  event.preventDefault();
  try { await openRepository(event.target.dataset.cardRepo); } catch (error) { toast(error.message, true); }
});
$('#modal').addEventListener('cancel', event => { if (state.repositoryBusy) event.preventDefault(); });
$('#modal').addEventListener('close', () => {
  if ($('#modal').open) return;
  $('#modal').classList.remove('scan-dialog');
  $('#modal').querySelectorAll('input[type="password"]').forEach(input => { input.value = ''; input.disabled = true; });
});

(async () => {
  try {
    const session = await api('/api/session');
    if (session.authenticated) await startApp();
    else showAuth(!!session.setupRequired);
  } catch (error) {
    $('#loading-screen').innerHTML = `<div class="align-center"><p>${esc(error.message)}</p><button class="button" id="retry-start">重新连接</button></div>`;
    $('#retry-start').onclick = () => location.reload();
  }
})();
