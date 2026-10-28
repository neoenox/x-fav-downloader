import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

import main


class LoginStateSafetyTest(unittest.TestCase):
    def test_missing_token_preserves_existing_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "storage_state.json"
            original = b'{"cookies":[{"name":"auth_token","value":"old"}],"origins":[]}'
            state.write_bytes(original)
            self.assertFalse(
                main.save_authenticated_storage_state(
                    {"cookies": [{"name": "ct0", "value": "csrf"}]}, str(state)
                )
            )
            self.assertEqual(state.read_bytes(), original)
            self.assertEqual(list(Path(tmp).glob(".login-state-*")), [])

    def test_empty_token_and_invalid_state_do_not_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "storage_state.json"
            state.write_bytes(b"old")
            for invalid in (
                None,
                {"cookies": "invalid"},
                {"cookies": [{"name": "auth_token", "value": ""}]},
            ):
                self.assertFalse(main.save_authenticated_storage_state(invalid, str(state)))
                self.assertEqual(state.read_bytes(), b"old")

    def test_valid_token_atomically_replaces_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "storage_state.json"
            state.write_bytes(b"old")
            data = {"cookies": [{"name": "auth_token", "value": "new"}], "origins": []}
            self.assertTrue(main.save_authenticated_storage_state(data, str(state)))
            self.assertEqual(json.loads(state.read_text(encoding="utf-8")), data)
            self.assertEqual(list(Path(tmp).glob(".login-state-*")), [])

    def test_replace_failure_preserves_existing_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "storage_state.json"
            state.write_bytes(b"old")
            data = {"cookies": [{"name": "auth_token", "value": "new"}], "origins": []}
            with mock.patch.object(main.os, "replace", side_effect=OSError("simulated")):
                with self.assertRaises(OSError):
                    main.save_authenticated_storage_state(data, str(state))
            self.assertEqual(state.read_bytes(), b"old")
            self.assertEqual(list(Path(tmp).glob(".login-state-*")), [])

    def test_fsync_failure_preserves_existing_state_and_cleans_temp(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "storage_state.json"
            original = b'{"cookies":[{"name":"auth_token","value":"old"}],"origins":[]}'
            state.write_bytes(original)
            data = {"cookies": [{"name": "auth_token", "value": "new"}], "origins": []}
            with mock.patch.object(main.os, "fsync", side_effect=OSError("disk sync failed")):
                with self.assertRaises(OSError):
                    main.save_authenticated_storage_state(data, str(state))
            self.assertEqual(state.read_bytes(), original)
            self.assertEqual(list(Path(tmp).glob(".login-state-*")), [])

    def test_guest_browser_login_keeps_previous_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            auth = root / "auth"
            auth.mkdir()
            state = auth / "storage_state.json"
            original = b'{"cookies":[{"name":"auth_token","value":"old"}],"origins":[]}'
            state.write_bytes(original)

            page = mock.Mock()
            page.url = "https://x.com/"
            page.title.return_value = "X"
            context = mock.Mock()
            context.pages = [page]
            context.storage_state.return_value = {"cookies": [], "origins": []}
            playwright = mock.Mock()
            playwright.chromium.launch_persistent_context.return_value = context
            manager = mock.MagicMock()
            manager.__enter__.return_value = playwright
            sync_api = types.ModuleType("playwright.sync_api")
            sync_api.sync_playwright = lambda: manager
            pkg = types.ModuleType("playwright")
            pkg.sync_api = sync_api

            with (
                mock.patch.dict(sys.modules, {"playwright": pkg, "playwright.sync_api": sync_api}),
                mock.patch.object(main, "AUTH_DIR", str(auth)),
                mock.patch.object(main, "STORAGE_STATE", str(state)),
                mock.patch.object(main, "COOKIES_TXT", str(auth / "cookies.txt")),
                mock.patch.object(main.time, "sleep"),
                mock.patch("builtins.input", return_value=""),
                mock.patch.object(main, "export_cookies_txt") as export,
            ):
                main.do_login()

            self.assertEqual(state.read_bytes(), original)
            context.close.assert_called_once()
            export.assert_not_called()
            context.storage_state.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
