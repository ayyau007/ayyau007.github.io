const queueEl = document.getElementById('queue');
const stagingEl = document.getElementById('staging');
const courtsEl = document.getElementById('courts');
const playerInput = document.getElementById('playerName');
const addBtn = document.getElementById('addPlayerBtn');
const courtCountSelect = document.getElementById('courtCount');

let state = JSON.parse(localStorage.getItem('badmintonState') || '{}');
if (!state.players) state.players = [];
if (!state.staging) state.staging = [];
if (!state.courts) state.courts = [[], []]; // default 2 courts

courtCountSelect.value = state.courts.length;

function save() {
  localStorage.setItem('badmintonState', JSON.stringify(state));
}

function makePlayer(name, loc, idx1, idx2) {
  const div = document.createElement('div');
  div.className = 'player';
  div.textContent = name;
  div.draggable = true;
  div.dataset.location = loc;
  div.dataset.index1 = idx1;
  if (idx2 !== undefined) div.dataset.index2 = idx2;
  div.ondragstart = drag;
  return div;
}

function render() {
  queueEl.innerHTML = '<h3>Queue</h3>';
  state.players.forEach((p, i) => queueEl.appendChild(makePlayer(p, 'players', i)));

  stagingEl.innerHTML = '<h3>Staging</h3>';
  state.staging.forEach((p, i) => stagingEl.appendChild(makePlayer(p, 'staging', i)));

  courtsEl.innerHTML = '';
  state.courts.forEach((court, i) => {
    const div = document.createElement('div');
    div.className = 'court';
    div.dataset.court = i;
    div.ondrop = drop;
    div.ondragover = allowDrop;
    div.innerHTML = `<div class="court-title">Court ${i + 1}</div>`;
    court.forEach((p, j) => div.appendChild(makePlayer(p, 'courts', i, j)));
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
  } else if (data.location === 'staging') {
    name = state.staging.splice(data.index1, 1)[0];
  } else if (data.location === 'courts') {
    name = state.courts[data.index1].splice(data.index2, 1)[0];
  }

  const target = e.currentTarget;
  if (target.id === 'queue') {
    state.players.push(name);
  } else if (target.id === 'staging') {
    if (state.staging.length < 4) state.staging.push(name); else state.players.push(name);
  } else if (target.classList.contains('court')) {
    const idx = target.dataset.court;
    if (state.courts[idx].length < 4) state.courts[idx].push(name); else state.players.push(name);
  }
  save();
  render();
}

addBtn.onclick = () => {
  const name = playerInput.value.trim();
  if (name) {
    state.players.push(name);
    playerInput.value = '';
    save();
    render();
  }
};

courtCountSelect.onchange = () => {
  let n = parseInt(courtCountSelect.value, 10);
  state.courts = state.courts.slice(0, n);
  while (state.courts.length < n) state.courts.push([]);
  save();
  render();
};

render();
