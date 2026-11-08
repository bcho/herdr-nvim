"""Exercise only an existing disposable /tmp/remote-nvim-poc-* demo space."""

import argparse
import importlib.util
import json
from pathlib import Path
import subprocess
import time

SOURCE = Path(__file__).resolve().parents[1] / "mods" / "remote-nvim-poc" / "control.py"
spec = importlib.util.spec_from_file_location("control", SOURCE)
control = importlib.util.module_from_spec(spec)
spec.loader.exec_module(control)


def evaluate(ctx, lua):
    helper = control.deploy_worker(ctx)
    script = "import json,runpy,sys; w=runpy.run_path(sys.argv[1]); print(json.dumps(w['evaluate'](json.loads(sys.argv[2]),sys.argv[3])))"
    return json.loads(control.remote_command(ctx, ["python3", "-c", script, helper,
                                                  json.dumps(control.remote_context(ctx)), lua]))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--demo", required=True)
    parser.add_argument("--home", required=True)
    args = parser.parse_args()
    demo = json.loads(Path(args.demo).read_text())
    ctx = demo["ctx"]
    assert ctx["cwd"].startswith("/tmp/remote-nvim-poc-")
    assert str(Path(demo["file"]).parent) == ctx["cwd"]
    initial = control.remote(ctx, "open")
    assert initial["file"] == demo["file"] and not initial["modified"]
    control.app_do(args.home, "space.select", {"id": ctx["space"]})
    control.app_do(args.home, "tab.select", {"id": ctx["tab"]})
    time.sleep(0.5)
    control.show(args.home, ctx, initial)
    screens = control.ui_terminals(ctx)
    assert len(screens) == 1
    original_screen = screens[0]

    evaluate(ctx, '(function() vim.api.nvim_buf_set_lines(0,0,-1,false, '
             '{"# Remote Neovim POC", "", "Unsaved change survives hiding the pane."}); '
             'vim.api.nvim_win_set_cursor(0,{3,0}); return vim.json.encode(true) end)()')
    changed = control.remote(ctx, "status")
    assert changed["modified"] and changed["cursor"] == [3, 0]
    try:
        control.remote(ctx, "stop")
    except RuntimeError as error:
        assert "unsaved" in str(error)
    else:
        raise AssertionError("Stop discarded an unsaved buffer")
    print("PASS: Stop refuses unsaved work")

    control.hide(args.home, ctx)
    parked = control.remote(ctx, "status")
    assert parked["running"] and parked["modified"] and parked["cursor"] == [3, 0]
    assert control.ui_terminals(ctx) == [original_screen]
    control.app_do(args.home, "space.select", {"id": ctx["space"]})
    control.app_do(args.home, "tab.select", {"id": ctx["tab"]})
    time.sleep(0.5)
    reopened = control.show(args.home, ctx, parked)
    assert reopened["terminal"] == original_screen
    assert evaluate(ctx, "vim.json.encode(vim.api.nvim_buf_get_lines(0,0,-1,false))")[2] == "Unsaved change survives hiding the pane."
    print("PASS: Hide/reopen preserves the same remote screen, unsaved buffer and cursor")

    evaluate(ctx, '(function() vim.cmd.write(); return vim.json.encode(true) end)()')
    text = control.command(["hc", "file", "read", "--on", ctx["machine"], demo["file"]])
    assert "Unsaved change survives hiding the pane." in text
    assert not Path(demo["file"]).exists()
    assert not control.remote(ctx, "status")["modified"]
    print("PASS: :write changes the remote file; no local file exists at that path")

    control.remote(ctx, "stop")
    for terminal in control.ui_terminals(ctx):
        control.command(["hc", "kill", terminal])
    assert not control.remote(ctx, "status")["running"]
    print("PASS: Stop ends a clean editor")

    scratch = control.app_do(args.home, "tab.new", {"space": ctx["space"], "folder": ctx["cwd"], "name": "lifecycle-check"})
    time.sleep(0.5)
    tab = next(t for s in control.app(args.home, "ls")["spaces"] if s["id"] == ctx["space"]
               for t in s["tabs"] if t["id"] == scratch["tab"])
    test_ctx = {**ctx, "tab": scratch["tab"], "anchor": tab["terminals"][0]["pane"]}
    control.remote(test_ctx, "open")
    control.app_do(args.home, "tab.close", {"id": test_ctx["tab"], "confirmed": True})
    deadline = time.monotonic() + 12
    while control.remote(test_ctx, "status")["running"]:
        assert time.monotonic() < deadline, "Closing the tab left its Neovim daemon alive"
        time.sleep(1)
    print("PASS: Closing a tab automatically ends its remote Neovim daemon")

    control.app_do(args.home, "tab.select", {"id": ctx["tab"]})
    time.sleep(0.5)
    opened = control.show(args.home, ctx, control.remote(ctx, "open"))
    demo["opened"] = opened
    Path(args.demo).write_text(json.dumps(demo))
    print("Demo editor left open:", opened["terminal"])


if __name__ == "__main__":
    main()
