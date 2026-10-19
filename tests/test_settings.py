import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("service_settings", Path(__file__).resolve().parents[1] / "fnos/app/service-settings.py")
settings = importlib.util.module_from_spec(spec)
spec.loader.exec_module(settings)


class WizardSettings(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="fnsvn-settings-")
        self.var = Path(self.tmp.name)
        self.environment = patch.dict(os.environ, {"TRIM_PKGVAR": str(self.var), "TRIM_APPDEST": str(self.var / "app"),
                                                   "wizard_svn_port": "", "wizard_repos_path": ""})
        self.environment.start()

    def tearDown(self):
        self.environment.stop()
        self.tmp.cleanup()

    def apply(self):
        with patch.object(sys, "argv", ["service-settings.py", "apply"]):
            settings.main()
        return json.loads((self.var / "service-settings.json").read_text("utf-8"))

    def test_upgrade_without_new_input_preserves_old_directory(self):
        old = self.var / "data/repos/game"
        (old / "db").mkdir(parents=True)
        (old / "conf").mkdir()
        state = self.var / "data/config/state.json"
        state.parent.mkdir()
        state.write_text(json.dumps({"repositories": {"game": {}}}), encoding="utf-8")
        value = self.apply()
        self.assertEqual(3690, value["svn_port"])
        self.assertEqual(str((self.var / "data/repos").resolve()), value["repos_path"])
        self.assertTrue(old.is_dir())

    def test_custom_settings_persist_and_start_with_saved_values(self):
        custom = self.var / "shared repos"
        os.environ.update(wizard_svn_port="13690", wizard_repos_path=str(custom))
        first = self.apply()
        os.environ.update(wizard_svn_port="", wizard_repos_path="")
        self.assertEqual(first, self.apply())
        os.environ.update(wizard_svn_port="9999", wizard_repos_path=str(self.var / "stale-input"))
        with patch.object(sys, "argv", ["service-settings.py", "run"]), patch.object(settings.os, "execv") as execute:
            settings.main()
        arguments = execute.call_args.args[1]
        self.assertEqual("13690", arguments[arguments.index("--svn-port") + 1])
        self.assertEqual(str(custom.resolve()), arguments[arguments.index("--repos") + 1])

    def test_invalid_port_does_not_write_configuration(self):
        for value in ("1023", "65536", "abc", "3690;echo"):
            os.environ["wizard_svn_port"] = value
            with self.assertRaises(ValueError):
                self.apply()
        self.assertFalse((self.var / "service-settings.json").exists())

    def test_changing_storage_cannot_hide_existing_repositories(self):
        initial = self.apply()
        state = self.var / "data/config/state.json"
        state.parent.mkdir(parents=True)
        state.write_text(json.dumps({"repositories": {"game": {}}}), encoding="utf-8")
        os.environ["wizard_repos_path"] = str(self.var / "empty new location")
        with self.assertRaisesRegex(ValueError, "已有仓库时"):
            self.apply()
        self.assertEqual(initial, json.loads((self.var / "service-settings.json").read_text("utf-8")))

    def test_config_and_backup_directory_cannot_be_storage(self):
        for folder in (self.var / "data", self.var / "data/config/nested", self.var / "data/backups"):
            os.environ["wizard_repos_path"] = str(folder)
            with self.assertRaisesRegex(ValueError, "仓库目录必须与应用配置和备份目录分开"):
                self.apply()


if __name__ == "__main__":
    unittest.main(verbosity=2)
