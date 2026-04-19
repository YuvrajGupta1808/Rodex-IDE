import { SSEClient } from './sse-client.js';
import { store } from './store.js';

// ── Bootstrap ──
const params = new URLSearchParams(location.search);
const sessionId = params.get('session') || sessionStorage.getItem('review_session');
const files = JSON.parse(sessionStorage.getItem('review_files') || '{}');

if (!sessionId) {
  location.href = '/';
}

store.sessionId = sessionId;
store.files = files;

document.getElementById('session-label').textContent = sessionId.slice(0, 8);

// ── Monaco Editor setup ──
let editor;
let currentDecorations = [];
const _appliedFixes = new Set();
const _modifiedFiles = new Set(); // tracks which files have unsaved edits
let _suppressModified = false;    // true while auto-fix is writing to editor
let _findingSeverityFilter = 'all';
let _selectedFindingId = null;
let _terminalCwd = '';

require.config({ paths: { vs: 'https://cdnjs.cloudflare.com/ajax/libs/monaco-editor/0.44.0/min/vs' } });
require(['vs/editor/editor.main'], () => {
  editor = monaco.editor.create(document.getElementById('monaco-editor'), {
    value: Object.values(files)[0] || '# Select a file to view',
    language: 'python',
    theme: 'vs-dark',
    fontSize: 13,
    fontFamily: "'Cascadia Code', 'Fira Code', Consolas, monospace",
    fontLigatures: true,
    minimap: { enabled: true },
    scrollBeyondLastLine: false,
    lineNumbers: 'on',
    glyphMargin: true,
    renderWhitespace: 'selection',
    automaticLayout: true,
    padding: { top: 8 },
    smoothScrolling: true,
    cursorBlinking: 'smooth',
    cursorSmoothCaretAnimation: 'on',
  });

  // Mark file modified when user types (not when auto-fix sets value)
  editor.onDidChangeModelContent(() => {
    if (store.activeFile && !_suppressModified) {
      _modifiedFiles.add(store.activeFile);
      renderTabBar();
    }
  });

  // Cmd+S / Ctrl+S saves current file into store
  editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyS, () => {
    saveActiveFile();
  });

  buildFileTree();
  if (Object.keys(files).length > 0) {
    openFile(Object.keys(files)[0]);
  }
  // Force layout after DOM settles
  requestAnimationFrame(() => editor.layout());
});

// ── File Tree ──
function buildFileTree() {
  const tree = document.getElementById('file-tree');
  tree.innerHTML = '';
  Object.keys(store.files).forEach(name => {
    const item = document.createElement('div');
    item.className = 'tree-item' + (name === store.activeFile ? ' active' : '');
    item.dataset.file = name;
    item.innerHTML = `<span class="tree-icon">${getFileIcon(name)}</span>${name}`;
    item.addEventListener('click', () => openFile(name));
    tree.appendChild(item);
  });
}

function openFile(name) {
  store.activeFile = name;
  if (editor) {
    editor.setValue(store.files[name] || '');
    const lang = name.endsWith('.py') ? 'python' : name.endsWith('.js') ? 'javascript' : 'plaintext';
    monaco.editor.setModelLanguage(editor.getModel(), lang);
    applyFindingDecorations();
  }
  // Update tab bar
  renderTabBar();
  // Update tree selection
  document.querySelectorAll('.tree-item').forEach(el => {
    el.classList.toggle('active', el.dataset.file === name);
  });
}

function saveActiveFile() {
  if (!editor || !store.activeFile) return;
  store.files[store.activeFile] = editor.getValue();
  _modifiedFiles.delete(store.activeFile);
  renderTabBar();
}

function renderTabBar() {
  const bar = document.getElementById('tab-bar');
  bar.innerHTML = '';
  Object.keys(store.files).forEach(name => {
    const isModified = _modifiedFiles.has(name);
    const tab = document.createElement('div');
    tab.className = 'tab' + (name === store.activeFile ? ' active' : '');
    tab.innerHTML = `<span>${getFileIcon(name)} ${name}${isModified ? ' <span class="tab-modified" title="Unsaved changes — Cmd+S to save">●</span>' : ''}</span>
      <button class="tab-close" data-file="${name}">✕</button>`;
    tab.addEventListener('click', (e) => {
      if (!e.target.closest('.tab-close')) openFile(name);
    });
    tab.querySelector('.tab-close').addEventListener('click', (e) => {
      e.stopPropagation();
      delete store.files[name];
      if (store.activeFile === name) {
        const remaining = Object.keys(store.files);
        store.activeFile = remaining[0] || null;
        if (store.activeFile) openFile(store.activeFile);
      }
      buildFileTree();
      renderTabBar();
    });
    bar.appendChild(tab);
  });
}

function getFileIcon(name) {
  if (name.endsWith('.py')) return 'PY';
  if (name.endsWith('.js')) return 'JS';
  if (name.endsWith('.ts')) return 'TS';
  if (name.endsWith('.json')) return '{}';
  return 'FILE';
}

// ── Add file button ──
document.getElementById('btn-add-file').addEventListener('click', () => {
  document.getElementById('hidden-file-input-ide').click();
});

document.getElementById('hidden-file-input-ide').addEventListener('change', async (e) => {
  for (const f of Array.from(e.target.files)) {
    store.files[f.name] = await f.text();
  }
  buildFileTree();
  renderTabBar();
});

// ── Run button ──
const btnRun = document.getElementById('btn-run');
let sseClient = null;

btnRun.addEventListener('click', async () => {
  if (btnRun.classList.contains('running')) return;
  btnRun.classList.add('running');
  btnRun.textContent = 'Analyzing...';

  // Reset state
  store.findings = [];
  store.thoughts = [];
  store.toolCalls = [];
  store.planSteps = [];
  store.fixProposals = {};
  store.fixVerifications = {};
  store.fixedFiles = {};
  _appliedFixes.clear();
  clearFindingsUI();

  // Save current editor content before submitting
  saveActiveFile();
  _modifiedFiles.clear();

  try {
    const res = await fetch('/api/review', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ files: store.files }),
    });
    const data = await res.json();
    const newSessionId = data.session_id;
    store.sessionId = newSessionId;
    sessionStorage.setItem('review_session', newSessionId);
    document.getElementById('session-label').textContent = newSessionId.slice(0, 8);

    // Connect SSE
    if (sseClient) sseClient.close();
    sseClient = new SSEClient(newSessionId, (event) => {
      store.handleEvent(event);
    }, (status) => {
      if (status === 'closed' || status === 'connected') {
        if (status === 'closed') {
          btnRun.classList.remove('running');
          btnRun.textContent = 'Run Analysis';
        }
      }
    });
    sseClient.connect();

  } catch (err) {
    btnRun.classList.remove('running');
    btnRun.textContent = 'Run Analysis';
    alert(`Error: ${err.message}`);
  }
});

// ── Store subscribers ──
store.subscribe((key) => {
  if (key === 'agents') {
    renderAgents();
  }
  if (key === 'plan') renderPlan();
  if (key === 'thoughts') renderThoughts();
  if (key === 'tools') renderToolLog();
  if (key === 'findings' || key === 'fixes') {
    renderFindings();
    applyVerifiedFixes();
  }
  if (key === 'completed') {
    btnRun.classList.remove('running');
    btnRun.textContent = 'Run Analysis';
    saveAllFixedFiles();   // persist all auto-fixed content before next run
    applyFindingDecorations();
    renderFindings();
  }
});

// ── Render agents ──
function renderAgents() {
  Object.entries(store.agents).forEach(([id, { state }]) => {
    const dot = document.getElementById(`dot-${id}`);
    const label = document.getElementById(`status-${id}`);
    if (!dot || !label) return;
    dot.className = `dot dot-${state}`;
    label.className = `agent-status-label ${state}`;
    label.textContent = state.toUpperCase().replace('_', ' ');
  });
}

// ── Render plan ──
function renderPlan() {
  const body = document.getElementById('plan-body');
  if (!store.planSteps.length) return;
  body.innerHTML = store.planSteps.map(s => `
    <div class="plan-step ${s.status === 'done' ? 'done' : s.status === 'active' ? 'active' : ''}">
      <div class="plan-step-num">${s.status === 'done' ? 'DONE' : s.step}</div>
      <span>${s.description}</span>
    </div>
  `).join('');
}

// ── Render thoughts (Output panel) ──
function renderThoughts() {
  const stream = document.getElementById('thought-stream');
  if (!stream) return;
  const last = store.thoughts.slice(-50);
  if (!last.length) {
    stream.innerHTML = `<div class="empty-state"><span>Agent reasoning will appear here</span></div>`;
    return;
  }
  stream.innerHTML = last.map((t, i) => `
    <div class="thought-line ${i === last.length - 1 ? 'new' : ''}">
      <span style="color:var(--text-muted);font-size:10px">[${t.agentId}]</span>
      ${escapeHtml(t.text || '')}
    </div>
  `).join('');
  stream.scrollTop = stream.scrollHeight;
}

// ── Interactive terminal (Terminal tab) ──
function initTerminal() {
  const form = document.getElementById('terminal-form');
  const input = document.getElementById('terminal-input');
  if (!form || !input) return;

  updateTerminalPrompt();
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const command = input.value.trim();
    if (!command) return;

    appendTerminalLine('command', `${promptText()} ${command}`);
    input.value = '';

    try {
      const res = await fetch('/api/terminal/exec', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ session_id: store.sessionId, command }),
      });
      if (!res.ok) {
        appendTerminalLine('stderr', `Request failed (${res.status})`);
        return;
      }

      const data = await res.json();
      _terminalCwd = data.cwd || _terminalCwd;
      updateTerminalPrompt();

      if (data.stdout === '__CLEAR__') {
        clearTerminalOutput();
        return;
      }
      if (data.stdout) appendTerminalLine('stdout', data.stdout);
      if (data.stderr) appendTerminalLine('stderr', data.stderr);
    } catch (err) {
      appendTerminalLine('stderr', err.message || 'Command failed');
    }
  });

  input.addEventListener('keydown', (e) => {
    if (e.key.toLowerCase() === 'l' && (e.metaKey || e.ctrlKey)) {
      e.preventDefault();
      clearTerminalOutput();
    }
  });
}

function terminalOutputEl() {
  return document.getElementById('terminal-output');
}

function clearTerminalOutput() {
  const output = terminalOutputEl();
  if (!output) return;
  output.innerHTML = '';
}

function appendTerminalLine(kind, text) {
  const output = terminalOutputEl();
  if (!output) return;
  if (output.querySelector('.empty-state')) output.innerHTML = '';
  const line = document.createElement('div');
  line.className = `terminal-line terminal-line-${kind}`;
  line.textContent = text;
  output.appendChild(line);
  output.scrollTop = output.scrollHeight;
}

function promptText() {
  return _terminalCwd ? `${_terminalCwd} $` : '$';
}

function updateTerminalPrompt() {
  const prompt = document.getElementById('terminal-prompt');
  if (prompt) prompt.textContent = promptText();
}

// ── Render tool log (Log panel) ──
function renderToolLog() {
  const log = document.getElementById('tool-log');
  if (!log) return;
  if (!store.toolCalls.length) {
    log.innerHTML = `<div class="empty-state">
      <span>No tool calls yet</span>
    </div>`;
    return;
  }
  log.innerHTML = store.toolCalls.slice(0, 20).map(t => `
    <div class="tool-log-item">
      <div class="tool-log-header">
        <span style="color:var(--text-muted);font-size:10px">${t.ts}</span>
        <span class="tool-log-name">${t.toolName}</span>
        <span style="color:var(--text-secondary);font-size:10px">[${t.agentId}]</span>
        ${t.durationMs !== null ? `<span class="tool-log-duration">${t.durationMs}ms</span>` : ''}
        ${t.ok !== null ? `<span class="${t.ok ? 'tool-log-ok' : 'tool-log-err'}">${t.ok ? 'OK' : 'ERR'}</span>` : ''}
      </div>
      <div class="tool-log-output">${escapeHtml(t.output != null ? String(t.output) : 'Running...')}</div>
    </div>
  `).join('');
}

// ── Render findings (Problems tab) ──
function renderFindings() {
  const list = document.getElementById('findings-list');
  const count = document.getElementById('findings-count');
  const findings = store.findings;
  const severityCounts = findings.reduce((acc, finding) => {
    acc[finding.severity] = (acc[finding.severity] || 0) + 1;
    return acc;
  }, { critical: 0, high: 0, medium: 0, low: 0, info: 0 });

  renderFindingControls(severityCounts, findings.length);
  count.textContent = store.findings.length;

  if (!store.findings.length) {
    list.innerHTML = `
      <div class="empty-state">
        <span>Run analysis to see problems</span>
      </div>`;
    return;
  }

  const visibleFindings = findings.filter(f => _findingSeverityFilter === 'all' || f.severity === _findingSeverityFilter);
  if (_selectedFindingId && !visibleFindings.some(f => f.finding_id === _selectedFindingId)) {
    _selectedFindingId = null;
  }

  if (!visibleFindings.length) {
    list.innerHTML = `
      <div class="empty-state">
        <span>No ${_findingSeverityFilter} problems found</span>
      </div>`;
    return;
  }

  if (!_selectedFindingId) {
    _selectedFindingId = visibleFindings[0].finding_id;
  }

  list.innerHTML = visibleFindings.map(f => {
    const fix = store.fixProposals[f.finding_id];
    const ver = store.fixVerifications[f.finding_id];
    let fixStatus = 'none';
    if (ver) fixStatus = ver.verification_passed ? 'verified' : 'failed';
    else if (fix) fixStatus = 'pending';

    const fixBtn = fixStatus === 'none'
      ? `<button class="btn-fix" data-finding="${f.finding_id}">Fix</button>`
      : fixStatus === 'pending'
      ? `<button class="btn-fix pending" disabled>Pending...</button>`
      : fixStatus === 'verified'
      ? `<button class="btn-fix verified" disabled>Fixed</button>`
      : `<button class="btn-fix failed" disabled>Failed</button>`;

    return `
    <div class="finding-item ${f.finding_id === _selectedFindingId ? 'active' : ''}" data-finding="${f.finding_id}" data-file="${f.file}" data-line="${f.line}">
      <div class="finding-severity-rail finding-severity-rail-${f.severity}"></div>
      <div class="finding-left">
        <div class="finding-title">
          <span class="badge badge-${f.severity}">${f.severity}</span>
          <span>${f.category.replace(/_/g, ' ')}</span>
        </div>
        <div class="finding-desc">${escapeHtml(f.description)}</div>
        <div class="finding-meta">${f.file}:${f.line}</div>
      </div>
      <div class="finding-actions">${fixBtn}</div>
    </div>`;
  }).join('');

  // Click to navigate
  list.querySelectorAll('.finding-item').forEach(el => {
    el.addEventListener('click', (e) => {
      if (e.target.closest('.btn-fix')) return;
      _selectedFindingId = el.dataset.finding;
      list.querySelectorAll('.finding-item').forEach(node => node.classList.toggle('active', node.dataset.finding === _selectedFindingId));
      const file = el.dataset.file;
      const line = parseInt(el.dataset.line);
      if (store.files[file]) {
        openFile(file);
        setTimeout(() => editor?.revealLineInCenter(line), 100);
      }
    });
  });

  // Fix button click
  list.querySelectorAll('.btn-fix:not([disabled])').forEach(btn => {
    btn.addEventListener('click', () => showFixPopup(btn.dataset.finding));
  });
}

function clearFindingsUI() {
  document.getElementById('findings-list').innerHTML = `
    <div class="empty-state"><span>Analyzing...</span></div>`;
  _findingSeverityFilter = 'all';
  _selectedFindingId = null;
  renderFindingControls({ critical: 0, high: 0, medium: 0, low: 0, info: 0 }, 0);
  document.getElementById('findings-count').textContent = '0';
  document.getElementById('thought-stream').innerHTML = `
    <div class="empty-state"><span>Agent reasoning will appear here</span></div>`;
  document.getElementById('tool-log').innerHTML = `
    <div class="empty-state"><span>No tool calls yet</span></div>`;
  document.getElementById('plan-body').innerHTML = '';
  Object.keys(store.agents).forEach(id => {
    store.agents[id] = { state: 'idle', findingCount: 0 };
  });
  renderAgents();
  renderThoughts();
  renderToolLog();
}

function renderFindingControls(severityCounts, totalCount) {
  const summary = document.getElementById('findings-severity-summary');
  const filters = document.getElementById('findings-filter-row');
  if (!summary || !filters) return;

  const severities = ['critical', 'high', 'medium', 'low', 'info'];
  summary.innerHTML = severities.map(severity => `
    <span class="severity-pill severity-pill-${severity}">
      <span class="severity-pill-label">${severity}</span>
      <span class="severity-pill-value">${severityCounts[severity] || 0}</span>
    </span>
  `).join('');

  const filterOptions = [
    { id: 'all', label: 'All', count: totalCount },
    ...severities.map(severity => ({
      id: severity,
      label: severity[0].toUpperCase() + severity.slice(1),
      count: severityCounts[severity] || 0,
    })),
  ];

  filters.innerHTML = filterOptions.map(filter => `
    <button class="finding-filter-chip ${_findingSeverityFilter === filter.id ? 'active' : ''}" data-filter="${filter.id}">
      ${filter.label} <span>${filter.count}</span>
    </button>
  `).join('');

  filters.querySelectorAll('.finding-filter-chip').forEach(chip => {
    chip.addEventListener('click', () => {
      const nextFilter = chip.dataset.filter;
      if (_findingSeverityFilter === nextFilter) return;
      _findingSeverityFilter = nextFilter;
      _selectedFindingId = null;
      renderFindings();
    });
  });
}

// ── applyVerifiedFixes: no-op now — saveAllFixedFiles handles everything at completion ──
function applyVerifiedFixes() {}

// ── Auto-save all files after review completes ──
// Uses the backend's authoritative fixed_files dict (from review_completed payload).
// This is the single source of truth — overwrites store.files for ALL files so
// the next Run Analysis sends exactly what the backend produced after applying fixes.
function saveAllFixedFiles() {
  const fixed = store.fixedFiles || {};
  if (Object.keys(fixed).length === 0) return;

  Object.entries(fixed).forEach(([filename, content]) => {
    if (content == null) return;
    store.files[filename] = content;

    // Silently update editor if this is the active file
    if (filename === store.activeFile && editor) {
      _suppressModified = true;
      editor.setValue(content);
      _suppressModified = false;
    }
  });

  _appliedFixes.clear();
  _modifiedFiles.clear();
  renderTabBar();
}

// ── Monaco decorations for findings ──
function applyFindingDecorations() {
  if (!editor) return;
  injectDecorationStyles();

  const relevant = store.findings.filter(f => f.file === store.activeFile);
  const newDecorations = [];

  relevant.forEach(f => {
    const ver = store.fixVerifications[f.finding_id];
    const isFixed = ver?.verification_passed;

    if (isFixed) {
      // Fixed: fixed indicator glyph only — no highlight, no strikethrough
      newDecorations.push({
        range: new monaco.Range(f.line, 1, f.line, 1),
        options: {
          glyphMarginClassName: 'finding-glyph-fixed',
          glyphMarginHoverMessage: { value: `**Fixed** — ${f.category.replace(/_/g, ' ')}` },
          overviewRuler: { color: '#4ec9b0', position: monaco.editor.OverviewRulerLane.Right },
          minimap: { color: '#4ec9b0', position: 1 },
        },
      });
    } else {
      // Unfixed: squiggly underline + glyph dot, no background fill
      newDecorations.push({
        range: new monaco.Range(f.line, 1, f.line, 9999),
        options: {
          isWholeLine: false,
          glyphMarginClassName: `finding-glyph-${f.severity}`,
          glyphMarginHoverMessage: { value: `**${f.severity.toUpperCase()} — ${f.category.replace(/_/g, ' ')}**\n\n${f.description}` },
          inlineClassName: `finding-squiggle-${f.severity}`,
          overviewRuler: { color: severityColor(f.severity), position: monaco.editor.OverviewRulerLane.Right },
          minimap: { color: severityColor(f.severity), position: 1 },
        },
      });
    }
  });

  currentDecorations = editor.deltaDecorations(currentDecorations, newDecorations);
}

function injectDecorationStyles() {
  if (document.getElementById('finding-decoration-styles')) return;
  const style = document.createElement('style');
  style.id = 'finding-decoration-styles';
  style.textContent = `
    /* Squiggly underlines — no background, professional IDE style */
    .finding-squiggle-critical { text-decoration: underline wavy #f44747; text-underline-offset: 3px; }
    .finding-squiggle-high     { text-decoration: underline wavy #f48771; text-underline-offset: 3px; }
    .finding-squiggle-medium   { text-decoration: underline wavy #dcdcaa; text-underline-offset: 3px; }
    .finding-squiggle-low      { text-decoration: underline dotted #4ec9b0; text-underline-offset: 3px; }
    .finding-squiggle-info     { text-decoration: underline dotted #569cd6; text-underline-offset: 3px; }

    /* Glyph margin indicators */
    .finding-glyph-critical::before { content: "C"; color: #f44747; font-size: 9px; margin-left: 3px; font-weight: 700; }
    .finding-glyph-high::before     { content: "H"; color: #f48771; font-size: 9px; margin-left: 3px; font-weight: 700; }
    .finding-glyph-medium::before   { content: "M"; color: #dcdcaa; font-size: 9px; margin-left: 3px; font-weight: 700; }
    .finding-glyph-low::before      { content: "L"; color: #4ec9b0; font-size: 9px; margin-left: 3px; font-weight: 700; }
    .finding-glyph-info::before     { content: "I"; color: #569cd6; font-size: 9px; margin-left: 3px; font-weight: 700; }
    .finding-glyph-fixed::before    { content: "OK"; color: #4ec9b0; font-size: 9px; font-weight: 700; margin-left: 1px; }
  `;
  document.head.appendChild(style);
}

// ── Fix popup ──
let activeFindingId = null;
const fixPopup = document.getElementById('fix-popup');

function showFixPopup(findingId) {
  const finding = store.findings.find(f => f.finding_id === findingId);
  const proposal = store.fixProposals[findingId];
  if (!finding) return;

  activeFindingId = findingId;
  const body = document.getElementById('fix-popup-body');

  if (proposal) {
    body.innerHTML = `<strong>Fix:</strong> ${escapeHtml(proposal.explanation)}<br/><br/>
      <code style="font-size:10px;color:var(--green)">${escapeHtml(proposal.proposed_fix.slice(0, 200))}</code>`;
  } else {
    body.textContent = `${finding.category}: ${finding.description}`;
  }

  // Position near the finding line in editor
  if (editor) {
    const lineHeight = editor.getOption(monaco.editor.EditorOption.lineHeight);
    const top = (finding.line - 1) * lineHeight + 40;
    fixPopup.style.top = `${Math.min(top, window.innerHeight - 200)}px`;
    fixPopup.style.left = '220px';
  }
  fixPopup.style.display = 'block';
}

document.getElementById('fix-popup-dismiss').addEventListener('click', () => {
  fixPopup.style.display = 'none';
  activeFindingId = null;
});

document.getElementById('fix-popup-apply').addEventListener('click', async () => {
  if (!activeFindingId) return;
  const proposal = store.fixProposals[activeFindingId];
  const finding = store.findings.find(f => f.finding_id === activeFindingId);
  if (proposal && finding && store.files[finding.file]) {
    const patched = store.files[finding.file].replace(proposal.original_code, proposal.proposed_fix, 1);
    store.files[finding.file] = patched;
    if (store.activeFile === finding.file && editor) {
      editor.setValue(patched);
    }
  }
  fixPopup.style.display = 'none';
  activeFindingId = null;
});

// ── Apply fixed files to editor ──
function applyFixedFiles() {
  const fixedFiles = store.fixedFiles;
  console.log('applyFixedFiles called, fixedFiles:', fixedFiles);
  console.log('store.files:', Object.keys(store.files));
  console.log('store.activeFile:', store.activeFile);

  if (!fixedFiles || Object.keys(fixedFiles).length === 0) {
    console.log('No fixed files to apply');
    return;
  }

  // Update store.files with fixed content
  Object.entries(fixedFiles).forEach(([filename, content]) => {
    console.log(`Processing fixed file: ${filename}, content length: ${content?.length}`);

    // Try exact match first
    let targetFilename = filename;
    if (store.files[filename] === undefined) {
      // Try to find a matching file (in case of path differences)
      const storeFileKeys = Object.keys(store.files);
      const match = storeFileKeys.find(k => k.endsWith(filename) || filename.endsWith(k));
      if (match) {
        console.log(`Found matching file: ${match} for ${filename}`);
        targetFilename = match;
      }
    }

    if (store.files[targetFilename] !== undefined) {
      store.files[targetFilename] = content;
      console.log(`Updated store.files[${targetFilename}]`);
      // If this is the currently active file, update the editor
      if (store.activeFile === targetFilename && editor) {
        console.log(`Updating editor with fixed content for ${targetFilename}`);
        editor.setValue(content);
        // Also trigger a re-render of the tab to show it's been modified
        renderTabBar();
      }
    } else {
      console.log(`File ${filename} (tried ${targetFilename}) not found in store.files`);
    }
  });
}

// ── Helpers ──
function escapeHtml(str) {
  return String(str).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

function severityColor(sev) {
  const map = { critical:'#f44747', high:'#f48771', medium:'#dcdcaa', low:'#4ec9b0', info:'#569cd6' };
  return map[sev] || '#666';
}

// ── Bottom tab switching ──
function initBottomTabs() {
  const tabs = document.querySelectorAll('.bottom-tab');
  const panes = document.querySelectorAll('.tab-pane');
  if (!tabs.length || !panes.length) return;

  const setActiveTab = (targetTab) => {
    tabs.forEach(t => t.classList.toggle('active', t.dataset.tab === targetTab));
    panes.forEach(p => p.classList.toggle('active', p.id === `pane-${targetTab}`));
  };

  tabs.forEach(tab => {
    tab.addEventListener('click', (e) => {
      e.preventDefault();
      const targetTab = tab.dataset.tab;
      if (!targetTab) return;
      setActiveTab(targetTab);
    });
  });
}

// ── Section toggle ──
function toggleSection(name) {
  const body = document.getElementById(`${name}-body`);
  const chevron = document.getElementById(`${name}-chevron`);
  if (!body) return;
  const hidden = body.style.display === 'none';
  body.style.display = hidden ? '' : 'none';
  if (chevron) chevron.textContent = hidden ? '[−]' : '[+]';
}
window.toggleSection = toggleSection;

// ── Navigation ──
document.getElementById('btn-home').addEventListener('click', () => { location.href = '/'; });

// ── Panel resize ──
const fileTreePanel = document.getElementById('file-tree-panel');
const agentsPanel   = document.getElementById('agents-panel');
const bottomPanel   = document.getElementById('bottom-panel');

let colFiletree  = 200;
let colAgents    = 320;
let rowBottom    = 220;

function applyPanelSizes() {
  if (fileTreePanel) fileTreePanel.style.width  = colFiletree  + 'px';
  if (agentsPanel)   agentsPanel.style.width    = colAgents    + 'px';
  if (bottomPanel)   bottomPanel.style.height   = rowBottom    + 'px';
  if (editor) editor.layout();
}

function makeDraggable(id, onDelta, axis) {
  const el = document.getElementById(id);
  if (!el) return;
  el.addEventListener('pointerdown', (e) => {
    e.preventDefault();
    e.stopPropagation();

    let last = axis === 'x' ? e.clientX : e.clientY;
    const pointerId = e.pointerId;

    el.classList.add('dragging');
    document.body.style.cursor = axis === 'x' ? 'col-resize' : 'row-resize';
    document.body.style.userSelect = 'none';

    const onMove = (mv) => {
      if (mv.pointerId !== pointerId) return;
      const cur = axis === 'x' ? mv.clientX : mv.clientY;
      onDelta(cur - last);
      last = cur;
    };

    const onUp = (up) => {
      if (up.pointerId !== pointerId) return;
      el.classList.remove('dragging');
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
      window.removeEventListener('pointermove', onMove);
      window.removeEventListener('pointerup', onUp);
      window.removeEventListener('pointercancel', onUp);
    };

    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup', onUp);
    window.addEventListener('pointercancel', onUp);
  });
}

makeDraggable('resize-filetree', (dx) => {
  colFiletree = Math.max(80, Math.min(500, colFiletree + dx));
  applyPanelSizes();
}, 'x');

makeDraggable('resize-agents', (dx) => {
  colAgents = Math.max(160, Math.min(600, colAgents - dx));
  applyPanelSizes();
}, 'x');

makeDraggable('resize-findings', (dy) => {
  rowBottom = Math.max(60, Math.min(600, rowBottom - dy));
  applyPanelSizes();
}, 'y');

applyPanelSizes();
window.addEventListener('resize', () => { if (editor) editor.layout(); });

// Initialize bottom tabs
initBottomTabs();
initTerminal();

// ── Font size controls ──
let currentFontSize = 13;
const fontLabel = document.getElementById('font-size-label');

function applyFontSize(size) {
  currentFontSize = Math.max(10, Math.min(24, size));
  fontLabel.textContent = currentFontSize;
  if (editor) editor.updateOptions({ fontSize: currentFontSize });
  document.documentElement.style.setProperty('--ui-font-size', `${currentFontSize}px`);
}

document.getElementById('btn-font-increase').addEventListener('click', () => applyFontSize(currentFontSize + 1));
document.getElementById('btn-font-decrease').addEventListener('click', () => applyFontSize(currentFontSize - 1));
