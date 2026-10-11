import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import main


class CookieImportSafetyTest(unittest.TestCase):
    def _import(self, directory: Path, cookie_lines: str) -> tuple[bool, Path]:
        auth = directory / "auth"
        auth.mkdir(exist_ok=True)
        state = auth / "storage_state.json"
        source = directory / "cookies-input.txt"
        source.write_text(cookie_lines, encoding="utf-8")
        with (
            mock.patch.object(main, "AUTH_DIR", str(auth)),
            mock.patch.object(main, "STORAGE_STATE", str(state)),
            mock.patch.object(main, "COOKIES_TXT", str(auth / "cookies.txt")),
        ):
            result = main.import_cookies_txt(str(source))
        return result, state

    def test_missing_auth_token_preserves_existing_state(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            auth = root / "auth"
            auth.mkdir()
            existing = auth / "storage_state.json"
            original = b'{"cookies":[{"name":"auth_token","value":"previous"}],"origins":[]}'
            existing.write_bytes(original)
            result, state = self._import(
                root, ".x.com\\tTRUE\\t/\\tTRUE\\t2147483647\\tct0\\tcsrf\\n"
            )
            self.assertFalse(result)
            self.assertEqual(state.read_bytes(), original)
            self.assertFalse(list(auth.glob("*.tmp")))

    def test_deceptive_domains_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            result, state = self._import(
                Path(temp),
                "x.com.evil.example\\tTRUE\\t/\\tTRUE\\t2147483647\\tauth_token\\tfake\\n"
                "notx.com\\tTRUE\\t/\\tTRUE\\t2147483647\\tauth_token\\tfake\\n",
            )
            self.assertFalse(result)
            self.assertFalse(state.exists())

    def test_valid_domains_can_update_storage(self):
        for domain in ("x.com", ".x.com", "twitter.com", ".twitter.com", "api.x.com"):
            with self.subTest(domain=domain), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                result, state = self._import(
                    root,
                    f"{domain}\\tTRUE\\t/\\tTRUE\\t2147483647\\tauth_token\\tvalid\\n"
                    "evil.x.com.invalid\\tTRUE\\t/\\tTRUE\\t2147483647\\tct0\\tbad\\n",
                )
                self.assertTrue(result)
                cookies = json.loads(state.read_text(encoding="utf-8"))["cookies"]
                self.assertEqual([cookie["domain"] for cookie in cookies], [domain])


if __name__ == "__main__":
    unittest.main()
