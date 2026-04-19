// Landing page logic: file upload, drag-drop, paste → navigate to IDE

const fileInput = document.getElementById('hidden-file-input');
const folderInput = document.getElementById('hidden-folder-input');
const pasteArea = document.getElementById('paste-textarea');
const btnAnalyze = document.getElementById('btn-analyze');

let pendingFiles = {}; // filename -> content

// Enable button when there's content
pasteArea.addEventListener('input', () => {
  btnAnalyze.disabled = pasteArea.value.trim().length === 0 && Object.keys(pendingFiles).length === 0;
});

// ── Card click handlers ──
document.getElementById('card-folder').addEventListener('click', () => folderInput.click());
document.getElementById('card-file').addEventListener('click', () => fileInput.click());
document.getElementById('card-write').addEventListener('click', () => pasteArea.focus());

// ── File input handlers ──
fileInput.addEventListener('change', async (e) => {
  pendingFiles = await readFiles(Array.from(e.target.files));
  btnAnalyze.disabled = false;
  updateCardLabel('card-file', `${Object.keys(pendingFiles).length} file(s) ready`);
});

folderInput.addEventListener('change', async (e) => {
  pendingFiles = await readFiles(Array.from(e.target.files));
  btnAnalyze.disabled = false;
  updateCardLabel('card-folder', `${Object.keys(pendingFiles).length} file(s) ready`);
});

// ── Drag and drop on cards ──
['card-folder', 'card-file'].forEach(id => {
  const card = document.getElementById(id);
  card.addEventListener('dragover', (e) => { e.preventDefault(); card.classList.add('drag-over'); });
  card.addEventListener('dragleave', () => card.classList.remove('drag-over'));
  card.addEventListener('drop', async (e) => {
    e.preventDefault();
    card.classList.remove('drag-over');
    const items = Array.from(e.dataTransfer.files);
    pendingFiles = await readFiles(items);
    btnAnalyze.disabled = false;
    updateCardLabel(id, `${Object.keys(pendingFiles).length} file(s) ready`);
  });
});

function updateCardLabel(cardId, text) {
  const card = document.getElementById(cardId);
  card.querySelector('.upload-card-sub').textContent = text;
  card.querySelector('.upload-card-icon').textContent = '✅';
}

async function readFiles(fileList) {
  const result = {};
  for (const file of fileList) {
    if (file.size > 500_000) continue; // skip huge files
    try {
      const text = await file.text();
      result[file.webkitRelativePath || file.name] = text;
    } catch (_) {}
  }
  return result;
}

// ── Analyze button ──
btnAnalyze.addEventListener('click', async () => {
  let files = { ...pendingFiles };
  const paste = pasteArea.value.trim();
  if (paste) {
    files['main.py'] = paste;
  }
  if (Object.keys(files).length === 0) return;

  btnAnalyze.disabled = true;
  btnAnalyze.textContent = 'Starting...';

  try {
    const res = await fetch('/api/review', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ files }),
    });
    const data = await res.json();
    // Navigate to IDE with session
    sessionStorage.setItem('review_session', data.session_id);
    sessionStorage.setItem('review_files', JSON.stringify(files));
    window.location.href = `/ide?session=${data.session_id}`;
  } catch (err) {
    btnAnalyze.disabled = false;
    btnAnalyze.textContent = 'Analyze Code →';
    alert(`Error: ${err.message}`);
  }
});
