import gzip
import importlib.util
import io
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import urllib.error
import zipfile


spec = importlib.util.spec_from_file_location(
    "panel_updates_under_test", Path(__file__).resolve().parents[1] / "panel" / "app.py"
)
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)


def asset(name, content=None):
    return {
        "name": name,
        "browser_download_url": "https://downloads.example/" + name,
        "content_type": "application/octet-stream",
        "size": len(content or b""),
    }


def release(tag, assets):
    return {
        "tag_name": tag,
        "name": tag,
        "body": "Release notes",
        "html_url": "https://github.com/example/core/releases/tag/" + tag,
        "assets": assets,
        "draft": False,
        "prerelease": False,
    }


class UpdateTests(unittest.TestCase):
    def setUp(self):
        app._UPDATE_CHECK_CACHE.clear()
        self.addCleanup(app._UPDATE_CHECK_CACHE.clear)

    def http_responses(self, responses):
        def open_url(request, timeout=None):
            url = request.full_url
            if url not in responses:
                raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
            value = responses[url]
            if not isinstance(value, bytes):
                value = json.dumps(value).encode("utf-8")
            return io.BytesIO(value)

        return patch.object(app.urllib.request, "urlopen", side_effect=open_url)

    def test_mihomo_selects_native_package_for_architecture_aliases(self):
        assets = [
            asset("mihomo-linux-amd64-v1.2.3.gz"),
            asset("mihomo-linux-amd64-compatible-v1.2.3.gz"),
            asset("mihomo-linux-arm64-v1.2.3.gz"),
        ]
        for machine, expected in (
            ("x86_64", "mihomo-linux-amd64-compatible-v1.2.3.gz"),
            ("amd64", "mihomo-linux-amd64-compatible-v1.2.3.gz"),
            ("aarch64", "mihomo-linux-arm64-v1.2.3.gz"),
            ("arm64", "mihomo-linux-arm64-v1.2.3.gz"),
        ):
            with self.subTest(machine=machine), patch("platform.machine", return_value=machine):
                selected = app._pick_mihomo_asset(assets, "1.2.3")
                self.assertEqual(selected["name"], expected)

    def test_mihomo_uses_plain_amd64_if_compatible_package_absent(self):
        assets = [
            asset("mihomo-linux-amd64-v3-v1.2.3.gz"),
            asset("mihomo-linux-amd64-go120-v1.2.3.gz"),
            asset("mihomo-linux-amd64-v1.2.3.gz"),
        ]
        with patch("platform.machine", return_value="x86_64"):
            self.assertEqual(
                app._pick_mihomo_asset(assets, "1.2.3")["name"],
                "mihomo-linux-amd64-v1.2.3.gz",
            )

    def test_mihomo_does_not_fall_back_to_foreign_or_cpu_specific_packages(self):
        for machine, names in (
            ("arm64", ["mihomo-linux-amd64-v1.2.3.gz"]),
            ("amd64", ["mihomo-linux-arm64-v1.2.3.gz"]),
            ("amd64", ["mihomo-linux-amd64-v3-v1.2.3.gz"]),
            ("arm64", ["mihomo-linux-arm64-go120-v1.2.3.gz"]),
        ):
            with self.subTest(machine=machine, names=names), patch("platform.machine", return_value=machine):
                self.assertIsNone(app._pick_mihomo_asset([asset(n) for n in names], "1.2.3"))

    def test_xray_selects_only_exact_native_archive(self):
        assets = [
            asset("Xray-linux-64.zip"),
            asset("Xray-linux-arm64-v8a.zip"),
            asset("Xray-linux-64.zip.dgst"),
            asset("Xray-linux-64-extra.zip"),
        ]
        for machine, expected in (
            ("x86_64", "Xray-linux-64.zip"),
            ("amd64", "Xray-linux-64.zip"),
            ("aarch64", "Xray-linux-arm64-v8a.zip"),
            ("arm64", "Xray-linux-arm64-v8a.zip"),
        ):
            with self.subTest(machine=machine), patch("platform.machine", return_value=machine):
                self.assertEqual(app._pick_xray_asset(assets)["name"], expected)
        for machine, names in (
            ("arm64", ["Xray-linux-64.zip"]),
            ("amd64", ["Xray-linux-arm64-v8a.zip", "Xray-linux-64-extra.zip"]),
        ):
            with self.subTest(machine=machine), patch("platform.machine", return_value=machine):
                self.assertIsNone(app._pick_xray_asset([asset(n) for n in names]))

    def test_unsupported_architecture_rejected_before_services_or_downloads(self):
        for component in ("mihomo", "xray"):
            with self.subTest(component=component), patch("platform.machine", return_value="riscv64"), \
                    self.http_responses({}), \
                    patch.object(app, "_version_mihomo", return_value={"version": "1.0.0"}), \
                    patch.object(app, "_version_xray", return_value={"version": "1.0.0"}), \
                    patch.object(app, "_http_download") as download, \
                    patch.object(app.subprocess, "run") as command, \
                    patch.object(app, "log_activity"):
                result = app.update_component_binary(component)
                self.assertFalse(result["ok"])
                download.assert_not_called()
                command.assert_not_called()

    def test_missing_native_asset_does_not_stop_or_replace_service(self):
        responses = {
            "https://api.github.com/repos/XTLS/Xray-core/releases/latest":
                release("v1.2.3", [asset("Xray-linux-64.zip")]),
        }
        with patch("platform.machine", return_value="aarch64"), self.http_responses(responses), \
                patch.object(app, "_version_xray", return_value={"version": "1.0.0"}), \
                patch.object(app.subprocess, "run") as command, \
                patch.object(app, "_backup_binary") as backup, \
                patch.object(app, "log_activity"):
            result = app.update_component_binary("xray")
            self.assertFalse(result["ok"])
            command.assert_not_called()
            backup.assert_not_called()

    def test_pinned_lookup_uses_tag_and_does_not_share_latest_cache(self):
        base = "https://api.github.com/repos/MetaCubeX/mihomo/releases/"
        responses = {
            base + "latest": release("v9.0.0", [asset("mihomo-linux-arm64-v9.0.0.gz")]),
            base + "tags/v1.2.3": release("v1.2.3", [asset("mihomo-linux-arm64-v1.2.3.gz")]),
            base + "tags/v2.3.4": release("v2.3.4", [asset("mihomo-linux-arm64-v2.3.4.gz")]),
        }
        with patch("platform.machine", return_value="arm64"), self.http_responses(responses), \
                patch.object(app, "_version_mihomo", return_value={"version": "1.0.0"}):
            latest = app.check_component_update("mihomo")
            first = app.check_component_update("mihomo", target_version="v1.2.3")
            second = app.check_component_update("mihomo", target_version="2.3.4")
            latest_again = app.check_component_update("mihomo")
        self.assertEqual(latest["latest"], "9.0.0")
        self.assertTrue(first["ok"])
        self.assertEqual(first["latest"], "1.2.3")
        self.assertEqual(first["asset_name"], "mihomo-linux-arm64-v1.2.3.gz")
        self.assertEqual(second["latest"], "2.3.4")
        self.assertEqual(latest_again["latest"], "9.0.0")

    def test_missing_or_mismatched_pinned_release_never_substitutes_latest(self):
        base = "https://api.github.com/repos/XTLS/Xray-core/releases/"
        for pinned_response in (None, release("v9.0.0", [asset("Xray-linux-64.zip")])):
            responses = {base + "latest": release("v9.0.0", [asset("Xray-linux-64.zip")])}
            if pinned_response is not None:
                responses[base + "tags/v1.2.3"] = pinned_response
            with self.subTest(response=pinned_response), patch("platform.machine", return_value="amd64"), \
                    self.http_responses(responses), \
                    patch.object(app, "_version_xray", return_value={"version": "1.0.0"}):
                result = app.check_component_update("xray", target_version="v1.2.3")
                self.assertFalse(result["ok"])
                self.assertFalse(result["update_available"])

    def test_fleet_installs_pinned_native_archives_and_preserves_backups(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            mihomo = root / "mihomo"
            xray = root / "xray"
            backups = root / "backups"
            backups.mkdir()
            mihomo.write_bytes(b"old mihomo")
            xray.write_bytes(b"old xray")
            xray_zip = io.BytesIO()
            with zipfile.ZipFile(xray_zip, "w") as archive:
                archive.writestr("xray", b"pinned arm64 xray")
            mihomo_name = "mihomo-linux-arm64-v1.2.3.gz"
            xray_name = "Xray-linux-arm64-v8a.zip"
            responses = {
                "https://api.github.com/repos/MetaCubeX/mihomo/releases/tags/v1.2.3":
                    release("v1.2.3", [asset(mihomo_name)]),
                "https://api.github.com/repos/XTLS/Xray-core/releases/tags/v2.3.4":
                    release("v2.3.4", [asset(xray_name)]),
                "https://downloads.example/" + mihomo_name: gzip.compress(b"pinned arm64 mihomo"),
                "https://downloads.example/" + xray_name: xray_zip.getvalue(),
            }
            with patch("platform.machine", return_value="aarch64"), self.http_responses(responses), \
                    patch.object(app, "MIHOMO_BIN", str(mihomo)), patch.object(app, "XRAY_BIN", str(xray)), \
                    patch.object(app, "BIN_BACKUP_DIR", str(backups)), \
                    patch.object(app, "_ensure_bin_backup_dir"), \
                    patch.object(app, "_version_mihomo", return_value={"version": "1.0.0"}), \
                    patch.object(app, "_version_xray", return_value={"version": "1.0.0"}), \
                    patch.object(app, "components_versions", return_value={
                        "mihomo": {"version": "1.0.0"}, "xray": {"version": "1.0.0"}}), \
                    patch.object(app, "fleet_health_ok", return_value=(True, "ok")), \
                    patch.object(app, "_health_mihomo", return_value=True), \
                    patch.object(app, "_health_xray", return_value=True), \
                    patch.object(app, "log_activity"), patch.object(app.time, "sleep"), \
                    patch.object(app.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "0", "")):
                report = app.fleet_apply_from_manifest(
                    {"mihomo": "v1.2.3", "xray": "v2.3.4"}, repo=td
                )
            self.assertTrue(report["ok"], report)
            self.assertEqual(mihomo.read_bytes(), b"pinned arm64 mihomo")
            self.assertEqual(xray.read_bytes(), b"pinned arm64 xray")
            results = {action["component"]: action["result"] for action in report["actions"]}
            self.assertEqual(results["mihomo"]["new"], "1.2.3")
            self.assertEqual(results["xray"]["new"], "2.3.4")
            self.assertEqual(Path(results["mihomo"]["backup"]).read_bytes(), b"old mihomo")
            self.assertEqual(Path(results["xray"]["backup"]).read_bytes(), b"old xray")

    def test_failed_healthcheck_restores_original_binary(self):
        with tempfile.TemporaryDirectory() as td:
            binary = Path(td) / "mihomo"
            binary.write_bytes(b"working original")
            backups = Path(td) / "backups"
            backups.mkdir()
            name = "mihomo-linux-arm64-v1.2.3.gz"
            responses = {
                "https://api.github.com/repos/MetaCubeX/mihomo/releases/tags/v1.2.3":
                    release("v1.2.3", [asset(name)]),
                "https://downloads.example/" + name: gzip.compress(b"unhealthy replacement"),
            }
            with patch("platform.machine", return_value="arm64"), self.http_responses(responses), \
                    patch.object(app, "MIHOMO_BIN", str(binary)), \
                    patch.object(app, "BIN_BACKUP_DIR", str(backups)), \
                    patch.object(app, "_ensure_bin_backup_dir"), \
                    patch.object(app, "_version_mihomo", return_value={"version": "1.0.0"}), \
                    patch.object(app, "_health_mihomo", return_value=False), \
                    patch.object(app, "log_activity"), patch.object(app.time, "sleep"), \
                    patch.object(app.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "", "")):
                result = app.update_component_binary("mihomo", target_version="1.2.3")
            self.assertFalse(result["ok"])
            self.assertTrue(result["rolled_back"])
            self.assertEqual(binary.read_bytes(), b"working original")
            self.assertEqual(Path(result["backup"]).read_bytes(), b"working original")

    @unittest.skipUnless(shutil.which("git"), "git is required")
    def test_panel_divergence_preserves_local_work_and_does_not_install(self):
        real_run = subprocess.run

        def git(repo, *args):
            return real_run(
                ["git", "-C", str(repo), "-c", "user.name=Test", "-c", "user.email=test@example.invalid", *args],
                check=True, capture_output=True, text=True,
            ).stdout.strip()

        with tempfile.TemporaryDirectory() as td:
            upstream = Path(td) / "upstream"
            repo = Path(td) / "node"
            upstream.mkdir()
            git(upstream, "init", "-b", "main")
            (upstream / "scripts").mkdir()
            (upstream / "scripts" / "update.sh").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            (upstream / "code.txt").write_text("base", encoding="utf-8")
            git(upstream, "add", ".")
            git(upstream, "commit", "-m", "base")
            git(upstream, "clone", str(upstream), str(repo))
            (repo / "local-rules.txt").write_text("device rules", encoding="utf-8")
            git(repo, "add", ".")
            git(repo, "commit", "-m", "local rule commit")
            head = git(repo, "rev-parse", "HEAD")
            (repo / "untracked.txt").write_text("user work", encoding="utf-8")
            (upstream / "code.txt").write_text("new upstream", encoding="utf-8")
            git(upstream, "add", ".")
            git(upstream, "commit", "-m", "upstream update")
            installed = []

            def run_command(args, **kwargs):
                if args[0] == "git":
                    if args[3:5] == ["remote", "set-url"]:
                        # Keep the real origin pointed at the local fixture, not GitHub.
                        return subprocess.CompletedProcess(args, 0, "", "")
                    return real_run(args, **kwargs)
                installed.append(args)
                return subprocess.CompletedProcess(args, 1, "", "unexpected installation")

            with patch.dict(app.os.environ, {"XTTP_GIT_REPO": str(repo)}), \
                    patch.object(app, "check_component_update", return_value={
                        "ok": True, "update_available": True, "current": "1.0.0", "latest": "2.0.0"}), \
                    patch.object(app.subprocess, "run", side_effect=run_command), \
                    patch.object(app, "log_activity"):
                result = app.update_component_panel()
            self.assertFalse(result["ok"])
            self.assertEqual(git(repo, "rev-parse", "HEAD"), head)
            self.assertEqual((repo / "local-rules.txt").read_text(encoding="utf-8"), "device rules")
            self.assertEqual((repo / "untracked.txt").read_text(encoding="utf-8"), "user work")
            self.assertEqual((repo / "code.txt").read_text(encoding="utf-8"), "base")
            self.assertEqual(installed, [])


if __name__ == "__main__":
    unittest.main()
