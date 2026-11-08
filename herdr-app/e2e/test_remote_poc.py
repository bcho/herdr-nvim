import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1] / "mods" / "remote-nvim-poc"


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


control = load("control")
worker = load("worker")


class RemotePocTests(unittest.TestCase):
    def test_machine_ids_are_stripped_only_at_the_core_boundary(self):
        self.assertEqual(control.raw_id("box", "box/t7"), "t7")
        for bad in ("t7", "other/t7", "box/", "box/other/t7"):
            with self.assertRaises(ValueError):
                control.raw_id("box", bad)

    def test_remote_execution_uses_the_selected_machine(self):
        ctx = {"machine": "box", "space": "box/s1", "tab": "box/t1", "cwd": "/remote"}
        with patch.object(control, "deploy_worker", return_value="/tmp/worker.py"), \
             patch.object(control, "command", return_value='{"running":false}') as run:
            self.assertEqual(control.remote(ctx, "status"), {"running": False})
            self.assertEqual(run.call_args.args[0][:6], ["hc", "exec", "--on", "box", "--timeout", "25"])

    def test_daemon_loads_remote_config_without_forced_editor_options(self):
        ctx = {"space": "s1", "tab": "t1"}
        with patch.object(worker, "nvim_bin", return_value="/remote/bin/nvim"), \
             patch.object(worker, "runtime", return_value=Path("/tmp/editor")):
            self.assertEqual(worker.daemon_command(ctx),
                             ["/remote/bin/nvim", "--headless", "--listen", "/tmp/editor.sock"])

    def test_daemon_keys_include_space_and_tab(self):
        self.assertNotEqual(worker.key_for({"space": "s1", "tab": "t1"}),
                            worker.key_for({"space": "s2", "tab": "t1"}))

    def test_missing_document_does_not_reap_daemons(self):
        ctx = {"space": "s1", "tab": "t1"}
        for doc in ({"version": 0}, {"version": 1, "value": {}},
                    {"version": 1, "value": None},
                    {"version": 1, "value": {"spaces": [{"id": "s1"}]}},
                    {"version": 1, "value": {"spaces": [{"id": "s1", "tabs": [None]}]}}):
            with patch.object(worker, "core", return_value=doc):
                self.assertIsNone(worker.tab_exists(ctx))
        with patch.object(worker, "core", return_value={"version": 1, "value": {"spaces": []}}):
            self.assertFalse(worker.tab_exists(ctx))

    def test_stop_refuses_unsaved_work_before_killing_anything(self):
        with patch.object(worker, "status", return_value={"running": True, "modified": [{"name": "/remote/a"}]}), \
             patch.object(worker, "core") as core:
            with self.assertRaisesRegex(RuntimeError, "unsaved"):
                worker.stop_daemon({"space": "s1", "tab": "t1"})
            core.assert_not_called()

    def test_remote_file_path_is_not_interpreted_by_shell_or_lua(self):
        ctx = {"space": "s1", "tab": "t1", "cwd": "/remote", "file": "notes '雪' ]=].md"}
        with patch.object(worker, "ensure"), patch.object(worker, "evaluate") as evaluate, \
             patch.object(worker, "status", return_value={"running": True}):
            worker.open_file(ctx)
            lua = evaluate.call_args.args[1]
            self.assertIn("[==[/remote/notes '雪' ]=].md]==]", lua)


if __name__ == "__main__":
    unittest.main()
