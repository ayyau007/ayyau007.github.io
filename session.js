const STORAGE_KEY = "sessions";

const sessionList = document.getElementById("session-list");
const selectAll = document.getElementById("select-all");
const saveButton = document.getElementById("save-session");
const restoreSelectedButton = document.getElementById("restore-selected");
const deleteSelectedButton = document.getElementById("delete-selected");
const closeSelectedButton = document.getElementById("close-selected");
const exportJsonButton = document.getElementById("export-json");
const importJsonInput = document.getElementById("import-json");
const toolbar = document.querySelector(".toolbar");

let sessions = [];
const isExtensionEnv =
  typeof chrome !== "undefined" && Boolean(chrome.storage?.local);

const getSessions = async () => {
  if (!isExtensionEnv) {
    return [
      {
        id: "sample-session",
        name: "Sample Session",
        createdAt: new Date().toISOString(),
        windows: [
          {
            title: "Design Research",
            tabs: [
              {
                title: "Inspiration Board",
                url: "https://example.com/inspiration",
                favIconUrl: "",
              },
              {
                title: "User Interviews",
                url: "https://example.com/interviews",
                favIconUrl: "",
              },
            ],
          },
          {
            title: "Sprint Planning",
            tabs: [
              {
                title: "Backlog",
                url: "https://example.com/backlog",
                favIconUrl: "",
              },
            ],
          },
        ],
      },
    ];
  }
  const result = await chrome.storage.local.get(STORAGE_KEY);
  return result[STORAGE_KEY] || [];
};

const saveSessions = async (updated) => {
  if (isExtensionEnv) {
    await chrome.storage.local.set({ [STORAGE_KEY]: updated });
  }
  sessions = updated;
};

const buildSession = async (label = "Manual Save") => {
  if (!isExtensionEnv) {
    return {
      id: crypto.randomUUID(),
      name: label,
      createdAt: new Date().toISOString(),
      windows: [],
    };
  }
  const windows = await chrome.windows.getAll({
    populate: true,
    windowTypes: ["normal"],
  });

  return {
    id: crypto.randomUUID(),
    name: label,
    createdAt: new Date().toISOString(),
    windows: windows.map((window) => ({
      title: window.title || "Chrome Window",
      tabs: (window.tabs || []).map((tab) => ({
        title: tab.title || tab.pendingUrl || "Untitled Tab",
        url: tab.url || tab.pendingUrl || "",
        favIconUrl: tab.favIconUrl || "",
      })),
    })),
  };
};

const renderEmptyState = () => {
  const template = document.getElementById("empty-state");
  sessionList.innerHTML = "";
  sessionList.appendChild(template.content.cloneNode(true));
};

const createButton = (label, action, extra = {}) => {
  const button = document.createElement("button");
  button.textContent = label;
  button.dataset.action = action;
  Object.assign(button.dataset, extra);
  return button;
};

const createCheckbox = (extra = {}) => {
  const checkbox = document.createElement("input");
  checkbox.type = "checkbox";
  Object.assign(checkbox.dataset, extra);
  return checkbox;
};

const updateParentCheckboxes = (checkbox) => {
  let parent = checkbox.closest(".children")?.closest("[data-node]");
  while (parent) {
    const checkboxes = parent.querySelectorAll(
      ".children > [data-node] input[type='checkbox']"
    );
    const checked = Array.from(checkboxes).filter((input) => input.checked);
    const parentCheckbox = parent.querySelector("input[type='checkbox']");

    if (parentCheckbox) {
      parentCheckbox.checked = checked.length === checkboxes.length;
      parentCheckbox.indeterminate =
        checked.length > 0 && checked.length < checkboxes.length;
    }

    parent = parent.closest(".children")?.closest("[data-node]");
  }
};

const toggleChildren = (checkbox, checked) => {
  const node = checkbox.closest("[data-node]");
  if (!node) {
    return;
  }
  const descendants = node.querySelectorAll(".children input[type='checkbox']");
  descendants.forEach((child) => {
    child.checked = checked;
    child.indeterminate = false;
  });
};

const createSessionNode = (session, index) => {
  const node = document.createElement("div");
  node.className = "node";
  node.dataset.node = "session";
  node.dataset.sessionId = session.id;
  node.dataset.sessionIndex = index;

  const header = document.createElement("div");
  header.className = "node-header";

  const toggle = document.createElement("button");
  toggle.className = "toggle";
  toggle.textContent = "▾";

  const checkbox = createCheckbox({
    nodeType: "session",
    sessionId: session.id,
  });

  const title = document.createElement("div");
  title.className = "title";
  const titleText = document.createElement("span");
  titleText.className = "node-title";
  titleText.textContent = `${session.name} • ${new Date(
    session.createdAt
  ).toLocaleString()}`;
  title.appendChild(titleText);

  const actions = document.createElement("div");
  actions.className = "node-actions";
  actions.append(
    createButton("Restore", "restore", { nodeType: "session" }),
    createButton("Delete", "delete", { nodeType: "session" }),
    createButton("Close", "close", { nodeType: "session" })
  );

  header.append(toggle, checkbox, title, actions);

  const children = document.createElement("div");
  children.className = "children";
  session.windows.forEach((window, windowIndex) => {
    children.appendChild(createWindowNode(session, window, windowIndex));
  });

  toggle.addEventListener("click", () => {
    children.classList.toggle("collapsed");
    toggle.textContent = children.classList.contains("collapsed") ? "▸" : "▾";
  });

  checkbox.addEventListener("change", (event) => {
    toggleChildren(event.target, event.target.checked);
    updateParentCheckboxes(event.target);
  });

  node.append(header, children);
  return node;
};

const createWindowNode = (session, window, windowIndex) => {
  const node = document.createElement("div");
  node.className = "node";
  node.dataset.node = "window";
  node.dataset.sessionId = session.id;
  node.dataset.windowIndex = windowIndex;
  node.draggable = true;

  const header = document.createElement("div");
  header.className = "node-header";

  const toggle = document.createElement("button");
  toggle.className = "toggle";
  toggle.textContent = "▾";

  const checkbox = createCheckbox({
    nodeType: "window",
    sessionId: session.id,
    windowIndex,
  });

  const title = document.createElement("div");
  title.className = "title";
  const titleText = document.createElement("span");
  titleText.className = "node-title";
  titleText.textContent = `${window.title} (${window.tabs.length} tabs)`;
  title.appendChild(titleText);

  const actions = document.createElement("div");
  actions.className = "node-actions";
  actions.append(
    createButton("Restore", "restore", { nodeType: "window" }),
    createButton("Delete", "delete", { nodeType: "window" }),
    createButton("Close", "close", { nodeType: "window" })
  );

  header.append(toggle, checkbox, title, actions);

  const children = document.createElement("div");
  children.className = "children";
  window.tabs.forEach((tab, tabIndex) => {
    children.appendChild(createTabNode(session, tab, windowIndex, tabIndex));
  });

  toggle.addEventListener("click", () => {
    children.classList.toggle("collapsed");
    toggle.textContent = children.classList.contains("collapsed") ? "▸" : "▾";
  });

  checkbox.addEventListener("change", (event) => {
    toggleChildren(event.target, event.target.checked);
    updateParentCheckboxes(event.target);
  });

  node.append(header, children);
  return node;
};

const createTabNode = (session, tab, windowIndex, tabIndex) => {
  const node = document.createElement("div");
  node.className = "tab-row";
  node.dataset.node = "tab";
  node.dataset.sessionId = session.id;
  node.dataset.windowIndex = windowIndex;
  node.dataset.tabIndex = tabIndex;
  node.draggable = true;

  const checkbox = createCheckbox({
    nodeType: "tab",
    sessionId: session.id,
    windowIndex,
    tabIndex,
  });

  const title = document.createElement("div");
  title.className = "title";
  const favicon = document.createElement("img");
  favicon.src =
    tab.favIconUrl ||
    "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='16' height='16'%3E%3Crect width='16' height='16' rx='4' fill='%23cbd2d9'/%3E%3C/svg%3E";
  favicon.alt = "";
  const titleLink = document.createElement("a");
  titleLink.className = "tab-title";
  titleLink.href = tab.url || "#";
  titleLink.textContent = tab.title || "Untitled Tab";
  titleLink.target = "_blank";
  titleLink.rel = "noopener noreferrer";
  const link = document.createElement("span");
  link.textContent = tab.url || "No URL";
  link.className = "tab-link";
  title.append(favicon, titleLink, link);

  const actions = document.createElement("div");
  actions.className = "tab-actions";
  actions.append(
    createButton("Restore", "restore", { nodeType: "tab" }),
    createButton("Delete", "delete", { nodeType: "tab" }),
    createButton("Close", "close", { nodeType: "tab" })
  );

  node.append(checkbox, title, actions);

  checkbox.addEventListener("change", (event) => {
    updateParentCheckboxes(event.target);
  });

  return node;
};

const renderSessions = () => {
  sessionList.innerHTML = "";

  if (!sessions.length) {
    renderEmptyState();
    return;
  }

  sessions.forEach((session, index) => {
    sessionList.appendChild(createSessionNode(session, index));
  });
};

const handleAction = async (action, target) => {
  const nodeType = target.dataset.nodeType;
  const sessionId = target.closest("[data-node]")?.dataset.sessionId;
  const sessionIndex = sessions.findIndex((s) => s.id === sessionId);
  if (sessionIndex === -1) {
    return;
  }

  if (nodeType === "session") {
    if (action === "restore") {
      await restoreSession(sessions[sessionIndex]);
    } else if (action === "delete") {
      sessions.splice(sessionIndex, 1);
      await saveSessions(sessions);
      renderSessions();
    } else if (action === "close") {
      await closeSessionTabs(sessions[sessionIndex]);
    }
    return;
  }

  const windowIndex = Number(target.closest("[data-node]").dataset.windowIndex);
  const session = sessions[sessionIndex];
  const windowData = session.windows[windowIndex];

  if (nodeType === "window") {
    if (action === "restore") {
      await restoreWindow(windowData);
    } else if (action === "delete") {
      session.windows.splice(windowIndex, 1);
      await saveSessions(sessions);
      renderSessions();
    } else if (action === "close") {
      await closeWindowTabs(windowData);
    }
    return;
  }

  if (nodeType === "tab") {
    const tabIndex = Number(target.closest("[data-node]").dataset.tabIndex);
    const tabData = windowData.tabs[tabIndex];
    if (action === "restore") {
      await restoreTab(tabData);
    } else if (action === "delete") {
      windowData.tabs.splice(tabIndex, 1);
      await saveSessions(sessions);
      renderSessions();
    } else if (action === "close") {
      await closeTab(tabData);
    }
  }
};

const restoreSession = async (session) => {
  if (!isExtensionEnv) {
    return;
  }
  for (const window of session.windows) {
    await restoreWindow(window);
  }
};

const restoreWindow = async (windowData) => {
  if (!isExtensionEnv) {
    return;
  }
  const urls = windowData.tabs.map((tab) => tab.url || "chrome://newtab/");
  await chrome.windows.create({
    url: urls.length ? urls : ["chrome://newtab/"],
  });
};

const restoreTab = async (tabData) => {
  if (!isExtensionEnv) {
    return;
  }
  const window = await chrome.windows.getLastFocused({ populate: false });
  await chrome.tabs.create({
    windowId: window.id,
    url: tabData.url || "chrome://newtab/",
  });
};

const closeSessionTabs = async (session) => {
  if (!isExtensionEnv) {
    return;
  }
  for (const window of session.windows) {
    await closeWindowTabs(window);
  }
};

const closeWindowTabs = async (windowData) => {
  if (!isExtensionEnv) {
    return;
  }
  const liveWindows = await chrome.windows.getAll({ populate: true });
  for (const liveWindow of liveWindows) {
    const liveUrls = (liveWindow.tabs || []).map((tab) => tab.url);
    const targetUrls = windowData.tabs.map((tab) => tab.url);
    const isMatch =
      liveUrls.length === targetUrls.length &&
      liveUrls.every((url, index) => url === targetUrls[index]);
    if (isMatch) {
      await chrome.windows.remove(liveWindow.id);
      return;
    }
  }

  for (const tab of windowData.tabs) {
    await closeTab(tab);
  }
};

const closeTab = async (tabData) => {
  if (!isExtensionEnv) {
    return;
  }
  if (!tabData.url) {
    return;
  }
  const tabs = await chrome.tabs.query({ url: tabData.url });
  if (tabs.length) {
    await chrome.tabs.remove(tabs[0].id);
  }
};

const getCheckedNodes = () =>
  Array.from(
    document.querySelectorAll("input[type='checkbox']:checked")
  ).filter((input) => input.dataset.nodeType);

const restoreSelected = async () => {
  const checked = getCheckedNodes();
  for (const input of checked) {
    const nodeType = input.dataset.nodeType;
    const session = sessions.find((item) => item.id === input.dataset.sessionId);
    if (!session) {
      continue;
    }
    if (nodeType === "session") {
      await restoreSession(session);
    } else if (nodeType === "window") {
      const windowData = session.windows[Number(input.dataset.windowIndex)];
      if (windowData) {
        await restoreWindow(windowData);
      }
    } else if (nodeType === "tab") {
      const windowData = session.windows[Number(input.dataset.windowIndex)];
      const tab = windowData?.tabs[Number(input.dataset.tabIndex)];
      if (tab) {
        await restoreTab(tab);
      }
    }
  }
};

const deleteSelected = async () => {
  const checked = getCheckedNodes();
  checked
    .sort((a, b) => (b.dataset.nodeType || "").localeCompare(a.dataset.nodeType))
    .forEach((input) => {
      const sessionIndex = sessions.findIndex(
        (item) => item.id === input.dataset.sessionId
      );
      if (sessionIndex === -1) {
        return;
      }
      const session = sessions[sessionIndex];
      if (input.dataset.nodeType === "session") {
        sessions.splice(sessionIndex, 1);
        return;
      }
      const windowIndex = Number(input.dataset.windowIndex);
      if (input.dataset.nodeType === "window") {
        session.windows.splice(windowIndex, 1);
        return;
      }
      const tabIndex = Number(input.dataset.tabIndex);
      session.windows[windowIndex]?.tabs.splice(tabIndex, 1);
    });
  await saveSessions(sessions);
  renderSessions();
};

const closeSelected = async () => {
  const checked = getCheckedNodes();
  for (const input of checked) {
    const session = sessions.find((item) => item.id === input.dataset.sessionId);
    if (!session) {
      continue;
    }
    if (input.dataset.nodeType === "session") {
      await closeSessionTabs(session);
    } else if (input.dataset.nodeType === "window") {
      const windowData = session.windows[Number(input.dataset.windowIndex)];
      if (windowData) {
        await closeWindowTabs(windowData);
      }
    } else if (input.dataset.nodeType === "tab") {
      const windowData = session.windows[Number(input.dataset.windowIndex)];
      const tab = windowData?.tabs[Number(input.dataset.tabIndex)];
      if (tab) {
        await closeTab(tab);
      }
    }
  }
};

const getNodeTarget = (event) => event.target.closest("[data-node]");

const handleDragStart = (event) => {
  const node = getNodeTarget(event);
  if (!node) {
    return;
  }
  event.dataTransfer.setData(
    "text/plain",
    JSON.stringify({
      nodeType: node.dataset.node,
      sessionId: node.dataset.sessionId,
      windowIndex: node.dataset.windowIndex,
      tabIndex: node.dataset.tabIndex,
    })
  );
  event.dataTransfer.effectAllowed = "move";
};

const handleDragOver = (event) => {
  const targetNode = getNodeTarget(event);
  if (!targetNode) {
    return;
  }
  event.preventDefault();
  targetNode.classList.add("drop-target");
};

const handleDragLeave = (event) => {
  const targetNode = getNodeTarget(event);
  if (!targetNode) {
    return;
  }
  targetNode.classList.remove("drop-target");
};

const handleDrop = async (event) => {
  const targetNode = getNodeTarget(event);
  if (!targetNode) {
    return;
  }
  event.preventDefault();
  targetNode.classList.remove("drop-target");
  const data = JSON.parse(event.dataTransfer.getData("text/plain"));

  if (!data.sessionId || data.sessionId !== targetNode.dataset.sessionId) {
    return;
  }

  const session = sessions.find((item) => item.id === data.sessionId);
  if (!session) {
    return;
  }

  if (data.nodeType === "window" && targetNode.dataset.node === "window") {
    const fromIndex = Number(data.windowIndex);
    const toIndex = Number(targetNode.dataset.windowIndex);
    const [moved] = session.windows.splice(fromIndex, 1);
    session.windows.splice(toIndex, 0, moved);
  }

  if (data.nodeType === "tab") {
    const fromWindowIndex = Number(data.windowIndex);
    const fromTabIndex = Number(data.tabIndex);
    const [moved] = session.windows[fromWindowIndex].tabs.splice(
      fromTabIndex,
      1
    );
    if (targetNode.dataset.node === "tab") {
      const toWindowIndex = Number(targetNode.dataset.windowIndex);
      const toTabIndex = Number(targetNode.dataset.tabIndex);
      session.windows[toWindowIndex].tabs.splice(toTabIndex, 0, moved);
    } else if (targetNode.dataset.node === "window") {
      const toWindowIndex = Number(targetNode.dataset.windowIndex);
      session.windows[toWindowIndex].tabs.push(moved);
    }
  }

  await saveSessions(sessions);
  renderSessions();
};

const updateToggleState = (node, collapsed) => {
  const children = node.querySelector(":scope > .children");
  if (!children) {
    return;
  }
  const toggle = node.querySelector(":scope .toggle");
  if (collapsed) {
    children.classList.add("collapsed");
  } else {
    children.classList.remove("collapsed");
  }
  if (toggle) {
    toggle.textContent = children.classList.contains("collapsed") ? "▸" : "▾";
  }
};

const toggleLevel = (mode, level) => {
  const collapsed = mode === "collapse";
  if (level === "all") {
    sessionList
      .querySelectorAll("[data-node='session'], [data-node='window']")
      .forEach((node) => updateToggleState(node, collapsed));
    return;
  }
  if (level === "windows") {
    sessionList
      .querySelectorAll("[data-node='session']")
      .forEach((node) => updateToggleState(node, collapsed));
    return;
  }
  if (level === "tabs") {
    sessionList
      .querySelectorAll("[data-node='window']")
      .forEach((node) => updateToggleState(node, collapsed));
  }
};

const attachEventHandlers = () => {
  sessionList.addEventListener("click", (event) => {
    const target = event.target.closest("button[data-action]");
    if (target) {
      handleAction(target.dataset.action, target);
    }
  });

  toolbar?.addEventListener("click", (event) => {
    const expandButton = event.target.closest("button[data-expand]");
    if (expandButton) {
      toggleLevel("expand", expandButton.dataset.expand);
      return;
    }
    const collapseButton = event.target.closest("button[data-collapse]");
    if (collapseButton) {
      toggleLevel("collapse", collapseButton.dataset.collapse);
    }
  });

  sessionList.addEventListener("dragstart", handleDragStart);
  sessionList.addEventListener("dragover", handleDragOver);
  sessionList.addEventListener("drop", handleDrop);
  sessionList.addEventListener("dragleave", handleDragLeave);

  selectAll.addEventListener("change", (event) => {
    const checked = event.target.checked;
    document
      .querySelectorAll("input[type='checkbox']")
      .forEach((input) => {
        input.checked = checked;
        input.indeterminate = false;
      });
  });

  saveButton.addEventListener("click", async () => {
    const session = await buildSession("Manual Save");
    sessions.unshift(session);
    await saveSessions(sessions);
    renderSessions();
  });

  restoreSelectedButton.addEventListener("click", restoreSelected);
  deleteSelectedButton.addEventListener("click", deleteSelected);
  closeSelectedButton.addEventListener("click", closeSelected);

  exportJsonButton.addEventListener("click", () => {
    const blob = new Blob([JSON.stringify(sessions, null, 2)], {
      type: "application/json",
    });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = "sessions.json";
    link.click();
    URL.revokeObjectURL(url);
  });

  importJsonInput.addEventListener("change", async (event) => {
    const file = event.target.files[0];
    if (!file) {
      return;
    }
    const text = await file.text();
    const parsed = JSON.parse(text);
    if (Array.isArray(parsed)) {
      sessions = parsed;
      await saveSessions(sessions);
      renderSessions();
    }
    importJsonInput.value = "";
  });
};

const init = async () => {
  sessions = await getSessions();
  renderSessions();
  attachEventHandlers();
  if (!isExtensionEnv) {
    saveButton.disabled = true;
    restoreSelectedButton.disabled = true;
    closeSelectedButton.disabled = true;
  }
};

init();
