import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest


APP_PATH = Path(__file__).resolve().parents[1] / "panel" / "app.py"


class ProviderGenerationTests(unittest.TestCase):
    def test_prepare_config_materializes_only_active_rules_without_changing_local_groups(self):
        sys.dont_write_bytecode = True
        spec = importlib.util.spec_from_file_location("rules_test_panel", APP_PATH)
        app = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(app)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("DATA_DIR", "XRAY_BACKUP_DIR", "GROUPS_BACKUP_DIR", "GROUPS_YAML_QUARANTINE"):
                setattr(app, name, str(root / name))
            app.GROUPS_DIR = str(root / "rule-providers" / "groups")
            app.GROUPS_FILE = str(root / "groups.json")
            app.CONFIG_FILE = str(root / "config.yaml")
            app.MIHOMO_BIN = str(root / "not-installed-mihomo")
            groups = [
                {"id": "on", "name": "Active", "enabled": True, "entries": [
                    {"id": "one", "rule": "DOMAIN-SUFFIX,example.org", "enabled": True},
                    {"id": "two", "rule": "DOMAIN,disabled.example.org", "enabled": False},
                ]},
                {"id": "off", "name": "Disabled", "enabled": False, "entries": [
                    {"id": "three", "rule": "DOMAIN,hidden.example.org", "enabled": True},
                ]},
            ]
            Path(app.GROUPS_FILE).write_text(json.dumps(groups), encoding="utf-8")
            original_groups = Path(app.GROUPS_FILE).read_bytes()
            Path(app.CONFIG_FILE).write_text("mode: rule\nproxies: []\nrule-providers: {}\nrules: []\n", encoding="utf-8")
            app.prepare_config()
            generated = Path(app.GROUPS_DIR) / "g_Active.yaml"
            self.assertTrue(generated.is_file(), "fresh install must create the provider it references")
            payload = [line.strip()[2:] for line in generated.read_text(encoding="utf-8").splitlines()
                       if line.strip().startswith("- ")]
            self.assertEqual(payload, ["DOMAIN-SUFFIX,example.org"])
            self.assertFalse((Path(app.GROUPS_DIR) / "g_Disabled.yaml").exists())
            config = Path(app.CONFIG_FILE).read_text(encoding="utf-8")
            self.assertIn("RULE-SET,g_Active,PROXY", config)
            self.assertNotIn("g_Disabled", config)
            self.assertEqual(Path(app.GROUPS_FILE).read_bytes(), original_groups)


if __name__ == "__main__":
    unittest.main()
