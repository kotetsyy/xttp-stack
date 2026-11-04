import gzip
import http.client
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import urllib.parse
import uuid
from contextlib import contextmanager


class ReadinessClock:
    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


@contextmanager
def readiness_clock(app):
    clock = ReadinessClock()
    with patch.object(app.time, "monotonic", side_effect=clock.monotonic), \
            patch.object(app.time, "sleep", side_effect=clock.sleep):
        yield clock


APP_PATH = Path(__file__).resolve().parents[1] / "panel" / "app.py"


class TransactionTests(unittest.TestCase):
    def setUp(self):
        sys.dont_write_bytecode = True
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        env = patch.dict(os.environ, {"XTTP_UPDATE_LOCK": str(self.root / "update.lock")})
        env.start()
        self.addCleanup(env.stop)
        name = "transaction_panel_" + uuid.uuid4().hex
        spec = importlib.util.spec_from_file_location(name, APP_PATH)
        self.app = importlib.util.module_from_spec(spec)
        sys.modules[name] = self.app
        self.addCleanup(sys.modules.pop, name, None)
        spec.loader.exec_module(self.app)
        for key in ("DATA_DIR", "XRAY_BACKUP_DIR", "GROUPS_BACKUP_DIR", "GROUPS_YAML_QUARANTINE"):
            setattr(self.app, key, str(self.root / key))
        for key in ("GROUPS_FILE", "REMOTES_FILE", "CONFIG_FILE", "USERS_FILE", "SESSIONS_FILE",
                    "BOOTSTRAP_PASSWORD_FILE", "ACTIVITY_FILE", "PREFS_FILE", "TRAFFIC_HISTORY_FILE"):
            setattr(self.app, key, str(self.root / key))
        self.app.GROUPS_DIR = str(self.root / "rule-providers" / "groups")
        self.app.XRAY_CONFIGS = [str(self.root / "xray" / "config.json")]
        Path(self.app.XRAY_CONFIGS[0]).parent.mkdir()
        self.app.MIHOMO_BIN = str(self.root / "mihomo")
        self.app.XRAY_BIN = str(self.root / "xray-bin")
        self.app.ensure_data_dir()

    def test_delayed_rule_readiness_keeps_candidate_and_persists_applied_state(self):
        groups = [{"id": "g", "name": "Candidate", "enabled": True, "entries": [
            {"id": "a", "rule": "DOMAIN,candidate.org", "enabled": True}]}]
        Path(self.app.GROUPS_FILE).write_text(json.dumps(groups), encoding="utf-8")
        config = Path(self.app.CONFIG_FILE)
        old = b"mode: rule\nrule-providers: {}\nrules: []\n"
        config.write_bytes(old)
        with readiness_clock(self.app) as clock, \
                patch.object(self.app.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "", "")), \
                patch.object(self.app, "_health_mihomo", side_effect=lambda **kw: clock.now >= 2.0):
            result = self.app.activate_rules()
        self.assertTrue(result["ok"], result)
        self.assertNotEqual(config.read_bytes(), old)
        self.assertEqual(result["state"]["status"], "applied")
        self.assertEqual(self.app.rules_state()["status"], "applied")
        persisted = json.loads((Path(self.app.DATA_DIR) / "rules-state.json").read_text())
        self.assertEqual(persisted["applied_revision"], result["state"]["saved_revision"])
        self.assertEqual(persisted["config_hash"], self.app._file_hash(str(config)))
        providers = list(Path(self.app.GROUPS_DIR).rglob("*.yaml"))
        self.assertTrue(any(b"DOMAIN,candidate.org" in path.read_bytes() for path in providers))

    def test_delayed_rule_restore_reports_recovery_only_after_old_bytes_are_ready(self):
        Path(self.app.GROUPS_FILE).write_text("[]", encoding="utf-8")
        config = Path(self.app.CONFIG_FILE)
        old = b"mode: rule\nrule-providers: {}\nrules: []\n"
        config.write_bytes(old)
        with readiness_clock(self.app) as clock, \
                patch.object(self.app.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "", "")), \
                patch.object(self.app, "restart_mihomo", side_effect=[RuntimeError("start failed"), None]), \
                patch.object(self.app, "_health_mihomo", side_effect=lambda **kw: clock.now >= 2.0):
            result = self.app.activate_rules()
        self.assertFalse(result["ok"])
        self.assertEqual(result["rollback"]["status"], "succeeded", result)
        self.assertEqual(config.read_bytes(), old)
        self.assertNotEqual(result["state"]["status"], "applied")

    def test_rule_readiness_deadline_rejects_late_candidate_and_verifies_restore(self):
        Path(self.app.GROUPS_FILE).write_text("[]", encoding="utf-8")
        config = Path(self.app.CONFIG_FILE)
        old = b"mode: rule\nrule-providers: {}\nrules: []\n"
        config.write_bytes(old)
        def health(**kwargs):
            if config.read_bytes() != old:
                clock.now = 15.0
                return True  # Readiness at the deadline is too late to commit.
            return clock.now >= 15.5
        with readiness_clock(self.app) as clock, \
                patch.object(self.app.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "", "")), \
                patch.object(self.app, "_health_mihomo", side_effect=health):
            result = self.app.activate_rules()
        self.assertFalse(result["ok"], result)
        self.assertEqual(result["rollback"]["status"], "succeeded", result)
        self.assertEqual(config.read_bytes(), old)
        self.assertNotEqual(result["state"]["status"], "applied")

    def test_delayed_xray_readiness_keeps_candidate_and_verifies_delayed_restore(self):
        config = Path(self.app.XRAY_CONFIGS[0])
        old = b'{"outbounds":[{"protocol":"freedom","tag":"old"}]}\n'
        candidate = {"outbounds": [{"protocol": "freedom", "tag": "candidate"}]}
        for restore in (False, True):
            with self.subTest(restore=restore):
                config.write_bytes(old)
                with readiness_clock(self.app) as clock, \
                        patch.object(self.app.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "", "")), \
                        patch.object(self.app, "restart_xray", side_effect=[RuntimeError("start failed"), None] if restore else None), \
                        patch.object(self.app, "_health_xray", side_effect=lambda **kw: clock.now >= 2.0), \
                        patch.object(self.app, "socks_http_probe", return_value={"ok": True}):
                    result = self.app.activate_xray(candidate)
                self.assertEqual(result["ok"], not restore, result)
                if restore:
                    self.assertEqual(result["rollback"]["status"], "succeeded", result)
                    self.assertEqual(config.read_bytes(), old)
                else:
                    self.assertEqual(json.loads(config.read_bytes()), candidate)
                    self.assertTrue(result["http_ok"])
                    self.assertEqual((Path(self.app.XRAY_BACKUP_DIR) / result["backup"]).read_bytes(), old)

    def test_delayed_native_readiness_keeps_replacement_binary(self):
        import io
        import zipfile
        xray_archive = io.BytesIO()
        with zipfile.ZipFile(xray_archive, "w") as archive:
            archive.writestr("xray", b"NEW")
        for component, archive in (("mihomo", gzip.compress(b"NEW")), ("xray", xray_archive.getvalue())):
            with self.subTest(component=component):
                binary = Path(self.app.MIHOMO_BIN if component == "mihomo" else self.app.XRAY_BIN)
                binary.write_bytes(b"OLD")
                backup = self.root / ("backup-" + component)
                backup.write_bytes(b"OLD")
                name = "native.gz" if component == "mihomo" else "native.zip"
                check = {"ok": True, "update_available": True, "asset_url": "https://fixture.invalid/" + name,
                         "asset_name": name, "current": "1", "latest": "2"}
                with readiness_clock(self.app) as clock, \
                        patch.object(self.app, "check_component_update", return_value=check), \
                        patch.object(self.app, "_http_download", side_effect=lambda url, dst, **kw: Path(dst).write_bytes(archive)), \
                        patch.object(self.app, "_backup_binary", return_value=str(backup)), \
                        patch.object(self.app.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "", "")), \
                        patch.object(self.app, "_health_" + component, side_effect=lambda **kw: clock.now >= 2.0):
                    result = self.app.update_component_binary(component)
                self.assertTrue(result["ok"], result)
                self.assertEqual(binary.read_bytes(), b"NEW")
                self.assertEqual(Path(result["backup"]).read_bytes(), b"OLD")


    def test_rejected_rule_generation_preserves_previously_referenced_files(self):
        groups = [{"id": "fixture", "name": "Old", "enabled": True, "entries": [
            {"id": "one", "rule": "DOMAIN-SUFFIX,example.org", "enabled": True}]}]
        Path(self.app.GROUPS_FILE).write_text(json.dumps(groups), encoding="utf-8")
        config = Path(self.app.CONFIG_FILE)
        config.write_text("mode: rule\nproxies: []\nrule-providers: {}\nrules: []\n", encoding="utf-8")
        Path(self.app.MIHOMO_BIN).write_bytes(b"fixture-native-validator")
        self.app.apply_config(validate=False)
        old_config = config.read_bytes()
        old_providers = {p: p.read_bytes() for p in Path(self.app.GROUPS_DIR).rglob("*.yaml")}
        self.assertTrue(old_providers, "fixture must contain a referenced rule provider")
        groups[0]["name"] = "New"
        Path(self.app.GROUPS_FILE).write_text(json.dumps(groups), encoding="utf-8")
        with patch.object(self.app.subprocess, "run", return_value=subprocess.CompletedProcess(
                [], 1, "", "candidate rejected by native validator")):
            with self.assertRaises(RuntimeError):
                self.app.apply_config()
        self.assertEqual(config.read_bytes(), old_config)
        for path, contents in old_providers.items():
            self.assertTrue(path.exists(), "rejected apply removed an old referenced provider")
            self.assertEqual(path.read_bytes(), contents)

    def request(self, method, path, fields=None, cookie=None, csrf=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        if cookie:
            headers["Cookie"] = cookie
        if csrf:
            headers["X-CSRF-Token"] = csrf
        conn.request(method, path, urllib.parse.urlencode(fields or {}) if method == "POST" else None, headers)
        response = conn.getresponse()
        result = response.status, response.getheaders(), response.read()
        conn.close()
        return result

    def start_server(self):
        self.app.save_users([{"id": "fixture-admin", "username": "fixture-admin", "enabled": True,
                              "password_hash": self.app.hash_password("fixture-only-password")}])
        self.app.Handler.log_message = lambda *args: None
        server = self.app.ThreadingHTTPServer(("127.0.0.1", 0), self.app.Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        def stop():
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)
        self.addCleanup(stop)
        self.port = server.server_port
        status, headers, _ = self.request("POST", "/login", {
            "user": "fixture-admin", "pass": "fixture-only-password"})
        self.assertEqual(status, 303)
        cookie = next(v.split(";", 1)[0] for k, v in headers
                      if k.lower() == "set-cookie" and v.startswith("lists_session="))
        code, _, body = self.request("GET", "/api/session", cookie=cookie)
        csrf = json.loads(body).get("csrf") if code == 200 else None
        return cookie, csrf

    def test_invalid_connection_apply_does_not_replace_working_config(self):
        config = Path(self.app.XRAY_CONFIGS[0])
        config.write_text(json.dumps({"outbounds": [{"tag": "old-fixture", "protocol": "freedom"}]}), encoding="utf-8")
        old = config.read_bytes()
        cookie, csrf = self.start_server()
        link = "vless://11111111-1111-4111-8111-111111111111@fixture.invalid:70000?type=invalid-transport&security=none"
        with patch.object(self.app, "restart_xray", side_effect=RuntimeError("candidate cannot start")):
            status, _, _ = self.request("POST", "/api/xttp/apply", {"vless": link}, cookie, csrf)
        self.assertEqual(status, 400)
        self.assertEqual(config.read_bytes(), old, "failed connection apply left its candidate installed")

    def test_failed_restore_never_claims_successful_binary_rollback(self):
        binary = Path(self.app.MIHOMO_BIN)
        binary.write_bytes(b"OLD")
        backup = self.root / "backup"
        backup.write_bytes(b"OLD")
        real_copy = self.app.shutil.copy2
        def copy(src, dst, *args, **kwargs):
            if str(src) == str(backup):
                raise OSError("restore blocked")
            return real_copy(src, dst, *args, **kwargs)
        def download(url, dst, **kwargs):
            Path(dst).write_bytes(gzip.compress(b"NEW"))
        check = {"ok": True, "update_available": True, "asset_url": "https://fixture.invalid/native.gz",
                 "asset_name": "fixture.gz", "current": "1.0.0", "latest": "2.0.0"}
        with patch.object(self.app, "check_component_update", return_value=check), \
                patch.object(self.app, "_http_download", side_effect=download), \
                patch.object(self.app, "_backup_binary", return_value=str(backup)), \
                patch.object(self.app.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "", "")), \
                readiness_clock(self.app), \
                patch.object(self.app, "_health_mihomo", return_value=False), \
                patch.object(self.app.shutil, "copy2", side_effect=copy):
            result = self.app.update_component_binary("mihomo")
        self.assertFalse(result["ok"])
        self.assertEqual(binary.read_bytes(), b"NEW")
        self.assertFalse(result.get("rolled_back", False), "restore failure was reported as successful rollback")

    @unittest.skipIf(os.name == "nt", "POSIX secret permissions")
    def test_rewriting_xray_config_and_backup_keeps_credentials_private(self):
        config = Path(self.app.XRAY_CONFIGS[0])
        config.write_text(json.dumps({"outbounds": [{"tag": "fake-credential", "protocol": "freedom"}]}), encoding="utf-8")
        config.chmod(0o600)
        old_umask = os.umask(0o022)
        try:
            backup = self.app.backup_xray()
            self.app.write_xray_config({"fixture": "new-fake-credential"})
        finally:
            os.umask(old_umask)
        self.assertEqual(stat.S_IMODE(config.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE((Path(self.app.XRAY_BACKUP_DIR) / backup).stat().st_mode), 0o600)

    def test_group_batch_rejects_whole_transaction_and_preserves_untouched_rows(self):
        groups = [{"id": "g", "name": "Original", "enabled": True, "entries": [
            {"id": "a", "type": "namespace", "value": "example.org", "rule": "DOMAIN-SUFFIX,example.org", "local_note": "preserve"},
            {"id": "b", "type": "namespace", "value": "second.org", "rule": "DOMAIN-SUFFIX,second.org"}]}]
        Path(self.app.GROUPS_FILE).write_text(json.dumps(groups), encoding="utf-8")
        cookie, csrf = self.start_server()
        old = Path(self.app.GROUPS_FILE).read_bytes()
        code, _, body = self.request("POST", "/api/groups/save", {
            "gid": "g", "name": "Changed",
            "entries": json.dumps([{"id": "a", "type": "domain", "value": "good.org"},
                                   {"type": "ipv4", "value": "not-an-ip"}])}, cookie, csrf)
        self.assertEqual(code, 400, body)
        self.assertEqual(json.loads(body)["errors"][0]["index"], 1)
        self.assertEqual(Path(self.app.GROUPS_FILE).read_bytes(), old)
        code, _, body = self.request("POST", "/api/groups/save", {
            "gid": "g", "entries": json.dumps([{"id": "a", "type": "domain", "value": "good.org"}])},
            cookie, csrf)
        self.assertEqual(code, 200, body)
        saved = json.loads(Path(self.app.GROUPS_FILE).read_text(encoding="utf-8"))[0]
        self.assertEqual(saved["entries"][1]["id"], "b")
        self.assertEqual(saved["entries"][1]["rule"], "DOMAIN-SUFFIX,second.org")
        self.assertEqual(saved["entries"][0]["local_note"], "preserve")

    def test_group_summaries_pagination_and_import_use_current_raw_text(self):
        groups = [{"id": "g", "name": "Group", "enabled": True, "entries": [
            {"id": str(i), "type": "domain", "value": f"host{i}.org", "rule": f"DOMAIN,host{i}.org"}
            for i in range(8)]}]
        Path(self.app.GROUPS_FILE).write_text(json.dumps(groups), encoding="utf-8")
        cookie, csrf = self.start_server()
        code, _, body = self.request("GET", "/api/groups", cookie=cookie)
        self.assertEqual(code, 200)
        self.assertNotIn("entries", json.loads(body)["groups"][0])
        code, _, body = self.request("GET", "/api/groups/g?offset=1&limit=2&q=host", cookie=cookie)
        page = json.loads(body)
        self.assertEqual((code, page["total"], len(page["entries"])), (200, 8, 2))
        self.assertEqual(page["entries"][0]["id"], "1")
        code, _, body = self.request("POST", "/api/groups/import", {
            "gid": "g", "text": "first.org\nsecond.org\nfirst.org", "detect": "auto",
            "skip_conflicts": "1", "preview": "[stale]"}, cookie, csrf)
        self.assertEqual(code, 200, body)
        self.assertEqual(json.loads(body)["imported"], 2)
        self.assertEqual(json.loads(body)["skipped"], 1)
        rules = [e["rule"] for e in self.app.load_groups()[0]["entries"]]
        self.assertIn("DOMAIN-SUFFIX,second.org", rules)

    def test_import_conflict_option_preserves_cross_group_membership_and_destination_dedup(self):
        groups = [
            {"id": "destination", "name": "Destination", "enabled": True, "entries": [
                {"id": "keep", "type": "namespace", "value": "same.org", "rule": "DOMAIN-SUFFIX,same.org"}]},
            {"id": "foreign", "name": "Foreign", "enabled": True, "entries": [
                {"id": "foreign-row", "type": "namespace", "value": "shared.org",
                 "rule": "DOMAIN-SUFFIX,shared.org", "enabled": True, "name": ""}]}]
        cookie, csrf = self.start_server()
        for skip, imported, skipped, expected in (
                ("1", 1, 3, ["DOMAIN-SUFFIX,same.org", "DOMAIN-SUFFIX,new.org"]),
                ("0", 2, 2, ["DOMAIN-SUFFIX,same.org", "DOMAIN-SUFFIX,new.org", "DOMAIN-SUFFIX,shared.org"])):
            with self.subTest(skip_conflicts=skip):
                Path(self.app.GROUPS_FILE).write_text(json.dumps(groups), encoding="utf-8")
                code, _, body = self.request("POST", "/api/groups/import", {
                    "gid": "destination", "detect": "auto", "skip_conflicts": skip,
                    "text": "same.org\nnew.org\nshared.org\nshared.org"}, cookie, csrf)
                self.assertEqual(code, 200, body)
                result = json.loads(body)
                self.assertEqual((result["imported"], result["skipped"], result["invalid"]),
                                 (imported, skipped, 0))
                persisted = json.loads(Path(self.app.GROUPS_FILE).read_text(encoding="utf-8"))
                self.assertEqual([entry["rule"] for entry in persisted[0]["entries"]], expected)
                self.assertEqual(persisted[0]["entries"][0]["id"], "keep")
                self.assertEqual(persisted[1], groups[1])

    def test_session_csrf_migration_and_foreign_origin_do_not_revoke_session(self):
        cookie, csrf = self.start_server()
        self.assertTrue(csrf)
        token = urllib.parse.unquote(cookie.split("=", 1)[1])
        key = self.app.hashlib.sha256(token.encode()).hexdigest()
        sessions = json.loads(Path(self.app.SESSIONS_FILE).read_text())
        sessions[key].pop("csrf", None)
        Path(self.app.SESSIONS_FILE).write_text(json.dumps(sessions))
        code, _, body = self.request("GET", "/api/session", cookie=cookie)
        csrf = json.loads(body)["csrf"]
        self.assertEqual(code, 200)
        code, _, _ = self.request("POST", "/api/users/add", {
            "username": "blocked", "password": "fixture-password"}, cookie)
        self.assertEqual(code, 403)
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request("POST", "/api/users/add", "username=blocked&password=fixture-password",
                     {"Cookie": cookie, "X-CSRF-Token": csrf, "Origin": "http://127.0.0.1:1",
                      "Content-Type": "application/x-www-form-urlencoded"})
        response = conn.getresponse()
        self.assertEqual(response.status, 403)
        response.read()
        conn.close()
        self.assertEqual(self.request("GET", "/api/session", cookie=cookie)[0], 200)
        self.assertIsNone(self.app.find_user("blocked"))

    def test_http_body_rejects_ambiguous_negative_and_oversized_lengths(self):
        self.start_server()
        for length, expected in (("-1", 400), ("abc", 400), ("2097153", 413),
                                 ("0\r\nContent-Length: 0", 400)):
            with self.subTest(length=length):
                sock = self.app.socket.create_connection(("127.0.0.1", self.port), timeout=3)
                sock.sendall(("POST /login HTTP/1.0\r\nHost: localhost\r\nContent-Length: "
                              + length + "\r\n\r\n").encode())
                response = http.client.HTTPResponse(sock)
                response.begin()
                self.assertEqual(response.status, expected)
                response.read()
                sock.close()

    def test_epoch_reset_returns_current_history_and_log_cursor(self):
        self.app._traffic_history.extend([{"t": 10, "upRate": 1, "downRate": 2},
                                         {"t": 11, "upRate": 2, "downRate": 3}])
        with patch.object(self.app, "mihomo_get", return_value={}), \
                patch.object(self.app, "load_groups", return_value=[]), \
                patch.object(self.app, "start_log_sampler"):
            first = self.app.stats_snapshot()
            delta = self.app.stats_snapshot(since=10, epoch=first["epoch"])
            reset = self.app.stats_snapshot(since=999, epoch="previous-process")
            self.assertEqual(len(first["history"]), 2)
            self.assertEqual([p["t"] for p in delta["history"]], [11])
            self.assertEqual(len(reset["history"]), 2)
            self.app._log_push("info", "current")
            logs = self.app.logs_snapshot(since=999, mode="live", epoch="previous-process")
            self.assertEqual(logs["items"][0]["payload"], "current")
            self.assertTrue(logs["epoch"])

    def test_start_timeout_restores_actual_old_binary(self):
        binary = Path(self.app.MIHOMO_BIN)
        binary.write_bytes(b"OLD")
        backup = self.root / "backup"
        backup.write_bytes(b"OLD")
        check = {"ok": True, "update_available": True, "asset_url": "https://fixture.invalid/native.gz",
                 "asset_name": "fixture.gz", "current": "1", "latest": "2"}
        starts = 0
        def command(args, **kwargs):
            nonlocal starts
            if args[:2] == ["systemctl", "start"]:
                starts += 1
                if starts == 1:
                    raise subprocess.TimeoutExpired(args, 30)
            return subprocess.CompletedProcess(args, 0, "", "")
        with patch.object(self.app, "check_component_update", return_value=check), \
                patch.object(self.app, "_http_download",
                             side_effect=lambda url, dst, **kw: Path(dst).write_bytes(gzip.compress(b"NEW"))), \
                patch.object(self.app, "_backup_binary", return_value=str(backup)), \
                patch.object(self.app.subprocess, "run", side_effect=command), \
                readiness_clock(self.app) as clock, \
                patch.object(self.app, "_health_mihomo", side_effect=lambda **kw: clock.now >= 2.0):
            result = self.app.update_component_binary("mihomo")
        self.assertFalse(result["ok"])
        self.assertTrue(result["rolled_back"], result)
        self.assertEqual(binary.read_bytes(), b"OLD")

    def test_operation_launch_failure_is_persisted_and_inspectable(self):
        with patch.object(self.app.subprocess, "run", return_value=subprocess.CompletedProcess(
                [], 1, "", "worker launch denied")):
            accepted = self.app.start_operation("mihomo", user="fixture-admin")
        operation = self.app.operation_snapshot(accepted["operation_id"])
        self.assertEqual(operation["status"], "failed")
        self.assertIn("worker launch denied", operation["error"])

    def test_preparation_resumes_publication_recorded_before_interruption(self):
        Path(self.app.GROUPS_FILE).write_text("[]", encoding="utf-8")
        config = Path(self.app.CONFIG_FILE)
        config.write_text("mode: rule\nrule-providers: {}\nrules: []\n", encoding="utf-8")
        marker = Path(str(config) + ".xttp-prepare.sha256")
        marker.write_text(self.app._file_hash(str(config)), encoding="ascii")
        original = self.app.atomic_json
        def interrupted(path, payload):
            if str(path).endswith("prepare-config.json") and payload.get("stage") == "completed":
                raise OSError("interrupted after publication")
            return original(path, payload)
        with patch.object(self.app, "atomic_json", side_effect=interrupted):
            with self.assertRaises(OSError):
                self.app.prepare_config()
        published = config.read_bytes()
        self.app.prepare_config()
        self.assertEqual(config.read_bytes(), published)
        self.assertFalse(marker.exists())
        config.write_bytes(b"user-edited config")
        self.app.prepare_config()
        self.assertEqual(config.read_bytes(), b"user-edited config")

    def test_failed_xray_restart_recovers_exact_previous_bytes(self):
        config = Path(self.app.XRAY_CONFIGS[0])
        old = b'{ "outbounds": [], "fixture-secret": "private" }\n'
        config.write_bytes(old)
        with patch.object(self.app.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "", "")), \
                patch.object(self.app, "restart_xray", side_effect=[RuntimeError("start failed"), None]), \
                patch.object(self.app, "_health_xray", return_value=True), \
                patch.object(self.app, "socks_http_probe", return_value={"ok": True}):
            result = self.app.activate_xray({"outbounds": [{"protocol": "freedom"}]})
        self.assertFalse(result["ok"])
        self.assertEqual(result["rollback"]["status"], "succeeded")
        self.assertEqual(config.read_bytes(), old)

    def test_operation_terminal_failure_survives_module_reload(self):
        record = self.app._create_operation("mihomo", "fixture")
        with patch.object(self.app, "update_component_binary", return_value={
                "ok": False, "error": "child failed", "rollback": {"status": "failed", "error": "restore denied"}}):
            self.assertEqual(self.app.run_operation(record["id"]), 1)
        spec = importlib.util.spec_from_file_location("operation_reloaded_" + uuid.uuid4().hex, APP_PATH)
        restarted = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(restarted)
        restarted.DATA_DIR = self.app.DATA_DIR
        operation = restarted.operation_snapshot(record["id"])
        self.assertEqual(operation["status"], "failed")
        self.assertEqual(operation["error"], "child failed")
        self.assertEqual(operation["rollback"]["status"], "failed")

    def test_other_process_holds_the_same_update_lock(self):
        script = (
            "import importlib.util,sys; "
            "spec=importlib.util.spec_from_file_location('child',sys.argv[1]); "
            "app=importlib.util.module_from_spec(spec);spec.loader.exec_module(app);"
            "app._update_lock.acquire();print('locked',flush=True);"
            "sys.stdin.readline();app._update_lock.release()")
        child = subprocess.Popen([sys.executable, "-c", script, str(APP_PATH)],
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            self.assertEqual(child.stdout.readline().strip(), "locked")
            binary = Path(self.app.MIHOMO_BIN)
            binary.write_bytes(b"OLD")
            with patch.object(self.app, "check_component_update", return_value={
                    "ok": True, "update_available": False, "current": "1.0.0"}):
                result = self.app.update_component_binary("mihomo")
            self.assertFalse(result["ok"], result)
            self.assertEqual(binary.read_bytes(), b"OLD")
        finally:
            child.communicate("\\n", timeout=10)
            self.assertEqual(child.returncode, 0)

    def test_two_processes_append_journal_without_lost_events(self):
        script = (
            "import importlib.util,sys;"
            "spec=importlib.util.spec_from_file_location('child',sys.argv[1]);"
            "app=importlib.util.module_from_spec(spec);spec.loader.exec_module(app);"
            "app.DATA_DIR=sys.argv[2];app.ACTIVITY_FILE=sys.argv[3];"
            "app.XRAY_BACKUP_DIR=sys.argv[2]+'/xray';app.GROUPS_BACKUP_DIR=sys.argv[2]+'/groups';"
            "app.GROUPS_YAML_QUARANTINE=sys.argv[2]+'/quarantine';"
            "[app.log_activity(sys.argv[4],'fixture',str(i)) for i in range(20)]")
        children = [subprocess.Popen([sys.executable, "-c", script, str(APP_PATH), self.app.DATA_DIR,
                                      self.app.ACTIVITY_FILE, name], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                    for name in ("one", "two")]
        for child in children:
            _, error = child.communicate(timeout=20)
            self.assertEqual(child.returncode, 0, error)
        events = self.app.load_activity()
        self.assertEqual(len(events), 40)
        self.assertEqual({event["user"] for event in events}, {"one", "two"})

    def test_static_bundle_is_coherent_and_revalidates_after_checkout_changes(self):
        bundle = self.root / "checkout" / "panel"
        (bundle / "templates").mkdir(parents=True)
        (bundle / "static").mkdir()
        (bundle / "templates" / "panel.html").write_text("panel @@VERSION@@ @@USERNAME@@", encoding="utf-8")
        (bundle / "templates" / "login.html").write_text("login @@CONTENT@@", encoding="utf-8")
        for name in ("panel.css", "app.js", "rules.js", "monitoring.js", "system.js"):
            (bundle / "static" / name).write_text("original " + name, encoding="utf-8")
        self.app.__file__ = str(bundle / "app.py")
        cookie, _ = self.start_server()
        code, headers, body = self.request("GET", "/static/app.js")
        self.assertEqual((code, body), (200, b"original app.js"))
        etag = next(value for key, value in headers if key.lower() == "etag")
        (bundle / "static" / "app.js").write_text("new incompatible bundle", encoding="utf-8")
        self.assertEqual(self.request("GET", "/static/app.js")[2], b"original app.js")
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request("GET", "/static/app.js", headers={"If-None-Match": etag})
        response = conn.getresponse()
        self.assertEqual(response.status, 304)
        response.read()
        conn.close()
        self.assertEqual(self.request("GET", "/rules", cookie=cookie)[0], 200)

    @unittest.skipIf(os.name == "nt", "POSIX existing secret permissions")
    def test_startup_hardens_existing_secrets_without_changing_bytes(self):
        config = Path(self.app.XRAY_CONFIGS[0])
        config.write_bytes(b'{"fixture-secret":"unchanged"}')
        config.chmod(0o644)
        backup_dir = Path(self.app.XRAY_BACKUP_DIR)
        backup_dir.chmod(0o755)
        backup = backup_dir / "old.json"
        backup.write_bytes(config.read_bytes())
        backup.chmod(0o644)
        old = config.read_bytes()
        self.app.harden_xray_credentials()
        self.assertEqual(stat.S_IMODE(config.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(backup.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(backup_dir.stat().st_mode), 0o700)
        self.assertEqual(config.read_bytes(), old)
        self.assertEqual(backup.read_bytes(), old)

    def test_slow_drip_body_still_expires_at_absolute_deadline(self):
        self.start_server()
        sock = self.app.socket.create_connection(("127.0.0.1", self.port), timeout=13)
        sock.sendall(b"POST /login HTTP/1.0\r\nHost: localhost\r\nContent-Length: 1000\r\n\r\nu")
        stopped = threading.Event()
        def drip():
            while not stopped.wait(0.2):
                try:
                    sock.sendall(b"x")
                except OSError:
                    return
        sender = threading.Thread(target=drip, daemon=True)
        sender.start()
        started = self.app.time.monotonic()
        try:
            response = http.client.HTTPResponse(sock)
            response.begin()
            self.assertEqual(response.status, 408)
            self.assertLess(self.app.time.monotonic() - started, 12)
            response.read()
        finally:
            stopped.set()
            sender.join(timeout=2)
            sock.close()

    def test_rule_publication_preserves_unmanaged_top_level_configuration(self):
        Path(self.app.GROUPS_FILE).write_text("[]", encoding="utf-8")
        config = Path(self.app.CONFIG_FILE)
        config.write_text("mode: rule\nrule-providers: {}\nrules: []\nallow-lan: true\nmixed-port: 17890\n",
                          encoding="utf-8")
        self.app.apply_config(validate=False)
        published = config.read_text(encoding="utf-8")
        self.assertIn("allow-lan: true\nmixed-port: 17890\n", published)
        self.assertIn("GEOIP,PRIVATE,DIRECT", published)

    def test_native_xray_rejection_preserves_working_config_without_secret_echo(self):
        config = Path(self.app.XRAY_CONFIGS[0])
        config.write_bytes(b'{"outbounds":[{"protocol":"freedom"}]}')
        old = config.read_bytes()
        with patch.object(self.app.subprocess, "run", return_value=subprocess.CompletedProcess(
                [], 23, "", "native output contains fixture-private-credential")):
            with self.assertRaises(ValueError) as rejected:
                self.app.activate_xray({"outbounds": [{"protocol": "invalid"}]})
        self.assertNotIn("fixture-private-credential", str(rejected.exception))
        self.assertEqual(config.read_bytes(), old)
        self.assertEqual(self.app.list_xray_backups(), [])

    def test_rule_restart_failure_restores_old_generation_and_truthful_state(self):
        group = {"id": "g", "name": "Before", "enabled": True, "entries": [
            {"id": "a", "type": "domain", "value": "before.org",
             "rule": "DOMAIN,before.org", "enabled": True}]}
        Path(self.app.GROUPS_FILE).write_text(json.dumps([group]), encoding="utf-8")
        config = Path(self.app.CONFIG_FILE)
        config.write_text("mode: rule\nrule-providers: {}\nrules: []\n", encoding="utf-8")
        self.app.apply_config(validate=False)
        old = config.read_bytes()
        providers = {path: path.read_bytes() for path in Path(self.app.GROUPS_DIR).rglob("*.yaml")}
        group["name"] = "After"
        Path(self.app.GROUPS_FILE).write_text(json.dumps([group]), encoding="utf-8")
        with patch.object(self.app.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "", "")), \
                patch.object(self.app, "restart_mihomo", side_effect=[RuntimeError("start failed"), None]), \
                patch.object(self.app, "_health_mihomo", return_value=True):
            result = self.app.activate_rules()
        self.assertFalse(result["ok"])
        self.assertEqual(result["rollback"]["status"], "succeeded")
        self.assertEqual(config.read_bytes(), old)
        for path, contents in providers.items():
            self.assertEqual(path.read_bytes(), contents)
        self.assertNotEqual(result["state"]["status"], "applied")

    def test_http_update_acceptance_is_not_terminal_success(self):
        cookie, csrf = self.start_server()
        with patch.object(self.app.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "", "")):
            code, _, body = self.request("POST", "/api/updates/start", {
                "component": "mihomo", "confirm": "1"}, cookie, csrf)
        accepted = json.loads(body)
        self.assertEqual(code, 202, body)
        self.assertTrue(accepted["operation_id"])
        code, _, body = self.request("GET", "/api/operations/" + accepted["operation_id"], cookie=cookie)
        operation = json.loads(body)["operation"]
        self.assertEqual((code, operation["status"]), (200, "queued"))
        self.assertIsNone(operation["result"])
        self.assertNotIn("_user", operation)

    @unittest.skipIf(os.name == "nt", "Winsock shutdown does not wake pending recv; Linux runtime contract")
    def test_log_level_post_persists_preference_and_interrupts_idle_stream(self):
        cookie, csrf = self.start_server()
        stream, peer = self.app.socket.socketpair()
        self.addCleanup(stream.close)
        self.addCleanup(peer.close)
        self.app._log_stream_socket = stream
        interrupted = threading.Event()
        def waiting_stream():
            if stream.recv(1) == b"":
                interrupted.set()
        reader = threading.Thread(target=waiting_stream, daemon=True)
        reader.start()
        with patch.object(self.app, "start_log_sampler"):
            code, _, body = self.request("POST", "/api/logs/level", {"level": "debug"}, cookie, csrf)
        self.assertEqual(code, 200, body)
        self.assertEqual(json.loads(body)["level"], "debug")
        self.assertTrue(interrupted.wait(2))
        reader.join(timeout=2)
        self.assertEqual(json.loads(Path(self.app.PREFS_FILE).read_text(encoding="utf-8"))["log_level"], "debug")


if __name__ == "__main__":
    unittest.main()
