const STORAGE_KEY = "sessions";
const MAX_SESSIONS = 50;

const getSessions = async () => {
  const result = await chrome.storage.local.get(STORAGE_KEY);
  return result[STORAGE_KEY] || [];
};

const saveSessions = async (sessions) => {
  await chrome.storage.local.set({ [STORAGE_KEY]: sessions });
};

const buildSession = async (label = "Auto Saved Session") => {
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

const saveCurrentSession = async (label) => {
  const sessions = await getSessions();
  const session = await buildSession(label);
  const updated = [session, ...sessions].slice(0, MAX_SESSIONS);
  await saveSessions(updated);
};

chrome.runtime.onStartup.addListener(() => {
  saveCurrentSession("Startup Session").catch(() => {});
});

chrome.runtime.onSuspend.addListener(() => {
  saveCurrentSession("Auto Saved Session").catch(() => {});
});

chrome.action.onClicked.addListener(async () => {
  const targetUrl = chrome.runtime.getURL("session.html");
  const windows = await chrome.windows.getAll({ populate: true });
  const existing = windows.find((window) =>
    (window.tabs || []).some((tab) => tab.url === targetUrl)
  );

  if (existing) {
    await chrome.windows.update(existing.id, { focused: true });
    const tab = (existing.tabs || []).find((t) => t.url === targetUrl);
    if (tab?.id) {
      await chrome.tabs.update(tab.id, { active: true });
    }
    return;
  }

  await chrome.windows.create({
    url: targetUrl,
    type: "popup",
    width: 1200,
    height: 800,
  });
});
