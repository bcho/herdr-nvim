export function captureTarget(focus, spaces, panes, terminals) {
  const space = spaces.find((s) => s.id === focus?.space);
  if (!space?.machine || !focus?.tab) return null;
  const screens = new Set(terminals.filter((t) => t.labels?.["remote-nvim-poc.role"] === "ui").map((t) => t.address));
  const own = panes.filter((p) => p.tab === focus.tab && p.terminal && !screens.has(p.terminal));
  const anchor = own.find((p) => p.pane === focus.pane) ?? own[0];
  if (!anchor) return null;
  return {
    machine: space.machine, space: space.id, tab: focus.tab,
    anchor: anchor.pane, cwd: anchor.cwd || space.folder || ".",
  };
}
