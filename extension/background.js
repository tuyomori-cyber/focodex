const page = browser.runtime.getURL("probe.html");
browser.action.onClicked.addListener(async () => {
  const existing = (await browser.tabs.query({})).find(tab => tab.url === page);
  if (existing) {
    await browser.tabs.update(existing.id, { active: true });
    await browser.windows.update(existing.windowId, { focused: true });
  } else {
    await browser.tabs.create({ url: page });
  }
});
