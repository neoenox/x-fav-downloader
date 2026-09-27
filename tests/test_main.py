import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import main


class MainLogicTest(unittest.TestCase):
    def test_auth_error_classification(self):
        self.assertTrue(main.looks_like_auth_error("HTTP 401 Unauthorized"))
        self.assertTrue(main.looks_like_auth_error("login required"))
        self.assertFalse(main.looks_like_auth_error("download completed"))

    def test_import_cookies_filters_non_x_domains_and_requires_auth_token(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / "cookies.txt"
            source.write_text(
                "# Netscape HTTP Cookie File\n"
                ".x.com\tTRUE\t/\tTRUE\t2147483647\tauth_token\tsecret\n"
                ".example.com\tTRUE\t/\tFALSE\t2147483647\tother\tignore\n",
                encoding="utf-8",
            )
            auth_dir = root / "auth"
            storage = auth_dir / "storage_state.json"
            exported = auth_dir / "cookies.txt"
            with (
                mock.patch.object(main, "AUTH_DIR", str(auth_dir)),
                mock.patch.object(main, "STORAGE_STATE", str(storage)),
                mock.patch.object(main, "COOKIES_TXT", str(exported)),
            ):
                self.assertTrue(main.import_cookies_txt(str(source)))
                state = json.loads(storage.read_text(encoding="utf-8"))
                self.assertEqual([c["domain"] for c in state["cookies"]], [".x.com"])
                self.assertEqual(state["cookies"][0]["name"], "auth_token")

    def test_import_cookies_rejects_missing_auth_token(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / "cookies.txt"
            source.write_text(
                ".twitter.com\tTRUE\t/\tTRUE\t2147483647\tct0\tcsrf\n",
                encoding="utf-8",
            )
            auth_dir = root / "auth"
            with (
                mock.patch.object(main, "AUTH_DIR", str(auth_dir)),
                mock.patch.object(main, "STORAGE_STATE", str(auth_dir / "storage_state.json")),
                mock.patch.object(main, "COOKIES_TXT", str(auth_dir / "cookies.txt")),
            ):
                self.assertFalse(main.import_cookies_txt(str(source)))

    def test_rename_sanitizes_username_and_avoids_collision(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            first = root / "a.JPG"
            first.write_bytes(b"a")
            _, first_name = main.rename_to_spec(
                str(first), str(root), "bad/name !", "20260927", "123", 0
            )
            second = root / "b.JPG"
            second.write_bytes(b"b")
            _, second_name = main.rename_to_spec(
                str(second), str(root), "bad/name !", "20260927", "123", 0
            )
            self.assertEqual(first_name, "20260927_123_0_bad_name_.jpg")
            self.assertEqual(second_name, "20260927_123_0_bad_name__1.jpg")

    def test_process_staging_keeps_all_media_for_one_tweet_and_marks_seen_once(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            out_dir = root / "out"
            staging = out_dir / "_staging"
            staging.mkdir(parents=True)
            for idx in (1, 2):
                media = staging / f"123_{idx}.jpg"
                media.write_bytes(bytes([idx]))
                Path(str(media) + ".json").write_text(
                    json.dumps(
                        {
                            "author": {"name": "alice"},
                            "date": "2026-09-27 12:34:56",
                            "content": "hello",
                            "type": "image",
                        }
                    ),
                    encoding="utf-8",
                )

            self.assertEqual(main.process_staging(str(out_dir), str(staging), 1), 1)
            saved = sorted(p.name for p in out_dir.glob("20260927_123_*.jpg"))
            self.assertEqual(
                saved,
                ["20260927_123_0_alice.jpg", "20260927_123_1_alice.jpg"],
            )
            seen = json.loads((out_dir / "seen.json").read_text(encoding="utf-8"))
            self.assertEqual(seen, ["123"])

            # A later duplicate is removed rather than re-added to metadata.
            duplicate = staging / "123_1.jpg"
            staging.mkdir(parents=True, exist_ok=True)
            duplicate.write_bytes(b"x")
            self.assertEqual(main.process_staging(str(out_dir), str(staging), 1), 0)
            lines = (out_dir / "metadata.jsonl").read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 1)


if __name__ == "__main__":
    unittest.main()
