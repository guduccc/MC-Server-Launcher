/* MC Panel 前端逻辑（原生 JS，无依赖） */
'use strict';

const $ = (id) => document.getElementById(id);
const enc = encodeURIComponent;
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) =>
  ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

const TABS = ['console', 'players', 'props', 'files', 'settings', 'install'];

function initialTab() {
  const h = (location.hash || '').replace('#', '');
  return TABS.includes(h) ? h : 'console';
}

const state = {
  current: null,
  overview: null,
  detail: null,
  tab: initialTab(),
  propsOriginal: {},
  propsData: null,
  filesPath: '',
  cores: [],
  presets: [],
  pollTimer: null,
  busy: false,
};

/* ============================================================ 基础请求 */
async function api(path, { method = 'GET', body = null, raw = false } = {}) {
  const opts = { method, headers: {} };
  if (body !== null && !raw) {
    opts.headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(body);
  } else if (raw) {
    opts.body = body;
  }
  const resp = await fetch(path, opts);
  let data;
  try { data = await resp.json(); } catch (e) { data = { ok: false, error: '响应解析失败' }; }
  if (!resp.ok) {
    if (resp.status === 401 && data.need_login) showLogin();
    throw new Error(data.error || ('HTTP ' + resp.status));
  }
  return data;
}

function toast(msg, type = 'info', ms = 3200) {
  const box = document.createElement('div');
  box.className = 'toast ' + type;
  box.textContent = msg;
  $('toasts').appendChild(box);
  setTimeout(() => box.remove(), ms);
}

function fmtBytes(n) {
  if (n === null || n === undefined) return '-';
  const u = ['B', 'KB', 'MB', 'GB', 'TB'];
  let i = 0, v = Number(n);
  while (v >= 1024 && i < u.length - 1) { v /= 1024; i++; }
  return (i === 0 ? v.toFixed(0) : v.toFixed(1)) + ' ' + u[i];
}

function fmtDuration(sec) {
  if (!sec || sec <= 0) return '-';
  sec = Math.floor(sec);
  const d = Math.floor(sec / 86400), h = Math.floor(sec % 86400 / 3600),
        m = Math.floor(sec % 3600 / 60), s = sec % 60;
  if (d) return d + '天' + h + '小时';
  if (h) return h + '小时' + m + '分';
  if (m) return m + '分' + s + '秒';
  return s + '秒';
}

function fmtTime(ts) {
  const d = new Date(ts * 1000);
  return [d.getHours(), d.getMinutes(), d.getSeconds()]
    .map((n) => String(n).padStart(2, '0')).join(':');
}

const STATE_TEXT = { stopped: '已停止', starting: '启动中', running: '运行中', stopping: '停止中' };

/* ============================================================ 登录 */
function showLogin() { $('login-layer').classList.remove('hidden'); }

$('login-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  try {
    await api('/api/login', { method: 'POST', body: { password: $('login-password').value } });
    $('login-layer').classList.add('hidden');
    $('login-error').textContent = '';
    boot();
  } catch (err) { $('login-error').textContent = err.message; }
});

/* ============================================================ 启动流程 */
async function boot() {
  try {
    const ov = await api('/api/overview');
    applyOverview(ov);
    if (!state.current && ov.instances.length) {
      // 默认选中正在跑的实例，没有就选第一个
      const running = ov.instances.find((i) => i.state !== 'stopped');
      selectInstance((running || ov.instances[0]).name);
    }
    if (!ov.instances.length) renderEmptyState();
  } catch (err) {
    if (!String(err.message).includes('未登录')) toast('加载失败：' + err.message, 'error');
  }
  if (!state.pollTimer) state.pollTimer = setInterval(pollTick, 1000);
}

function updateCounts(ov) {
  $('running-count').textContent = ov.instances.length
    ? `${ov.instances.length} 个 · ${ov.running} 个运行中` : '0';
}

function applyOverview(ov) {
  state.overview = ov;
  state.cores = ov.cores || [];
  state.presets = ov.presets || [];
  $('ver').textContent = ov.panel.version;
  $('info-host').textContent = ov.panel.host + ':' + ov.panel.port;
  $('info-ip').textContent = ov.local_ip;
  $('info-dir').textContent = ov.servers_dir;
  updateCounts(ov);
  updateQtButton(ov);
  renderSidebar();
  renderCoreGrid();
  renderPresets();
}

function updateQtButton(ov) {
  const btn = $('act-qt');
  if (!btn) return;
  const running = !!(ov && ov.panel && ov.panel.qt_running);
  btn.classList.toggle('hidden', !running);
  btn.textContent = 'Qt 界面';
}

$('act-qt').onclick = async () => {
  try {
    const r = await api('/api/focus-qt', { method: 'POST' });
    if (r.ok) toast('已把 Qt 界面切到前台', 'success');
    else toast(r.error || '没有正在运行的 Qt 界面', 'warn', 6000);
  } catch (err) { toast(err.message, 'error'); }
};

/* ============================================================ 侧边栏 */
function renderSidebar() {
  const list = $('inst-list');
  const items = (state.overview?.instances) || [];
  if (!items.length) { list.innerHTML = '<div class="empty">还没有实例<br>点上方按钮创建一个</div>'; return; }
  const existing = new Map([...list.querySelectorAll('.inst-item')].map((n) => [n.dataset.name, n]));
  const keep = new Set();

  items.forEach((it) => {
    keep.add(it.name);
    let node = existing.get(it.name);
    if (!node) {
      node = document.createElement('div');
      node.className = 'inst-item';
      node.dataset.name = it.name;
      node.innerHTML = `<div class="row1"><i class="dot"></i><span class="nm"></span></div>
                        <div class="sub"></div>`;
      node.addEventListener('click', () => selectInstance(it.name));
      list.appendChild(node);
    }
    node.querySelector('.nm').textContent = it.name;
    node.querySelector('.dot').className = 'dot ' + it.state;
    node.querySelector('.sub').textContent =
      `${it.core} ${it.mc_version} · ${it.state === 'running'
        ? '玩家 ' + it.player_count + '/' + it.max_players + ' · ' + fmtDuration(it.uptime)
        : STATE_TEXT[it.state] || it.state}`;
    node.classList.toggle('active', it.name === state.current);
  });
  [...existing.keys()].forEach((n) => { if (!keep.has(n)) existing.get(n).remove(); });
}

function renderEmptyState() {
  $('cur-name').textContent = '没有实例';
  $('cur-state').textContent = '空闲';
  $('console').innerHTML = '<span class="ln info">[面板] 左侧点「＋ 新建实例」开始，'
    + '创建时会自动下载你选的服务端核心。</span>';
}

function selectInstance(name) {
  state.current = name;
  consoleBox.reset();
  state.filesPath = '';
  renderSidebar();
  switchTab(state.tab);
  loadDetail();
  loadConsoleOnce();
}

async function loadDetail() {
  if (!state.current) return;
  try {
    const d = await api('/api/instances/' + enc(state.current));
    state.detail = d.detail;
    applySettingsForm(d.detail);
    renderInstallInfo(d.detail);
  } catch (err) { toast(err.message, 'error'); }
}

/* ============================================================ 顶栏状态 */
function updateHead(snap) {
  if (!snap || snap.name !== state.current) return;
  $('cur-name').textContent = snap.name;
  $('cur-core').textContent = snap.core + ' ' + snap.mc_version;
  $('cur-port').textContent = '端口 ' + (snap.port || '-');
  const st = $('cur-state');
  st.textContent = STATE_TEXT[snap.state] || snap.state;
  st.className = 'status ' + snap.state;
  $('stat-cpu').textContent = snap.cpu === null || snap.cpu === undefined ? '-' : snap.cpu + '%';
  $('stat-mem').textContent = snap.mem_used ? fmtBytes(snap.mem_used) : '-';
  $('stat-up').textContent = fmtDuration(snap.uptime);
  $('stat-players').textContent = snap.player_count + '/' + snap.max_players;

  const busy = snap.state !== 'stopped';
  $('act-start').disabled = busy;
  $('act-restart').disabled = !busy;
  $('act-stop').disabled = !busy;
  $('act-kill').disabled = !busy;

  renderPlayers(snap.players || []);
  updateTask(snap.task);
}

/* ============================================================ 轮询 */
async function pollTick() {
  if (state.busy) return;
  try {
    const ov = await api('/api/overview');
    state.overview = ov;
    updateCounts(ov);
    renderSidebar();
    const mine = ov.instances.find((i) => i.name === state.current);
    updateHead(mine);
    if (mine && mine.task) renderInstallInfoFromSnap(mine);
  } catch (err) {
    if (String(err.message).includes('未登录')) showLogin();
  }
  if (state.current) await tickConsole();
}

/* ============================================================ 控制台 */
const consoleBox = {
  map: new Map(),
  nodes: new Map(),
  lastSeq: 0,
  reset() { this.map.clear(); this.nodes.clear(); this.lastSeq = 0; $('console').innerHTML = ''; },
};

async function loadConsoleOnce() {
  const r = await api(`/api/instances/${enc(state.current)}/console?since=0`);
  appendLines(r.lines);
  consoleBox.lastSeq = r.next;
}

async function tickConsole() {
  if (!state.current) return;
  const since = Math.max(0, consoleBox.lastSeq - 1);
  try {
    const r = await api(`/api/instances/${enc(state.current)}/console?since=${since}`);
    appendLines(r.lines);
    consoleBox.lastSeq = r.next;
  } catch (e) { /* 忽略瞬时错误 */ }
}

function appendLines(lines) {
  const box = $('console');
  const filter = $('hide-progress').checked;
  let added = false;
  lines.forEach((l) => {
    if (filter && l.progress) return;
    let node = consoleBox.nodes.get(l.seq);
    const html = `<span class="t">${fmtTime(l.t)}</span><span class="${l.level}">${esc(l.text)}</span>`;
    if (node) { node.innerHTML = html; }
    else {
      node = document.createElement('span');
      node.className = 'ln';
      node.innerHTML = html;
      box.appendChild(node);
      consoleBox.nodes.set(l.seq, node);
      added = true;
    }
    consoleBox.map.set(l.seq, l);
  });
  while (box.childNodes.length > 4000) {
    const first = box.firstChild;
    const seq = [...consoleBox.nodes.entries()].find(([, n]) => n === first)?.[0];
    if (seq !== undefined) consoleBox.nodes.delete(seq);
    box.removeChild(first);
  }
  if ((added || lines.length) && $('auto-scroll').checked) box.scrollTop = box.scrollHeight;
  if (box.querySelector('.empty')) box.innerHTML = '';
}

const QUICK_CMDS = ['list', 'save-all', 'whitelist list', 'time set day', 'weather clear',
  'difficulty peaceful', 'say 服务器即将重启', 'tps'];
const ADMIN_CMDS = ['op ', 'deop ', 'kick ', 'ban ', 'ban-ip ', 'unban ', 'whitelist add ',
  'whitelist remove ', 'gamemode creative ', 'tp ', 'give ', 'clear ', 'kill ', 'stop'];

function renderQuickCmds() {
  const q = $('quick-cmds');
  q.innerHTML = '';
  QUICK_CMDS.forEach((c) => {
    const b = document.createElement('button');
    b.className = 'qbtn';
    b.textContent = c;
    b.onclick = () => sendCommand(c);
    q.appendChild(b);
  });
  const a = $('admin-cmds');
  a.innerHTML = '';
  ADMIN_CMDS.forEach((c) => {
    const b = document.createElement('button');
    b.className = 'qbtn';
    b.textContent = c.trim();
    b.onclick = () => { switchTab('console'); $('cmd-input').value = c; $('cmd-input').focus(); };
    a.appendChild(b);
  });
}

async function sendCommand(cmd) {
  if (!state.current) return;
  try {
    await api(`/api/instances/${enc(state.current)}/command`, { method: 'POST', body: { command: cmd } });
    if (cmd.trim() === 'stop') toast('已发送停止指令，服务端正在保存数据…', 'warn');
  } catch (err) { toast(err.message, 'error'); }
}

const history = { list: [], idx: -1 };

$('cmd-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const input = $('cmd-input');
  const cmd = input.value.trim();
  if (!cmd) return;
  history.list.push(cmd);
  history.idx = history.list.length;
  input.value = '';
  await sendCommand(cmd);
});

$('cmd-input').addEventListener('keydown', (e) => {
  if (e.key === 'ArrowUp') {
    e.preventDefault();
    if (!history.list.length) return;
    history.idx = Math.max(0, Math.min(history.idx, history.list.length) - 1);
    $('cmd-input').value = history.list[history.idx] || '';
  } else if (e.key === 'ArrowDown') {
    e.preventDefault();
    history.idx = Math.min(history.list.length, history.idx + 1);
    $('cmd-input').value = history.list[history.idx] || '';
  }
});

$('console-clear').onclick = () => consoleBox.reset();
$('console-copy').onclick = async () => {
  const text = [...consoleBox.map.values()].map((l) => `[${fmtTime(l.t)}] ${l.text}`).join('\n');
  try { await navigator.clipboard.writeText(text); toast('已复制 ' + consoleBox.map.size + ' 行', 'success'); }
  catch (e) { toast('复制失败，请手动选择', 'error'); }
};

/* ============================================================ 玩家 */
function renderPlayers(players) {
  const grid = $('player-grid');
  if (!players.length) {
    grid.innerHTML = '<div class="empty">当前没有玩家在线</div>';
    return;
  }
  grid.innerHTML = '';
  players.forEach((p) => {
    const card = document.createElement('div');
    card.className = 'player-card';
    card.innerHTML = `<div class="avatar">${esc(p[0] || '?')}</div><span class="nm">${esc(p)}</span>`;
    const op = document.createElement('button');
    op.className = 'btn ghost small'; op.textContent = 'OP';
    op.onclick = () => sendCommand('op ' + p);
    const kick = document.createElement('button');
    kick.className = 'btn ghost small'; kick.textContent = '踢出';
    kick.onclick = () => sendCommand('kick ' + p);
    card.appendChild(op); card.appendChild(kick);
    grid.appendChild(card);
  });
}

/* ============================================================ 标签页 */
function switchTab(name) {
  state.tab = name;
  if (location.hash !== '#' + name) {
    try { history.replaceState(null, '', '#' + name); } catch (e) { /* 忽略 */ }
  }
  [...document.querySelectorAll('.tab')].forEach((t) =>
    t.classList.toggle('active', t.dataset.tab === name));
  [...document.querySelectorAll('.pane')].forEach((p) =>
    p.classList.toggle('active', p.dataset.pane === name));
  if (!state.current) return;
  if (name === 'props') loadProperties();
  if (name === 'files') loadFiles(state.filesPath);
  if (name === 'players') { renderQuickCmds(); }
  if (name === 'install') renderInstallInfo(state.detail);
}
$('tabs').addEventListener('click', (e) => {
  const t = e.target.closest('.tab');
  if (t) switchTab(t.dataset.tab);
});

/* ============================================================ 服务端配置 */
async function loadProperties() {
  if (!state.current) return;
  try {
    const d = await api(`/api/instances/${enc(state.current)}/properties`);
    state.propsData = d;
    state.propsOriginal = { ...d.values };
    $('props-path').textContent = d.path;
    renderProperties(d);
    markDirty();
  } catch (err) { toast(err.message, 'error'); }
}

function renderProperties(d) {
  const form = $('props-form');
  form.innerHTML = '';
  d.groups.forEach((g) => {
    const box = document.createElement('div');
    box.className = 'prop-group';
    const h = document.createElement('h3');
    h.innerHTML = esc(g.title) + ` <span class="muted small">${g.fields.length} 项</span>`;
    box.appendChild(h);
    const body = document.createElement('div');
    body.className = 'prop-body';
    g.fields.forEach((f) => body.appendChild(propField(f)));
    box.appendChild(body);
    form.appendChild(box);
  });
}

function propField(f) {
  const wrap = document.createElement('div');
  wrap.className = 'prop-item' + (f.present ? '' : ' missing');
  wrap.dataset.key = f.key;
  const label = document.createElement('label');
  label.innerHTML = `<span>${esc(f.label)}</span><span class="key">${esc(f.key)}</span>`;
  wrap.appendChild(label);

  let input;
  if (f.type === 'bool') {
    input = document.createElement('select');
    [['true', '开启'], ['false', '关闭']].forEach(([v, t]) => {
      const o = document.createElement('option'); o.value = v; o.textContent = t; input.appendChild(o);
    });
    input.value = String(f.value).toLowerCase() === 'true' ? 'true' : 'false';
  } else if (f.type === 'enum') {
    input = document.createElement('select');
    const choices = f.choices && f.choices.length ? f.choices
      : (f.options || []).map((v) => ({ value: v, label: v }));
    choices.forEach((c) => {
      const o = document.createElement('option'); o.value = c.value; o.textContent = c.label;
      input.appendChild(o);
    });
    input.value = f.value;
    if (f.value === '') input.selectedIndex = -1;
    if (f.legacy) {
      const tag = document.createElement('div');
      tag.className = 'desc';
      tag.textContent = '该版本用数字存储此设置，面板会按原格式写回';
      wrap.appendChild(tag);
    }
  } else if (f.type === 'int') {
    input = document.createElement('input');
    input.type = 'number';
    input.value = f.value;
  } else {
    input = document.createElement('input');
    input.type = f.type === 'password' ? 'password' : 'text';
    input.value = f.value;
    input.placeholder = f.present ? '' : '未设置';
  }
  // 以控件实际初始值作为“原始值”，避免空的布尔/下拉被误判成已修改
  input.dataset.orig = input.value;
  input.addEventListener('input', markDirty);
  input.addEventListener('change', markDirty);
  wrap.appendChild(input);
  if (f.desc) {
    const d = document.createElement('div');
    d.className = 'desc';
    d.textContent = f.desc;
    wrap.appendChild(d);
  }
  return wrap;
}

function changedProps() {
  const updates = {};
  document.querySelectorAll('#props-form .prop-item').forEach((item) => {
    const input = item.querySelector('input,select');
    if (!input) return;
    if (input.value !== input.dataset.orig) updates[item.dataset.key] = input.value;
  });
  return updates;
}

function markDirty() {
  const n = Object.keys(changedProps()).length;
  document.querySelectorAll('#props-form .prop-item').forEach((item) => {
    const input = item.querySelector('input,select');
    item.classList.toggle('changed', !!input && input.value !== input.dataset.orig);
  });
  const label = $('props-dirty');
  label.textContent = n ? `已修改 ${n} 项` : '未修改';
  label.style.color = n ? 'var(--accent)' : '';
}

$('props-save').onclick = async () => {
  const updates = changedProps();
  if (!Object.keys(updates).length) { toast('没有需要保存的修改'); return; }
  try {
    await api(`/api/instances/${enc(state.current)}/properties`, { method: 'POST', body: { updates } });
    toast(`已保存 ${Object.keys(updates).length} 项配置，重启服务端后生效`, 'success');
    await loadProperties();
  } catch (err) { toast(err.message, 'error'); }
};
$('props-reload').onclick = loadProperties;

/* ============================================================ 文件管理 */
async function loadFiles(path) {
  if (!state.current) return;
  try {
    const d = await api(`/api/instances/${enc(state.current)}/files?path=${enc(path || '')}`);
    state.filesPath = d.path;
    renderCrumb(d.path);
    renderFiles(d);
  } catch (err) { toast(err.message, 'error'); }
}

function renderCrumb(path) {
  const c = $('file-crumb');
  c.innerHTML = '';
  const mk = (text, target) => {
    const a = document.createElement('a');
    a.textContent = text;
    a.onclick = () => loadFiles(target);
    return a;
  };
  c.appendChild(mk('实例根目录', ''));
  (path || '').split('/').filter(Boolean).forEach((part, i, arr) => {
    const sep = document.createElement('span'); sep.textContent = '/'; c.appendChild(sep);
    c.appendChild(mk(part, arr.slice(0, i + 1).join('/')));
  });
}

function renderFiles(d) {
  const list = $('file-list');
  list.innerHTML = '';
  const head = document.createElement('div');
  head.className = 'file-row head';
  head.innerHTML = '<span>名称</span><span>大小</span><span>修改时间</span><span>操作</span>';
  list.appendChild(head);

  if (d.parent !== null) {
    const up = document.createElement('div');
    up.className = 'file-row';
    up.innerHTML = '<span class="nm dir">↩ 返回上级</span><span></span><span></span><span></span>';
    up.querySelector('.nm').onclick = () => loadFiles(d.parent);
    list.appendChild(up);
  }
  if (!d.entries.length) {
    const e = document.createElement('div');
    e.className = 'empty';
    e.textContent = '空目录';
    list.appendChild(e);
    return;
  }
  d.entries.forEach((f) => {
    const row = document.createElement('div');
    row.className = 'file-row';
    const time = new Date(f.mtime * 1000).toLocaleString('zh-CN', { hour12: false });
    row.innerHTML = `<span class="nm ${f.dir ? 'dir' : ''}">${f.dir ? '📁' : '📄'} ${esc(f.name)}</span>
                     <span class="muted">${f.dir ? '<DIR>' : esc(f.size_text)}</span>
                     <span class="muted">${esc(time)}</span><span class="ops"></span>`;
    const nm = row.querySelector('.nm');
    nm.onclick = () => {
      if (f.dir) loadFiles(f.path);
      else if (f.editable) openEditor(f.path, f.name);
      else toast('该类型文件不支持在线编辑', 'warn');
    };
    const ops = row.querySelector('.ops');
    const mkBtn = (text, fn, cls = 'ghost') => {
      const b = document.createElement('button');
      b.className = 'btn small ' + cls;
      b.textContent = text;
      b.onclick = (e) => { e.stopPropagation(); fn(); };
      return b;
    };
    if (!f.dir && f.editable) ops.appendChild(mkBtn('编辑', () => openEditor(f.path, f.name)));
    if (!f.dir) ops.appendChild(mkBtn('下载', () => {
      window.open(`/api/instances/${enc(state.current)}/download?path=${enc(f.path)}`, '_blank');
    }));
    ops.appendChild(mkBtn('删除', async () => {
      if (!confirm(`确定删除 ${f.name} 吗？此操作不可恢复。`)) return;
      try {
        await api(`/api/instances/${enc(state.current)}/delete-file`, { method: 'POST', body: { path: f.path } });
        toast('已删除 ' + f.name, 'success');
        loadFiles(state.filesPath);
      } catch (err) { toast(err.message, 'error'); }
    }, 'danger ghost'));
    list.appendChild(row);
  });
}

async function openEditor(path, name) {
  try {
    const d = await api(`/api/instances/${enc(state.current)}/file?path=${enc(path)}`);
    openModal(`编辑 ${name}`, `
      <div class="modal-body">
        <p class="muted small">${esc(path)} · ${fmtBytes(d.size)}</p>
        <textarea id="editor-area" rows="20" spellcheck="false"></textarea>
      </div>
      <div class="modal-foot">
        <button class="btn ghost" data-close>取消</button>
        <button class="btn primary" id="editor-save">保存</button>
      </div>`);
    $('editor-area').value = d.content;
    $('editor-save').onclick = async () => {
      try {
        await api(`/api/instances/${enc(state.current)}/file`, {
          method: 'POST', body: { path, content: $('editor-area').value },
        });
        toast('已保存 ' + name, 'success');
        closeModal();
        loadFiles(state.filesPath);
      } catch (err) { toast(err.message, 'error'); }
    };
  } catch (err) { toast(err.message, 'error'); }
}

$('file-upload').addEventListener('change', async (e) => {
  const files = [...e.target.files];
  e.target.value = '';
  for (const f of files) {
    try {
      await api(`/api/instances/${enc(state.current)}/upload?path=${enc(state.filesPath)}`
        + `&filename=${enc(f.name)}`, { method: 'POST', body: f, raw: true });
      toast('已上传 ' + f.name, 'success');
    } catch (err) { toast(f.name + ' 上传失败：' + err.message, 'error'); }
  }
  loadFiles(state.filesPath);
});

$('file-mkdir').onclick = async () => {
  const name = prompt('新文件夹名称（可用 a/b 创建多级）');
  if (!name) return;
  const path = state.filesPath ? state.filesPath + '/' + name : name;
  try {
    await api(`/api/instances/${enc(state.current)}/mkdir`, { method: 'POST', body: { path } });
    loadFiles(state.filesPath);
  } catch (err) { toast(err.message, 'error'); }
};
$('file-refresh').onclick = () => loadFiles(state.filesPath);

/* ============================================================ 实例设置 */
function applySettingsForm(detail) {
  if (!detail) return;
  $('set-core').value = detail.core || '';
  $('set-version').value = detail.mc_version || '';
  $('set-minmem').value = detail.min_memory || 1024;
  $('set-maxmem').value = detail.max_memory || 2048;
  $('set-jvm').value = detail.jvm_args || '';
  $('set-serverargs').value = detail.server_args || '';
  $('set-stoptimeout').value = detail.stop_timeout || 90;
  $('set-note').value = detail.note || '';
  $('set-eula').checked = !!detail.accept_eula;
  $('set-autorestart').checked = !!detail.auto_restart;

  const sel = $('set-java');
  sel.innerHTML = '';
  const javas = state.overview?.javas || [];
  const opt0 = document.createElement('option');
  opt0.value = '';
  opt0.textContent = javas.length ? '自动选择（推荐）' : '未检测到 Java，请手动填写';
  sel.appendChild(opt0);
  javas.forEach((j) => {
    const o = document.createElement('option');
    o.value = j.path;
    o.textContent = `Java ${j.version} ${j.arch} — ${j.path}`;
    sel.appendChild(o);
  });
  const custom = document.createElement('option');
  custom.value = '__custom__';
  custom.textContent = '手动输入路径…';
  sel.appendChild(custom);
  sel.value = detail.java_path || '';
  if (detail.java_path && !javas.some((j) => j.path === detail.java_path)
      && detail.java_path !== '__custom__') {
    const o = document.createElement('option');
    o.value = detail.java_path;
    o.textContent = '当前：' + detail.java_path;
    sel.insertBefore(o, custom);
    sel.value = detail.java_path;
  }

  const info = $('set-info');
  info.innerHTML = '';
  const rows = [
    ['实例目录', detail.root],
    ['核心', `${detail.core} ${detail.mc_version}`],
    ['启动方式', detail.launch ? `${detail.launch.type} → ${detail.launch.target}` : '-'],
    ['下次启动命令', (state.overview?.javas || []).length ? '见控制台启动日志' : '-'],
    ['创建时间', detail.created_at ? new Date(detail.created_at * 1000).toLocaleString('zh-CN') : '-'],
    ['累计运行', fmtDuration(detail.total_uptime)],
    ['EULA 状态', detail.accept_eula ? '已自动同意' : '未同意'],
  ];
  rows.forEach(([k, v]) => {
    const r = document.createElement('div');
    r.className = 'row';
    r.innerHTML = `<span>${esc(k)}</span><b class="ellipsis" title="${esc(v)}">${esc(v)}</b>`;
    info.appendChild(r);
  });
}

$('set-java').addEventListener('change', () => {
  if ($('set-java').value !== '__custom__') return;
  const p = prompt('请输入 java 可执行文件的完整路径，例如\nC:\\Program Files\\Java\\jdk1.8.0_202\\bin\\java.exe');
  if (p) {
    const o = document.createElement('option');
    o.value = p; o.textContent = '手动：' + p;
    $('set-java').insertBefore(o, $('set-java').lastChild);
    $('set-java').value = p;
  } else { $('set-java').value = state.detail?.java_path || ''; }
});

function renderPresets() {
  const box = $('jvm-presets');
  if (!box) return;
  box.innerHTML = '';
  (state.presets || []).forEach((p) => {
    const b = document.createElement('button');
    b.className = 'btn ghost small';
    b.textContent = p.name;
    b.onclick = () => { $('set-jvm').value = p.args; toast('已套用：' + p.name); };
    box.appendChild(b);
  });
}

$('set-save').onclick = async () => {
  try {
    const min = parseInt($('set-minmem').value, 10) || 1024;
    const max = parseInt($('set-maxmem').value, 10) || 2048;
    if (min > max) { toast('最小内存不能大于最大内存', 'error'); return; }
    const d = await api(`/api/instances/${enc(state.current)}/config`, {
      method: 'POST',
      body: {
        jvm_args: $('set-jvm').value.trim(),
        server_args: $('set-serverargs').value.trim(),
        stop_timeout: parseInt($('set-stoptimeout').value, 10) || 90,
        min_memory: min,
        max_memory: max,
        java_path: $('set-java').value === '__custom__' ? '' : $('set-java').value,
        accept_eula: $('set-eula').checked,
        auto_restart: $('set-autorestart').checked,
        note: $('set-note').value.trim(),
        mc_version: $('set-version').value.trim(),
      },
    });
    state.detail = d.detail;
    toast('设置已保存，下次启动生效', 'success');
    boot();
  } catch (err) { toast(err.message, 'error'); }
};

$('set-delete').onclick = async () => {
  const rm = $('del-files').checked;
  const tip = rm ? '连同全部文件一起删除' : '只从面板移除（保留磁盘文件）';
  if (!confirm(`确定要删除实例「${state.current}」吗？\n${tip}`)) return;
  try {
    const r = await api(`/api/instances/${enc(state.current)}/delete`, {
      method: 'POST', body: { remove_files: rm },
    });
    if (r.warning) toast(r.warning, 'warn', 9000);
    else toast('实例已删除', 'success');
    state.current = null;
    state.detail = null;
    await boot();
  } catch (err) { toast(err.message, 'error'); }
};

/* ============================================================ 安装 */
function renderCoreGrid() {
  const grid = $('core-grid');
  if (!grid || grid.dataset.ready) return;
  grid.dataset.ready = '1';
  (state.cores || []).forEach((c) => {
    const card = document.createElement('div');
    card.className = 'core-card';
    card.dataset.core = c.id;
    card.innerHTML = `<b>${esc(c.name)}</b><small>${esc(c.desc)}</small>`;
    card.onclick = () => {
      [...grid.children].forEach((n) => n.classList.remove('active'));
      card.classList.add('active');
      updateInstallTip();
    };
    grid.appendChild(card);
  });
}

function pickedCore() {
  const active = document.querySelector('#core-grid .core-card.active');
  return active ? active.dataset.core : 'paper';
}

function updateInstallTip() {
  const core = pickedCore();
  const info = (state.cores || []).find((c) => c.id === core);
  const tips = {
    vanilla: '官方原版：无插件、无模组，适合纯净生存。',
    paper: 'Paper：性能好、生态最全，最多人用的插件端。',
    purpur: 'Purpur：在 Paper 基础上多了很多可调玩法选项。',
    fabric: 'Fabric：模组端，需自己往 mods 文件夹放模组。',
    forge: 'Forge：1.12.2 等老版本模组生态最好；安装器需联网拉取依赖库。',
    spigot: 'Spigot：通过 BuildTools 现场编译，需要本机安装 Git，耗时较长。',
    custom: '自定义：把整合包/服务端 jar 上传到实例目录后，面板会自动识别。',
  };
  $('ins-tip').textContent = tips[core] || (info ? info.desc : '');
  $('ver-hint').textContent = '';
  const v = $('ins-version').value.trim();
  if (v) {
    const need = guessJava(v);
    if (need) $('ver-hint').textContent = `推荐 Java ${need}`;
  }
}

function guessJava(v) {
  const m = String(v).match(/^1\.(\d+)(?:\.(\d+))?/);
  if (!m) return null;
  const minor = +m[1], patch = +(m[2] || 0);
  if (minor <= 16) return 8;
  if (minor <= 19) return 17;
  if (minor === 20 && patch <= 4) return 17;
  return 21;
}
$('ins-version').addEventListener('input', updateInstallTip);

$('ver-load').onclick = async () => {
  const core = pickedCore();
  $('ver-load').disabled = true;
  $('ver-load').textContent = '拉取中…';
  try {
    const d = await api(`/api/versions?core=${enc(core)}`);
    const dl = $('version-list');
    dl.innerHTML = '';
    const onlyRelease = $('ver-release-only').checked;
    let count = 0;
    (d.versions || []).forEach((v) => {
      if (onlyRelease && v.type !== 'release') return;
      const o = document.createElement('option');
      o.value = v.version;
      if (v.note) o.label = v.note;
      dl.appendChild(o);
      count++;
    });
    const tip = `共找到 ${count} 个版本`;
    toast(`已加载 ${count} 个 ${core} 版本`, 'success');
    $('ver-hint').textContent = tip;
    const first = (d.versions || []).find((v) => !onlyRelease || v.type === 'release');
    if (!$('ins-version').value && first) {
      $('ins-version').value = first.version;
      updateInstallTip();
    }
  } catch (err) { toast(err.message, 'error'); }
  $('ver-load').disabled = false;
  $('ver-load').textContent = '拉取列表';
};

$('build-load').onclick = async () => {
  const core = pickedCore();
  const mc = $('ins-version').value.trim();
  if (!mc) { toast('先填游戏版本', 'warn'); return; }
  if (['fabric', 'spigot', 'custom'].includes(core)) {
    toast(`${core} 没有构建号的概念，直接填版本即可`, 'warn');
    return;
  }
  try {
    const d = await api(`/api/builds?core=${enc(core)}&mc=${enc(mc)}`);
    const dl = $('build-list');
    dl.innerHTML = '';
    (d.builds || []).slice(0, 300).forEach((b) => {
      const o = document.createElement('option');
      o.value = b.build;
      if (b.note) o.label = b.note;
      dl.appendChild(o);
    });
    toast(`找到 ${(d.builds || []).length} 个构建`, 'success');
  } catch (err) { toast(err.message, 'error'); }
};

$('ins-go').onclick = async () => {
  const core = pickedCore();
  const mc = $('ins-version').value.trim();
  if (!mc) { toast('请填写游戏版本', 'warn'); return; }
  if (!confirm(`确定要为「${state.current}」下载安装 ${core} ${mc} 吗？\n`
    + '安装会覆盖同名的服务端核心 jar。')) return;
  try {
    await api(`/api/instances/${enc(state.current)}/install`, {
      method: 'POST', body: { core, mc_version: mc, build: $('ins-build').value.trim() || null },
    });
    toast('安装已开始，可以切到控制台看进度', 'success');
    switchTab('console');
  } catch (err) { toast(err.message, 'error'); }
};

function renderInstallInfo(detail) {
  if (!detail) return;
  const box = $('ins-current');
  if (!box) return;
  const rows = [
    ['核心类型', detail.core],
    ['游戏版本', detail.mc_version],
    ['启动入口', detail.launch ? `${detail.launch.type} · ${detail.launch.target}` : '未安装'],
    ['是否已安装', detail.installed ? '是' : '否'],
  ];
  box.innerHTML = '';
  rows.forEach(([k, v]) => {
    const r = document.createElement('div');
    r.className = 'row';
    r.innerHTML = `<span>${esc(k)}</span><b>${esc(v)}</b>`;
    box.appendChild(r);
  });
  if (!$('ins-version').value) $('ins-version').value = detail.mc_version || '';
  const card = document.querySelector(`#core-grid .core-card[data-core="${detail.core}"]`);
  if (card) {
    [...document.querySelectorAll('#core-grid .core-card')].forEach((n) => n.classList.remove('active'));
    card.classList.add('active');
  }
  updateInstallTip();
}

function renderInstallInfoFromSnap(snap) { updateTask(snap.task); }

function updateTask(task) {
  const bar = $('task-bar');
  if (!bar) return;
  if (!task) {
    bar.style.width = '0%';
    $('task-phase').textContent = '-';
    $('task-msg').textContent = '-';
    $('task-err').textContent = '';
    $('ins-go').disabled = false;
    return;
  }
  bar.style.width = (task.percent || 0) + '%';
  $('task-phase').textContent = task.phase || '-';
  $('task-msg').textContent = task.message || '-';
  $('task-err').textContent = task.error ? '错误：' + task.error : '';
  $('ins-go').disabled = !!task.running;
  if (!task.running && task.error) $('task-err').style.color = 'var(--danger)';
}

/* ============================================================ 模态框 */
function openModal(title, inner) {
  const box = $('modal-box');
  box.innerHTML = `<div class="modal-head"><b>${esc(title)}</b>
      <button class="close-x" data-close>×</button></div>${inner}`;
  $('modal').classList.remove('hidden');
  box.querySelectorAll('[data-close]').forEach((b) => b.onclick = closeModal);
}
function closeModal() { $('modal').classList.add('hidden'); $('modal-box').innerHTML = ''; }
$('modal').addEventListener('click', (e) => { if (e.target === $('modal')) closeModal(); });
document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeModal(); });

/* ============================================================ 新建实例 */
$('btn-new').onclick = () => {
  const javas = state.overview?.javas || [];
  const javaOpts = javas.map((j) =>
    `<option value="${esc(j.path)}">Java ${j.version} ${j.arch}</option>`).join('');
  openModal('新建服务端实例', `
    <div class="modal-body">
      <div class="form">
        <label>实例名称 <span class="muted small">字母/数字/-/_，会作为文件夹名</span></label>
        <input id="new-name" placeholder="survival" value="server">
        <label>核心类型</label>
        <select id="new-core">
          ${(state.cores || []).map((c) => `<option value="${c.id}">${esc(c.name)} — ${esc(c.desc)}</option>`).join('')}
        </select>
        <div class="row">
          <div><label>游戏版本</label><input id="new-version" value="1.20.1" list="new-version-list">
            <datalist id="new-version-list"></datalist></div>
          <div><label>服务端端口</label><input id="new-port" type="number" value="25565"></div>
        </div>
        <label>服务器标语 MOTD</label>
        <input id="new-motd" value="欢迎来到我的服务器！">
        <div class="row">
          <div><label>最小内存 (MB)</label><input id="new-minmem" type="number" value="1024" step="256"></div>
          <div><label>最大内存 (MB)</label><input id="new-maxmem" type="number" value="2048" step="256"></div>
        </div>
        <label>Java 环境</label>
        <select id="new-java">
          <option value="">自动选择（推荐）</option>${javaOpts}
        </select>
        <label class="check"><input type="checkbox" id="new-eula" checked>
          <span>自动同意 Minecraft EULA（<code>eula=true</code>）</span></label>
        <label class="check"><input type="checkbox" id="new-install" checked>
          <span>创建后立即下载服务端核心</span></label>
        <p class="muted small" id="new-tip">版本填 <b>1.12.2</b> 等老版本时，面板会自动套用 Java 8 的 JVM 参数。</p>
      </div>
    </div>
    <div class="modal-foot">
      <button class="btn ghost" data-close>取消</button>
      <button class="btn primary" id="new-create">创建实例</button>
    </div>`);

  $('new-core').addEventListener('change', () => {
    $('new-tip').innerHTML = '正在获取版本列表…';
    loadNewVersions($('new-core').value);
  });
  loadNewVersions($('new-core').value);

  $('new-create').onclick = async () => {
    const btn = $('new-create');
    btn.disabled = true;
    btn.textContent = '创建中…';
    try {
      const d = await api('/api/instances', {
        method: 'POST',
        body: {
          name: $('new-name').value.trim(),
          core: $('new-core').value,
          mc_version: $('new-version').value.trim(),
          port: $('new-port').value,
          motd: $('new-motd').value,
          min_memory: parseInt($('new-minmem').value, 10) || 1024,
          max_memory: parseInt($('new-maxmem').value, 10) || 2048,
          java_path: $('new-java').value,
          accept_eula: $('new-eula').checked,
          auto_install: $('new-install').checked,
        },
      });
      closeModal();
      toast('实例创建成功', 'success');
      await boot();
      selectInstance(d.instance.name);
    } catch (err) {
      toast(err.message, 'error');
      btn.disabled = false;
      btn.textContent = '创建实例';
    }
  };
};

async function loadNewVersions(core) {
  try {
    const d = await api(`/api/versions?core=${enc(core)}`);
    const dl = $('new-version-list');
    if (!dl) return;
    dl.innerHTML = '';
    const releases = (d.versions || []).filter((v) => v.type === 'release');
    releases.slice(0, 400).forEach((v) => {
      const o = document.createElement('option');
      o.value = v.version;
      if (v.note) o.label = v.note;
      dl.appendChild(o);
    });
    if (!$('new-version').value && releases.length) $('new-version').value = releases[0].version;
    const need = releases.length ? guessJava($('new-version').value) : null;
    $('new-tip').innerHTML = `已加载 ${releases.length} 个正式版本，可直接输入版本号（如 1.12.2）`
      + (need ? ` · 当前版本推荐 Java ${need}` : '');
  } catch (err) {
    const tip = $('new-tip');
    if (tip) tip.textContent = '版本列表获取失败，可直接手动输入版本号，例如 1.20.1';
  }
}

/* ============================================================ 面板设置 */
$('btn-panel-settings').onclick = () => {
  const p = state.overview?.panel || {};
  openModal('面板设置', `
    <div class="modal-body">
      <div class="form">
        <div class="row">
          <div><label>监听地址</label><input id="cfg-host" value="${esc(p.host)}">
            <p class="muted small">127.0.0.1 仅本机；0.0.0.0 允许局域网访问</p></div>
          <div><label>监听端口</label><input id="cfg-port" type="number" value="${esc(p.port)}"></div>
        </div>
        <label>实例存放目录</label>
        <input id="cfg-dir" value="${esc(p.servers_dir)}">
        <label>面板密码 <span class="muted small">留空 = 不校验（仅建议本机使用）</span></label>
        <input id="cfg-pwd" type="password" placeholder="${p.auth_enabled ? '已设置，留空则不修改' : '未设置'}">
        <label>控制台日志保留行数</label>
        <input id="cfg-buf" type="number" value="3000" min="200" max="50000">
        <p class="muted small">监听地址 / 端口修改后需要重启面板才能生效。</p>
      </div>
    </div>
    <div class="modal-foot">
      <button class="btn ghost" data-close>取消</button>
      <button class="btn primary" id="cfg-save">保存</button>
    </div>`);
  $('cfg-save').onclick = async () => {
    try {
      const body = {
        host: $('cfg-host').value.trim(),
        port: parseInt($('cfg-port').value, 10) || 8080,
        servers_dir: $('cfg-dir').value.trim(),
        console_buffer: parseInt($('cfg-buf').value, 10) || 3000,
      };
      if ($('cfg-pwd').value) body.password = $('cfg-pwd').value;
      await api('/api/settings', { method: 'POST', body });
      toast('面板设置已保存', 'success');
      closeModal();
      boot();
    } catch (err) { toast(err.message, 'error'); }
  };
};

/* ============================================================ 启动按钮 */
async function powerAction(action, confirmText) {
  if (!state.current) return;
  if (confirmText && !confirm(confirmText)) return;
  state.busy = true;
  try {
    await api(`/api/instances/${enc(state.current)}/${action}`, { method: 'POST' });
    toast({ start: '已发送启动指令', stop: '已发送停止指令', kill: '已强制结束进程',
            restart: '正在重启…' }[action] || '完成',
          action === 'start' ? 'success' : 'warn');
    setTimeout(boot, 600);
  } catch (err) { toast(err.message, 'error'); }
  state.busy = false;
}
$('act-start').onclick = () => powerAction('start');
$('act-stop').onclick = () => powerAction('stop');
$('act-kill').onclick = () => powerAction('kill', '强制结束进程可能导致世界数据未保存，确定吗？');
$('act-restart').onclick = () => powerAction('restart', '重启会先保存并关闭服务端，玩家会掉线，确定吗？');

/* ============================================================ 初始化 */
(async function init() {
  renderQuickCmds();
  try {
    const s = await api('/api/session');
    if (s.auth_enabled && !s.logged_in) { showLogin(); return; }
  } catch (e) { /* 忽略 */ }
  boot();
})();
