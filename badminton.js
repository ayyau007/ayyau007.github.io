const playersEl = document.getElementById('players');
const queueEl = document.getElementById('queue');
const nextEl = document.getElementById('nextGame');
const courtsEl = document.getElementById('courts');
const courtCountSelect = document.getElementById('courtCount');
const endSessionBtn = document.getElementById('endSession');

let pool = JSON.parse(localStorage.getItem('playerPool') || '[]');
let state = JSON.parse(localStorage.getItem('badmintonState') || '{}');
if (!state.players) state.players = [...pool];
if (!state.queue) state.queue = [];
if (!state.nextGame) state.nextGame = [];
if (!state.courts) state.courts = [[], []]; // default 2 courts

courtCountSelect.value = state.courts.length;

playersEl.ondrop = queueEl.ondrop = nextEl.ondrop = drop;
playersEl.ondragover = queueEl.ondragover = nextEl.ondragover = allowDrop;

let touchDragData = null;
let touchTimer = null;
let touchStartX = 0;
let touchStartY = 0;
let pendingTouchData = null;

function handleTouchStart(e) {
  if (e.target.tagName === 'BUTTON' || e.target.tagName === 'INPUT') return;
  const el = e.currentTarget;
  const touch = e.touches[0];
  touchStartX = touch.clientX;
  touchStartY = touch.clientY;
  pendingTouchData = {
    location: el.dataset.location,
    index1: el.dataset.index1,
    index2: el.dataset.index2
  };
  touchTimer = setTimeout(() => {
    touchDragData = pendingTouchData;
  }, 300);
}

function handleTouchMove(e) {
  if (touchTimer) {
    const touch = e.touches[0];
    if (Math.abs(touch.clientX - touchStartX) > 10 || Math.abs(touch.clientY - touchStartY) > 10) {
      clearTimeout(touchTimer);
      touchTimer = null;
      pendingTouchData = null;
    }
  }
  if (touchDragData) e.preventDefault();
}

function handleTouchEnd(e) {
  clearTimeout(touchTimer);
  touchTimer = null;
  if (!touchDragData) {
    pendingTouchData = null;
    return;
  }
  const touch = e.changedTouches[0];
  const target = document.elementFromPoint(touch.clientX, touch.clientY);
  const container = target && target.closest('#players, #queue, #nextGame, .court');
  if (container) {
    drop({
      preventDefault: () => {},
      currentTarget: container,
      target,
      dataTransfer: { getData: () => JSON.stringify(touchDragData) }
    });
  }
  touchDragData = null;
  pendingTouchData = null;
}

function save() {
  localStorage.setItem('badmintonState', JSON.stringify(state));
  localStorage.setItem('playerPool', JSON.stringify(pool));
}

function setPlayerWidths() {
  const containers = [
    playersEl,
    queueEl,
    nextEl,
    ...document.querySelectorAll('.court')
  ];
  containers.forEach(container => {
    const els = container.querySelectorAll('.player');
    if (!els.length) return;
    els.forEach(el => (el.style.width = 'auto'));
    let maxName = 0;
    let maxExtra = 0;
    els.forEach(el => {
      const span = el.querySelector('span');
      const nameWidth = span.getBoundingClientRect().width;
      if (nameWidth > maxName) maxName = nameWidth;
      const extra = el.getBoundingClientRect().width - nameWidth;
      if (extra > maxExtra) maxExtra = extra;
    });
    const total = Math.min(Math.ceil(maxName + maxExtra), 300);
    els.forEach(el => (el.style.width = total + 'px'));
  });
}

function makePlayer(name, loc, idx1, idx2, label) {
  const div = document.createElement('div');
  div.className = 'player';
  div.draggable = true;
  div.dataset.location = loc;
  div.dataset.index1 = idx1;
  if (idx2 !== undefined) div.dataset.index2 = idx2;
  div.ondragstart = drag;
  div.addEventListener('touchstart', handleTouchStart, {passive:true});
  if (loc === 'players' || loc === 'queue') {
    const cb = document.createElement('input');
    cb.type = 'checkbox';
    cb.className = 'select';
    cb.dataset.index = idx1;
    cb.onclick = e => e.stopPropagation();
    div.appendChild(cb);
  }
  const span = document.createElement('span');
  span.textContent = label || name;
  div.appendChild(span);
  if (loc === 'players') {
    const toQueueBtn = document.createElement('button');
    toQueueBtn.textContent = 'To Queue';
    toQueueBtn.className = 'toQueue';
    toQueueBtn.onclick = e => {
      e.stopPropagation();
      state.queue.push(state.players.splice(idx1, 1)[0]);
      save();
      render();
    };
    div.appendChild(toQueueBtn);

    const del = document.createElement('button');
    del.textContent = 'x';
    del.className = 'delete';
    del.onclick = e => {
      e.stopPropagation();
      const removed = state.players.splice(idx1, 1)[0];
      const pIdx = pool.indexOf(removed);
      if (pIdx !== -1) pool.splice(pIdx, 1);
      save();
      render();
    };
    div.appendChild(del);
  } else if (loc === 'queue') {
    const nextBtn = document.createElement('button');
    nextBtn.textContent = 'Next Game';
    nextBtn.className = 'toNext';
    nextBtn.onclick = e => {
      e.stopPropagation();
      if (state.nextGame.length < 4) {
        state.nextGame.push(state.queue.splice(idx1, 1)[0]);
        save();
        render();
      }
    };
    div.appendChild(nextBtn);
  }
  return div;
}

function render() {
  playersEl.innerHTML = '<h3>Players</h3>';
  const addDiv = document.createElement('div');
  const playerInput = document.createElement('input');
  playerInput.id = 'playerName';
  playerInput.placeholder = 'Player name';
  const addBtn = document.createElement('button');
  addBtn.id = 'addPlayerBtn';
  addBtn.textContent = 'Add Player';
  const importBtn = document.createElement('button');
  importBtn.id = 'importPlayersBtn';
  importBtn.textContent = 'Import Players';
  const exportBtn = document.createElement('button');
  exportBtn.id = 'exportPlayersBtn';
  exportBtn.textContent = 'Export Players';
  const importInput = document.createElement('input');
  importInput.type = 'file';
  importInput.accept = 'text/plain';
  importInput.style.display = 'none';
  addDiv.appendChild(playerInput);
  addDiv.appendChild(addBtn);
  addDiv.appendChild(importBtn);
  addDiv.appendChild(exportBtn);
  const toQueueBtn = document.createElement('button');
  toQueueBtn.id = 'toQueueBtn';
  toQueueBtn.textContent = 'To Queue';
  addDiv.appendChild(toQueueBtn);
  playersEl.appendChild(addDiv);
  playersEl.appendChild(importInput);
  addBtn.onclick = () => {
    const name = playerInput.value.trim();
    if (name) {
      if (!pool.includes(name)) pool.push(name);
      if (!state.players.includes(name) && !state.queue.includes(name) && !state.nextGame.includes(name) && !state.courts.some(c => c.includes(name))) {
        state.players.push(name);
      }
      playerInput.value = '';
      save();
      render();
    }
  };
  importBtn.onclick = () => importInput.click();
  importInput.onchange = async () => {
    const file = importInput.files[0];
    if (!file) return;
    const text = await file.text();
    text.split(/\r?\n/).map(s => s.trim()).filter(Boolean).forEach(n => {
      if (!pool.includes(n)) pool.push(n);
      if (!state.players.includes(n) && !state.queue.includes(n) && !state.nextGame.includes(n) && !state.courts.some(c => c.includes(n))) {
        state.players.push(n);
      }
    });
    importInput.value = '';
    save();
    render();
  };
  exportBtn.onclick = () => {
    const content = pool.join('\n');
    const blob = new Blob([content], {type: 'text/plain'});
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'players.txt';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  };
  state.players.forEach((p, i) => playersEl.appendChild(makePlayer(p, 'players', i)));
  toQueueBtn.onclick = () => {
    const selected = Array.from(playersEl.querySelectorAll('input.select:checked'))
      .map(cb => parseInt(cb.dataset.index, 10)).sort((a, b) => b - a);
    selected.forEach(idx => state.queue.push(state.players.splice(idx, 1)[0]));
    save();
    render();
  };

  queueEl.innerHTML = '<h3>Queue</h3>';
  const toNextBtn = document.createElement('button');
  toNextBtn.id = 'toNextBtn';
  toNextBtn.textContent = 'Next Game';
  queueEl.appendChild(toNextBtn);
  state.queue.forEach((p, i) => queueEl.appendChild(makePlayer(p, 'queue', i, undefined, `${i + 1}. ${p}`)));
  toNextBtn.onclick = () => {
    const selected = Array.from(queueEl.querySelectorAll('input.select:checked'))
      .map(cb => parseInt(cb.dataset.index, 10)).sort((a, b) => b - a);
    selected.forEach(idx => {
      if (state.nextGame.length < 4) state.nextGame.push(state.queue.splice(idx, 1)[0]);
    });
    save();
    render();
  };

  nextEl.innerHTML = '<h3>Next Game</h3>';
  state.nextGame.forEach((p, i) => nextEl.appendChild(makePlayer(p, 'next', i)));

  courtsEl.innerHTML = '';
  state.courts.forEach((court, i) => {
    const div = document.createElement('div');
    div.className = 'court';
    div.dataset.court = i;
    div.ondrop = drop;
    div.ondragover = allowDrop;
    div.innerHTML = `<div class="court-title">Court ${i + 1} <button class="in">In</button> <button class="out">Out</button></div>`;
    court.forEach((p, j) => div.appendChild(makePlayer(p, 'courts', i, j)));
    const inBtn = div.querySelector('.in');
    const outBtn = div.querySelector('.out');
    inBtn.onclick = () => {
      if (state.courts[i].length === 0 && state.nextGame.length) {
        state.courts[i] = state.nextGame.splice(0, 4);
        save();
        render();
      }
    };
    outBtn.onclick = () => {
      state.queue.push(...state.courts[i]);
      state.courts[i] = [];
      save();
      render();
    };
    courtsEl.appendChild(div);
  });
  setPlayerWidths();
}

function allowDrop(e) {
  e.preventDefault();
}

function drag(e) {
  e.dataTransfer.setData('text/plain', JSON.stringify({
    location: e.target.dataset.location,
    index1: e.target.dataset.index1,
    index2: e.target.dataset.index2
  }));
}

function drop(e) {
  e.preventDefault();
  const data = JSON.parse(e.dataTransfer.getData('text/plain'));
  const srcIdx1 = data.index1 !== undefined ? parseInt(data.index1, 10) : undefined;
  const srcIdx2 = data.index2 !== undefined ? parseInt(data.index2, 10) : undefined;
  let name;
  if (data.location === 'players') {
    name = state.players.splice(srcIdx1, 1)[0];
  } else if (data.location === 'queue') {
    name = state.queue.splice(srcIdx1, 1)[0];
  } else if (data.location === 'next') {
    name = state.nextGame.splice(srcIdx1, 1)[0];
  } else if (data.location === 'courts') {
    name = state.courts[srcIdx1].splice(srcIdx2, 1)[0];
  }

  const container = e.currentTarget;
  const players = Array.from(container.querySelectorAll('.player'));
  const dropTarget = e.target.closest('.player');
  let dropIndex = players.length;
  if (dropTarget && dropTarget.parentElement === container) {
    dropIndex = players.indexOf(dropTarget);
  }

  const sameContainer =
    (data.location === 'players' && container.id === 'players') ||
    (data.location === 'queue' && container.id === 'queue') ||
    (data.location === 'next' && container.id === 'nextGame') ||
    (data.location === 'courts' && container.classList.contains('court') && parseInt(container.dataset.court, 10) === srcIdx1);

  const sourceIndex = data.location === 'courts' ? srcIdx2 : srcIdx1;
  if (sameContainer && dropIndex > sourceIndex) dropIndex--;

  if (container.id === 'players') {
    state.players.splice(dropIndex, 0, name);
  } else if (container.id === 'queue') {
    state.queue.splice(dropIndex, 0, name);
  } else if (container.id === 'nextGame') {
    if (state.nextGame.length < 4) {
      state.nextGame.splice(dropIndex, 0, name);
    } else {
      state.queue.push(name);
    }
  } else if (container.classList.contains('court')) {
    const idx = container.dataset.court;
    if (state.courts[idx].length < 4) {
      state.courts[idx].splice(dropIndex, 0, name);
    } else {
      state.queue.push(name);
    }
  }
  save();
  render();
}

courtCountSelect.onchange = () => {
  let n = parseInt(courtCountSelect.value, 10);
  state.courts = state.courts.slice(0, n);
  while (state.courts.length < n) state.courts.push([]);
  save();
  render();
};

render();
window.addEventListener('resize', setPlayerWidths);
window.addEventListener('touchmove', handleTouchMove, {passive:false});
window.addEventListener('touchend', handleTouchEnd);
window.addEventListener('touchcancel', handleTouchEnd);

endSessionBtn.onclick = () => {
  const all = [
    ...state.players,
    ...state.queue,
    ...state.nextGame,
    ...state.courts.flat()
  ];
  pool = Array.from(new Set([...pool, ...all]));
  state.players = [...pool];
  state.queue = [];
  state.nextGame = [];
  state.courts = state.courts.map(() => []);
  save();
  render();
};
