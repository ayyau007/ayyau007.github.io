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

function save() {
  localStorage.setItem('badmintonState', JSON.stringify(state));
  localStorage.setItem('playerPool', JSON.stringify(pool));
}

function makePlayer(name, loc, idx1, idx2, label) {
  const div = document.createElement('div');
  div.className = 'player';
  div.draggable = true;
  div.dataset.location = loc;
  div.dataset.index1 = idx1;
  if (idx2 !== undefined) div.dataset.index2 = idx2;
  div.ondragstart = drag;
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
  const importInput = document.createElement('input');
  importInput.type = 'file';
  importInput.accept = 'text/plain';
  importInput.style.display = 'none';
  addDiv.appendChild(playerInput);
  addDiv.appendChild(addBtn);
  addDiv.appendChild(importBtn);
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
  state.players.forEach((p, i) => playersEl.appendChild(makePlayer(p, 'players', i)));
  const toQueueBtn = document.createElement('button');
  toQueueBtn.id = 'toQueueBtn';
  toQueueBtn.textContent = 'To Queue';
  playersEl.appendChild(toQueueBtn);
  toQueueBtn.onclick = () => {
    const selected = Array.from(playersEl.querySelectorAll('input.select:checked'))
      .map(cb => parseInt(cb.dataset.index, 10)).sort((a, b) => b - a);
    selected.forEach(idx => state.queue.push(state.players.splice(idx, 1)[0]));
    save();
    render();
  };

  queueEl.innerHTML = '<h3>Queue</h3>';
  state.queue.forEach((p, i) => queueEl.appendChild(makePlayer(p, 'queue', i, undefined, `${i + 1}. ${p}`)));
  const toNextBtn = document.createElement('button');
  toNextBtn.id = 'toNextBtn';
  toNextBtn.textContent = 'Next Game';
  queueEl.appendChild(toNextBtn);
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
  let name;
  if (data.location === 'players') {
    name = state.players.splice(data.index1, 1)[0];
  } else if (data.location === 'queue') {
    name = state.queue.splice(data.index1, 1)[0];
  } else if (data.location === 'next') {
    name = state.nextGame.splice(data.index1, 1)[0];
  } else if (data.location === 'courts') {
    name = state.courts[data.index1].splice(data.index2, 1)[0];
  }

  const target = e.currentTarget;
  if (target.id === 'players') {
    state.players.push(name);
  } else if (target.id === 'queue') {
    state.queue.push(name);
  } else if (target.id === 'nextGame') {
    if (state.nextGame.length < 4) state.nextGame.push(name); else state.queue.push(name);
  } else if (target.classList.contains('court')) {
    const idx = target.dataset.court;
    if (state.courts[idx].length < 4) state.courts[idx].push(name); else state.queue.push(name);
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

endSessionBtn.onclick = () => {
  state.players = [...pool];
  state.queue = [];
  state.nextGame = [];
  state.courts = state.courts.map(() => []);
  save();
  render();
};
