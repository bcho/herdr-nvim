"""Runs on the space's machine. Neovim and its Unix socket stay there."""

import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time

LABEL = "remote-nvim-poc"


def command(argv, *, timeout=10, stdin=None, cwd=None):
    result = subprocess.run(argv, input=stdin, text=True, capture_output=True,
                            timeout=timeout, cwd=cwd)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or str(argv))
    return result.stdout


def core(method, params):
    requests = [
        {"jsonrpc": "2.0", "id": 1, "method": "hello", "params": {}},
        {"jsonrpc": "2.0", "id": 2, "method": method, "params": params},
    ]
    text = command(["hc", "connect"], stdin="".join(json.dumps(r) + "\n" for r in requests))
    for line in text.splitlines():
        reply = json.loads(line)
        if reply.get("id") == 2:
            if "error" in reply:
                raise RuntimeError(str(reply["error"]))
            return reply["result"]
    raise RuntimeError(f"No reply from herdr-core for {method}")


def tab_exists(ctx):
    doc = core("doc.get", {"name": "herdr-app"})
    # An unavailable document is not evidence that its tabs were closed.
    value = doc.get("value")
    if not doc.get("version") or not isinstance(value, dict) or not isinstance(value.get("spaces"), list):
        return None
    for space in value["spaces"]:
        if not isinstance(space, dict) or not isinstance(space.get("id"), str) or not isinstance(space.get("tabs"), list):
            return None
        if any(not isinstance(tab, dict) or not isinstance(tab.get("id"), str) for tab in space["tabs"]):
            return None
    return any(s["id"] == ctx["space"] and any(t["id"] == ctx["tab"] for t in s["tabs"])
               for s in value["spaces"])


def key_for(ctx):
    return hashlib.sha256(f'{ctx["space"]}:{ctx["tab"]}'.encode()).hexdigest()[:20]


def runtime(ctx):
    root = Path("/tmp") / f"remote-nvim-poc-{os.getuid()}"
    root.mkdir(mode=0o700, exist_ok=True)
    return root / key_for(ctx)


def nvim_bin():
    binary = shutil.which("nvim")
    if not binary:
        raise RuntimeError("Neovim is not installed on this machine")
    version = command([binary, "--version"]).splitlines()[0].split("v", 1)[1]
    if tuple(map(int, version.split(".")[:2])) < (0, 10):
        raise RuntimeError("Neovim 0.10+ is required")
    return binary


def evaluate(ctx, lua):
    # JSON quoting is compatible with Vim's double-quoted string syntax.
    expr = "luaeval(" + json.dumps(lua) + ")"
    result = command([nvim_bin(), "--clean", "--headless", "--server",
                      str(runtime(ctx)) + ".sock", "--remote-expr", expr])
    return json.loads(result.strip())


def status(ctx):
    base = runtime(ctx)
    if not Path(str(base) + ".sock").exists():
        return {"running": False}
    data = evaluate(ctx, "vim.json.encode({file=vim.api.nvim_buf_get_name(0), "
                    "cursor=vim.api.nvim_win_get_cursor(0), cwd=vim.fn.getcwd(), "
                    "modified=vim.tbl_filter(function(b) return b.changed == 1 end, "
                    "vim.fn.getbufinfo())})")
    return {"running": True, "socket": str(base) + ".sock", "nvim": nvim_bin(), **data}


def terminals(ctx):
    return core("terminal.list", {"labels": {LABEL: key_for(ctx)}})["terminals"]


def ensure(ctx):
    base = runtime(ctx)
    with open(str(base) + ".lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        current = status(ctx)
        if current["running"]:
            return current
        if tab_exists(ctx) is not True:
            raise RuntimeError("The remote tab is absent or its layout is unavailable")
        if any(t["state"] == "live" and t.get("labels", {}).get(LABEL + ".role") == "daemon"
               for t in terminals(ctx)):
            raise RuntimeError("The tab's Neovim daemon exists but its socket is not ready")
        cwd = str(Path(ctx["cwd"]).resolve(strict=True))
        if not Path(cwd).is_dir():
            raise RuntimeError(f"Not a remote folder: {cwd}")
        binary = nvim_bin()
        # The core owns the supervisor, not the short-lived machine.exec call.
        core("terminal.run", {
            "argv": [sys.executable, str(Path(__file__).resolve()), "serve", json.dumps(ctx)],
            "cwd": cwd, "name": "rnvim-" + key_for(ctx), "no_parent": True, "rm": True,
            "labels": {LABEL: key_for(ctx), LABEL + ".role": "daemon"},
        })
        until = time.monotonic() + 8
        while time.monotonic() < until:
            if Path(str(base) + ".sock").exists():
                return status(ctx)
            time.sleep(0.1)
        raise RuntimeError(f"Neovim did not create its remote socket ({binary})")


def daemon_command(ctx):
    # Only the persistent editor loads the user's config. RPC/UI clients
    # remain clean and attach to this editor instead of initializing another.
    return [nvim_bin(), "--headless", "--listen", str(runtime(ctx)) + ".sock"]


def serve(ctx):
    base = runtime(ctx)
    env = dict(os.environ)
    for key in ("HC_SCOPE", "HC_TERMINAL_ID"):
        env.pop(key, None)
    log = open(str(base) + ".log", "w")
    process = subprocess.Popen(daemon_command(ctx), cwd=ctx["cwd"], env=env,
                               stdout=log, stderr=log)
    cause = "Neovim exited"

    def stop(signum, _frame):
        nonlocal cause
        cause = f"Supervisor received signal {signum}"
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGHUP, stop)
    next_check = 0
    try:
        while process.poll() is None:
            if time.monotonic() >= next_check:
                try:
                    if tab_exists(ctx) is False:
                        cause = "Tab closed"
                        break
                except (RuntimeError, subprocess.TimeoutExpired, OSError):
                    pass
                next_check = time.monotonic() + 5
            time.sleep(0.2)
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
        Path(str(base) + ".sock").unlink(missing_ok=True)
        log.write(f"\n{cause}; Neovim exit code {process.returncode}\n")
        log.close()


def open_file(ctx):
    ensure(ctx)
    if ctx.get("file"):
        path = Path(ctx["file"]).expanduser()
        if not path.is_absolute():
            path = Path(ctx["cwd"]) / path
        # Lua long strings avoid shell/Vim quoting of spaces and apostrophes.
        text = str(path)
        equals = "="
        while "]" + equals + "]" in text:
            equals += "="
        literal = "[" + equals + "[" + text + "]" + equals + "]"
        evaluate(ctx, f"(function() vim.cmd.edit(vim.fn.fnameescape({literal})); "
                 "return vim.json.encode(true) end)()")
    return status(ctx)


def stop_daemon(ctx):
    current = status(ctx)
    if current.get("modified"):
        raise RuntimeError("Neovim has unsaved buffers; save or quit them before stopping")
    if current["running"]:
        # :qa also checks for edits made after the status snapshot. Let the
        # supervisor exit naturally instead of racing terminal.kill with rm.
        command([nvim_bin(), "--clean", "--headless", "--server", current["socket"],
                 "--remote-send", "<C-\\><C-n><Cmd>qa<CR>"])
        until = time.monotonic() + 4
        while Path(current["socket"]).exists():
            if time.monotonic() >= until:
                raise RuntimeError("Neovim did not quit; check for unsaved buffers or a prompt")
            time.sleep(0.1)
    return {"running": False}


def main():
    operation, raw = sys.argv[1:]
    ctx = json.loads(raw)
    if operation == "serve":
        serve(ctx)
        return
    actions = {"ensure": ensure, "status": status, "open": open_file,
               "stop": stop_daemon, "terminals": terminals}
    print(json.dumps(actions[operation](ctx)))


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, ValueError, OSError, subprocess.TimeoutExpired) as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
