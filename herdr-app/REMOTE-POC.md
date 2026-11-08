# Remote Neovim POC

A standalone Herdr app mod for editing files on a connected remote machine.
The remote host runs both a persistent headless Neovim and its terminal UI
client. The editor loads that host's normal Neovim configuration, plugins,
options and keymaps. Herdr carries the terminal display over its existing
machine connection; files are not synchronized to the desktop.

This POC does not replace the stock Files panel, change layouts or bind keys.
It is separate from the upstream `herdr-nvim` mod and Rust binary.

## Install and preview

Requirements:

- A running Herdr app and `hc` on the desktop, with a connected remote machine
  listed by `hc machine ls`. Tested with Herdr app 0.1.14 and herdr-core 0.1.19.
- Python 3 on both machines, and Neovim 0.10+ on the remote host's core PATH.
- POSIX hosts: the helper uses Unix sockets, `fcntl` and `/tmp`. The tested
  desktop/remote combination is macOS/Linux; Windows is not supported.

```sh
git clone --branch remote-nvim-poc https://github.com/bcho/herdr-nvim.git
cd herdr-nvim
herdr-app check "$PWD/herdr-app/mods/remote-nvim-poc"

home=${HERDR_APP_HOME:-$HOME/.config/herdr-app}
test ! -e "$home/mods/remote-nvim-poc" || { echo "POC folder already exists; stop here to avoid overwriting it."; exit 1; }
cp -R herdr-app/mods/remote-nvim-poc "$home/mods/remote-nvim-poc"
herdr-app check "$home/mods/remote-nvim-poc"
```

Select a remote tab that has a terminal, then open the controls:

```sh
herdr-app do mod.open '{"name":"remote-nvim-poc"}'
```

Enter an absolute remote file path, or a path relative to the selected
terminal's folder, and press **Open**. An empty path resumes the tab's editor;
a new editor starts with the remote config's normal startup behavior.
Use your configured Neovim explorer and keymaps inside the pane.

| Control | Behavior |
|---|---|
| Open | Open the path and show/focus the tab's editor |
| Toggle | Hide a shown editor or show a hidden one |
| Hide | Park the terminal UI; preserve the daemon, buffers and cursor |
| Status | Show the current file, unsaved-buffer count and UI terminal address |
| Stop | Refuse unsaved buffers; otherwise quit Neovim and end its UI |

The remote config files are not modified. Its plugins may run programs or
access the network as they normally do. The POC uploads its Python helper in
small chunks to `/tmp/remote-nvim-poc-<uid>/worker-<sha256>.py`, verifies the
upload's checksum, and runs it through `hc exec --on <machine>`.

**Save before closing the Herdr tab or space.** The supervisor checks the
remote core's layout document every five seconds and ends Neovim when its tab
is gone, discarding unsaved work. An unavailable document does not count as a
closed tab.

To upgrade, save and Stop each POC editor before replacing the mod folder.
Running daemons retain their existing configuration/runtime until restarted.
To uninstall, save and Stop the editors, then remove only
`$home/mods/remote-nvim-poc`; the stock interface was never changed. Remote
helpers and logs remain in `/tmp` until cleaned up by the host.

## Implementation

- `mods/remote-nvim-poc/main.js` and `model.js`: controls and a captured
  machine/space/tab/pane/folder target.
- `control.py`: desktop-side app commands, per-tab action locks, helper
  deployment, machine-qualified terminal addresses, and pane park/restore.
- `worker.py`: remote Neovim operations over a local Unix socket and a
  core-owned supervisor. The daemon loads the user's config; RPC/UI clients
  use `--clean` so they do not initialize a second configured editor.

Each tab has its own daemon and at most one labelled UI client. The UI runs
`nvim --server <remote-socket> --remote-ui` on that same remote host. No Neovim
socket is forwarded to the desktop.

Daemon startup/shutdown output is recorded in the matching remote
`/tmp/remote-nvim-poc-<uid>/<tab-hash>.log` file. Slow or hung configs fail the
bounded open/status calls rather than intentionally starting another daemon.

## Checks

The dependency-free Python and context-selection checks also run in CI:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 herdr-app/e2e/test_remote_poc.py
node herdr-app/e2e/remote-poc-model.mjs
```

`herdr-app/e2e/remote-poc-live.py` exercises Stop's unsaved-work guard,
park/restore, remote writes, clean shutdown and tab-close cleanup against an
existing disposable demo space. It modifies that demo's file. Its `--demo`
argument is a local JSON file with this shape (IDs come from `herdr-app ls`):

```json
{
  "file": "/tmp/remote-nvim-poc-example/notes.md",
  "ctx": {
    "machine": "your-machine",
    "space": "your-machine/<space-id>",
    "tab": "your-machine/<tab-id>",
    "anchor": "your-machine/<shell-pane-id>",
    "cwd": "/tmp/remote-nvim-poc-example",
    "file": "/tmp/remote-nvim-poc-example/notes.md"
  }
}
```

The file, folder and Herdr space must already exist. Use a fresh demo tab with
no user work; the script leaves its demo editor open afterward.

```sh
PYTHONDONTWRITEBYTECODE=1 python3 herdr-app/e2e/remote-poc-live.py \
  --demo /path/to/demo.json --home "$home"
```

## Remaining scope

- Integrate machine routing into the upstream Rust host and normal mod, rather
  than maintaining a separate Python POC.
- Wire the stock/native tree, agent-file picker, annotations and rendered
  previews to remote editors and remote session logs.
- Add per-machine executable/environment settings, including alternate
  `NVIM_APPNAME` profiles and shell-derived environment variables. The POC
  currently inherits the core's environment and uses `nvim` on its PATH.
- Harden stale-socket recovery, slow/hung config diagnostics, reconnects,
  upgrades and multiple-window ownership.
- Verify actual LSP/formatter execution, clipboard/OSC 52, browser-opening
  plugins and config-loading behavior across more remote setups.
- Extend live tests to disconnect/reconnect, simultaneous actions and remote
  configuration behavior. CI currently exercises only the unit/model checks.
