const page = browser.runtime.getURL("probe.html");
browser.action.onClicked.addListener(async () => {
  const existing = (await browser.tabs.query({})).find(tab => tab.url === page);
  if (existing) {
    // An extension reload does not reliably replace an already-open extension
    // page.  Navigate it again so the button never revives a stale probe UI.
    await browser.tabs.update(existing.id, { url: page, active: true });
    await browser.windows.update(existing.windowId, { focused: true });
  } else {
    await browser.tabs.create({ url: page });
  }
});
