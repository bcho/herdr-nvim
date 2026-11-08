"""Mac-side controller: app commands stay local; editor operations use hc --on."""

import argparse
import base64
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

LABEL = "remote-nvim-poc"
WORKER = Path(__file__).with_name("worker.py").read_text()


def command(argv, *, timeout=30, env=None):
    result = subprocess.run(argv, text=True, capture_output=True, timeout=timeout, env=env)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or str(argv))
    return result.stdout


def raw_id(machine, value):
    prefix = machine + "/"
    if not value.startswith(prefix) or not value[len(prefix):] or "/" in value[len(prefix):]:
        raise ValueError(f"Expected an ID on {machine}, got {value!r}")
    return value[len(prefix):]


def remote_context(ctx):
    return {"space": raw_id(ctx["machine"], ctx["space"]),
            "tab": raw_id(ctx["machine"], ctx["tab"]),
            "cwd": ctx["cwd"], "file": ctx.get("file", "")}


def remote_command(ctx, argv):
    return command(["hc", "exec", "--on", ctx["machine"], "--timeout", "25", "--", *argv])


def deploy_worker(ctx):
    digest = hashlib.sha256(WORKER.encode()).hexdigest()
    bootstrap = (
        "import json,os,pathlib,sys,tempfile; "
        "root=pathlib.Path('/tmp')/f'remote-nvim-poc-{os.getuid()}'; "
        "root.mkdir(mode=0o700,exist_ok=True); "
        "p=root/('worker-'+sys.argv[1]+'.py'); "
        "stage=None if p.exists() else tempfile.mkstemp(prefix='.upload-',dir=root)[1]; "
        "print(json.dumps({'worker':str(p),'stage':stage}))"
    )
    deployed = json.loads(remote_command(ctx, ["python3", "-c", bootstrap, digest]))
    if deployed["stage"]:
        # This machine's connection drops large JSON requests. Small uploads
        # also avoid embedding the entire helper in each terminal's argv.
        content = WORKER.encode()
        append = "import base64,sys; open(sys.argv[1],'ab').write(base64.b64decode(sys.argv[2]))"
        for start in range(0, len(content), 768):
            chunk = base64.b64encode(content[start:start + 768]).decode()
            remote_command(ctx, ["python3", "-c", append, deployed["stage"], chunk])
        publish = (
            "import hashlib,pathlib,sys; p=pathlib.Path(sys.argv[1]); "
            "assert hashlib.sha256(p.read_bytes()).hexdigest()==sys.argv[3], 'Incomplete helper upload'; "
            "p.replace(sys.argv[2])"
        )
        remote_command(ctx, ["python3", "-c", publish, deployed["stage"], deployed["worker"], digest])
    return deployed["worker"]


def remote(ctx, operation):
    helper = deploy_worker(ctx)
    text = remote_command(ctx, ["python3", helper, operation, json.dumps(remote_context(ctx))])
    return json.loads(text)


def app(home, operation, args=None):
    env = {**os.environ, "HERDR_APP_HOME": home}
    argv = [os.environ.get("HERDR_APP_BIN", "herdr-app"), operation]
    if args is not None:
        argv.extend(args)
    return json.loads(command(argv, timeout=10, env=env))


def app_do(home, name, args):
    result = app(home, "do", [name, json.dumps(args)])
    if "error" in result:
        raise RuntimeError(result["error"])
    return result


def tab_terminals(home, ctx):
    listing = app(home, "ls")
    for space in listing["spaces"]:
        if space["id"] == ctx["space"]:
            for tab in space["tabs"]:
                if tab["id"] == ctx["tab"]:
                    return tab["terminals"]
    raise RuntimeError("The target tab is no longer in the app")


def ui_terminals(ctx):
    return [ctx["machine"] + "/" + t["address"] for t in remote(ctx, "terminals")
            if t["state"] == "live" and t.get("labels", {}).get(LABEL + ".role") == "ui"]


def wait_for(check):
    until = time.monotonic() + 4
    while time.monotonic() < until:
        if check():
            return
        time.sleep(0.1)
    raise RuntimeError("The app did not finish changing the Neovim pane within four seconds")


def show(home, ctx, editor):
    # Do not redirect keyboard focus if the person switched tabs during remote work.
    if app(home, "status")["tab"] != ctx["tab"]:
        raise RuntimeError("The selected tab changed; return to the remote tab and open Neovim again")
    own = tab_terminals(home, ctx)
    uis = ui_terminals(ctx)
    if len(uis) > 1:
        raise RuntimeError("This tab has multiple POC screens; stop them before opening another")
    if uis:
        terminal = uis[0]
        if terminal not in [p["terminal"] for p in own]:
            if ctx["anchor"] not in [p["pane"] for p in own]:
                raise RuntimeError("The target pane closed; select another pane and retry")
            app_do(home, "pane.focus", {"pane": ctx["anchor"]})
            wait_for(lambda: app(home, "status")["focused_pane"] == ctx["anchor"])
            app_do(home, "terminal.keep", {"terminal": terminal, "place": "right"})
            wait_for(lambda: terminal in [p["terminal"] for p in tab_terminals(home, ctx)])
    else:
        if ctx["anchor"] not in [p["pane"] for p in own]:
            raise RuntimeError("The target pane closed; select another pane and retry")
        made = app_do(home, "pane.split", {
            "pane": ctx["anchor"], "direction": "right",
            "argv": [editor["nvim"], "--clean", "--server", editor["socket"], "--remote-ui"],
        })
        terminal = made["terminal"]
        raw_id(ctx["machine"], terminal)
        key = hashlib.sha256(f'{remote_context(ctx)["space"]}:{remote_context(ctx)["tab"]}'.encode()).hexdigest()[:20]
        command(["hc", "label", terminal, LABEL + "=" + key, LABEL + ".role=ui"])
        wait_for(lambda: terminal in [p["terminal"] for p in tab_terminals(home, ctx)])
    app_do(home, "pane.focus", {"terminal": terminal})
    return {**editor, "terminal": terminal, "machine": ctx["machine"]}


def hide(home, ctx):
    shown = {p["terminal"] for p in tab_terminals(home, ctx)}
    for terminal in ui_terminals(ctx):
        if terminal in shown:
            app_do(home, "terminal.park", {"terminal": terminal})
            wait_for(lambda: terminal not in [p["terminal"] for p in tab_terminals(home, ctx)])
    return {"hidden": True, "machine": ctx["machine"]}


@contextmanager
def tab_lock(home, ctx):
    key = hashlib.sha256(f'{ctx["machine"]}:{ctx["space"]}:{ctx["tab"]}'.encode()).hexdigest()
    directory = Path(home) / "data" / LABEL
    directory.mkdir(parents=True, exist_ok=True)
    with open(directory / (key + ".lock"), "w") as lock:
        until = time.monotonic() + 3
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= until:
                    raise RuntimeError("Another Neovim action is running for this tab")
                time.sleep(0.1)
        yield


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["open", "hide", "toggle", "status", "stop"])
    parser.add_argument("--home", required=True)
    parser.add_argument("--ctx", required=True)
    args = parser.parse_args()
    ctx = json.loads(args.ctx)
    remote_context(ctx)  # Validate IDs before any command changes a pane.
    with tab_lock(args.home, ctx):
        action = args.action
        if action == "toggle":
            shown = {p["terminal"] for p in tab_terminals(args.home, ctx)}
            action = "hide" if any(t in shown for t in ui_terminals(ctx)) else "open"
        if action == "open":
            result = show(args.home, ctx, remote(ctx, "open"))
        elif action == "hide":
            result = hide(args.home, ctx)
        elif action == "status":
            result = {**remote(ctx, "status"), "terminals": ui_terminals(ctx)}
        else:
            result = remote(ctx, "stop")  # Refuses unsaved work before ending anything.
            for terminal in ui_terminals(ctx):
                command(["hc", "kill", terminal])
        print(json.dumps(result))


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, ValueError, KeyError, OSError, subprocess.TimeoutExpired) as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
