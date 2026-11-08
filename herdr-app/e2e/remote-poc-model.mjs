import assert from "node:assert/strict";
import { captureTarget } from "../mods/remote-nvim-poc/model.js";

const spaces = [{ id: "box/s1", machine: "box", folder: "/remote" }, { id: "s1", machine: null }];
const panes = [
  { pane: "box/p1", tab: "box/t1", terminal: "box/t7", cwd: "/remote/project" },
  { pane: "box/p2", tab: "box/t1", terminal: "box/t8", cwd: "/remote/project" },
];
const terminals = [{ address: "box/t8", labels: { "remote-nvim-poc.role": "ui" } }];
const expected = { machine: "box", space: "box/s1", tab: "box/t1", anchor: "box/p1", cwd: "/remote/project" };
assert.deepEqual(captureTarget({ space: "box/s1", tab: "box/t1", pane: "box/p1" }, spaces, panes, terminals), expected);
assert.deepEqual(captureTarget({ space: "box/s1", tab: "box/t1", pane: "box/p2" }, spaces, panes, terminals), expected);
assert.equal(captureTarget({ space: "s1", tab: "t1" }, spaces, panes, terminals), null);
assert.equal(captureTarget(null, [], [], []), null);
assert.equal(captureTarget({ space: "box/s1", tab: "box/t2" }, spaces, panes, terminals), null);
console.log("remote POC context checks: ok");
