import { View, div } from "gpui-kit";
import { h_flex, v_flex, Input, InputState } from "gpui-base";
import { appHome, closeOverlay, exec, focused, notify, panes, props, run, spaces, terminals, theme } from "herdr";
import { captureTarget } from "./model.js";

export default class RemoteNeovim extends View {
  init(_props, cx) {
    this.target = captureTarget(focused(), spaces(), panes(), terminals());
    this.input = InputState.new({ value: "", placeholder: "Remote file path (empty resumes this tab's editor)" });
    this.input.on("submit", (_event, cx) => this.act("open", cx));
    this.input.focus();
    this.message = "";
    this.busy = false;
  }

  act(action, cx) {
    if (this.busy || !this.target) return;
    const context = { ...this.target, file: this.input.value().trim() };
    this.busy = true;
    this.message = `${action} on ${context.machine}…`;
    cx.notify();
    cx.spawn(async (cx) => {
      let close = false, terminal = null;
      try {
        const result = await exec("python3", [
          `${appHome()}/mods/remote-nvim-poc/control.py`, action,
          "--home", appHome(), "--ctx", JSON.stringify(context),
        ], { timeout: 90000 });
        if (result.code !== 0) throw new Error(result.stderr || result.stdout);
        const data = JSON.parse(result.stdout);
        if (action === "status") {
          this.message = data.running
            ? `${data.file || "Explorer"} · ${data.modified.length} unsaved buffer(s) · ${data.terminals.join(", ")}`
            : "No Neovim daemon running for this tab.";
        } else {
          close = true;
          terminal = data.terminal;
        }
      } catch (error) {
        this.message = String(error.message ?? error).trim();
        notify({ title: "Remote Neovim POC", body: this.message, level: "error", timeout: 12000 });
      } finally {
        this.busy = false;
        if (close) {
          closeOverlay(props().overlay);
          if (terminal) run("pane.focus", { terminal });
        } else cx.notify();
      }
    });
  }

  render() {
    const t = theme();
    const button = (action, label, primary = false) => div().id(`remote-nvim-${action}`)
      .px(12).py(7).rounded(6).text_size(12).cursor_pointer()
      .bg(primary ? t.solid : t.raised).text_color(primary ? t.solid_foreground : t.foreground)
      .when(this.busy, (el) => el.opacity(0.5))
      .hover((s) => s.opacity(0.85)).on_click((_event, cx) => this.act(action, cx)).child(label);
    const shell = v_flex().w_full().p(18).gap(12).bg(t.popover).text_color(t.foreground).text_size(13)
      .child(h_flex().justify_between()
        .child(div().font_semibold().text_size(15).child("Remote Neovim POC"))
        .child(div().text_size(11).text_color(t.dim).child("Esc closes")));
    if (!this.target) return shell.child(div().text_color(t.muted).child("Select a remote tab with a terminal, then open this mod again."));
    return shell
      .child(div().text_color(t.accent).child(this.target.machine))
      .child(div().text_color(t.dim).truncate().child(this.target.cwd))
      .child(Input.new(this.input).h(36).px(10).rounded(6).border_1().border_color(t.input).bg(t.background))
      .child(div().text_size(12).text_color(t.muted).child("Uses the remote host’s Neovim configuration and keymaps. Hide preserves buffers; Stop refuses unsaved work."))
      .child(h_flex().gap(8).children([
        button("open", "Open", true), button("toggle", "Toggle"), button("hide", "Hide"),
        button("status", "Status"), button("stop", "Stop"),
      ]))
      .when(Boolean(this.message), (el) => el.child(div().text_size(12).text_color(t.muted).child(this.message)));
  }
}
