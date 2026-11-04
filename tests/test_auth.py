import http.client
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
import urllib.parse


APP_PATH = Path(__file__).resolve().parents[1] / "panel" / "app.py"


class AuthenticationTests(unittest.TestCase):
    def setUp(self):
        sys.dont_write_bytecode = True
        spec = importlib.util.spec_from_file_location("auth_test_panel", APP_PATH)
        self.app = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.app)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        for name in ("DATA_DIR", "XRAY_BACKUP_DIR", "GROUPS_BACKUP_DIR", "GROUPS_YAML_QUARANTINE"):
            setattr(self.app, name, str(root / name))
        for name in ("USERS_FILE", "SESSIONS_FILE", "ACTIVITY_FILE", "BOOTSTRAP_PASSWORD_FILE"):
            setattr(self.app, name, str(root / name))
        self.app.Handler.log_message = lambda *args: None

    def start_server(self):
        self.app.save_users([{
            "id": "test-admin", "username": "admin", "enabled": True,
            "password_hash": self.app.hash_password("original-password"),
        }])
        server = self.app.ThreadingHTTPServer(("127.0.0.1", 0), self.app.Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        def stop():
            server.shutdown()
            server.server_close()
            thread.join()
        self.addCleanup(stop)
        self.port = server.server_port
        status, headers, _ = self.request("POST", "/login", {"user": "admin", "pass": "original-password"})
        self.assertEqual(status, 303)
        return next(value.split(";", 1)[0] for key, value in headers
                    if key.lower() == "set-cookie" and value.startswith("lists_session="))

    def request(self, method, path, fields=None, cookie=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        if cookie:
            headers["Cookie"] = cookie
            if method in ("POST", "DELETE"):
                status, _, body = self.request("GET", "/api/session", cookie=cookie)
                self.assertEqual(status, 200, body)
                headers["X-CSRF-Token"] = json.loads(body)["csrf"]
        conn.request(method, path, urllib.parse.urlencode(fields) if fields is not None else None, headers)
        response = conn.getresponse()
        result = response.status, response.getheaders(), response.read()
        conn.close()
        return result

    def test_bootstrap_rejects_public_default_password(self):
        users = self.app.load_users()
        self.assertFalse(self.app.verify_password("changeme", users[0]["password_hash"]))
        password_file = Path(self.app.BOOTSTRAP_PASSWORD_FILE)
        self.assertTrue(password_file.is_file(), "initial credential must be available locally")
        password = password_file.read_text(encoding="utf-8").strip()
        self.assertTrue(self.app.verify_password(password, users[0]["password_hash"]))
        self.assertEqual(self.app.load_users()[0]["password_hash"], users[0]["password_hash"])

    def test_startup_rotates_legacy_default_password_but_keeps_custom_passwords(self):
        self.app.save_users([
            {"id": "legacy", "username": "admin", "enabled": True,
             "password_hash": self.app.hash_password("changeme")},
            {"id": "custom", "username": "operator", "enabled": True,
             "password_hash": self.app.hash_password("custom-password")},
        ])
        self.app.initialize_users()
        self.assertFalse(self.app.verify_password("changeme", self.app.find_user("admin")["password_hash"]))
        password = Path(self.app.BOOTSTRAP_PASSWORD_FILE).read_text(encoding="utf-8").strip()
        self.assertTrue(self.app.verify_password(password, self.app.find_user("admin")["password_hash"]))
        self.assertTrue(self.app.verify_password("custom-password", self.app.find_user("operator")["password_hash"]))

    def test_password_change_revokes_existing_session(self):
        cookie = self.start_server()
        status, _, body = self.request("POST", "/api/users/password", {
            "id": "test-admin", "password": "replacement-password",
        }, cookie)
        self.assertEqual(status, 200, body)
        self.assertTrue(json.loads(body).get("relogin"), "the client must leave the revoked session")
        self.assertEqual(self.request("GET", "/api/users", cookie=cookie)[0], 401)
        status, _, _ = self.request("POST", "/login", {"user": "admin", "pass": "replacement-password"})
        self.assertEqual(status, 303)

    def test_logout_revokes_only_the_current_session(self):
        first = self.start_server()
        _, headers, _ = self.request("POST", "/login", {"user": "admin", "pass": "original-password"})
        second = next(value.split(";", 1)[0] for key, value in headers
                      if key.lower() == "set-cookie" and value.startswith("lists_session="))
        self.assertEqual(self.request("POST", "/logout", {}, first)[0], 303)
        self.assertEqual(self.request("GET", "/api/users", cookie=first)[0], 401)
        self.assertEqual(self.request("GET", "/api/users", cookie=second)[0], 200)

    def test_recreated_username_cannot_reuse_deleted_users_session(self):
        cookie = self.start_server()
        users = self.app.load_users()
        users[0]["id"] = "different-admin"
        self.app.save_users(users)
        self.assertEqual(self.request("GET", "/api/users", cookie=cookie)[0], 401)

    def test_corrupt_session_store_revokes_tokens_without_locking_out_valid_users(self):
        cookie = self.start_server()
        for broken in ("{\"truncated\":", "[]"):
            with self.subTest(store=broken):
                Path(self.app.SESSIONS_FILE).write_text(broken, encoding="utf-8")
                self.assertEqual(self.request("GET", "/api/users", cookie=cookie)[0], 401)
                status, headers, _ = self.request("POST", "/login", {
                    "user": "admin", "pass": "original-password",
                })
                self.assertEqual(status, 303)
                replacement = next(value.split(";", 1)[0] for key, value in headers
                                   if key.lower() == "set-cookie" and value.startswith("lists_session="))
                self.assertEqual(self.request("GET", "/api/users", cookie=replacement)[0], 200)
                self.assertEqual(self.request("GET", "/api/users", cookie=cookie)[0], 401)

    def test_slow_login_body_does_not_block_other_logins(self):
        self.start_server()
        reading = threading.Event()
        original_form = self.app.Handler._form

        def read_form(handler):
            if handler.headers.get("X-Slow-Request") == "1":
                reading.set()
            return original_form(handler)

        self.app.Handler._form = read_form
        slow = self.app.socket.create_connection(("127.0.0.1", self.port), timeout=2)
        try:
            slow.sendall(b"POST /login HTTP/1.0\r\nHost: localhost\r\nContent-Length: 64\r\nX-Slow-Request: 1\r\n\r\nu")
            self.assertTrue(reading.wait(2), "slow request must reach the body reader")
            normal = http.client.HTTPConnection("127.0.0.1", self.port, timeout=1)
            try:
                normal.request("POST", "/login", "user=admin&pass=original-password",
                               {"Content-Type": "application/x-www-form-urlencoded"})
                response = normal.getresponse()
                self.assertEqual(response.status, 303)
                response.read()
            finally:
                normal.close()
        finally:
            slow.shutdown(self.app.socket.SHUT_WR)
            slow.settimeout(2)
            while slow.recv(4096):
                pass
            slow.close()

    def test_sessions_survive_process_state_reload(self):
        cookie = self.start_server()
        token = urllib.parse.unquote(cookie.split("=", 1)[1])
        spec = importlib.util.spec_from_file_location("restarted_panel", APP_PATH)
        restarted = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(restarted)
        for name in ("DATA_DIR", "XRAY_BACKUP_DIR", "GROUPS_BACKUP_DIR", "GROUPS_YAML_QUARANTINE", "USERS_FILE", "SESSIONS_FILE"):
            setattr(restarted, name, getattr(self.app, name))
        self.assertEqual(restarted.verify_session_token(token), "admin")


if __name__ == "__main__":
    unittest.main()
