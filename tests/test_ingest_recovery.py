import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import main


class IngestRecoveryTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.output = Path(self.temp.name) / "out"
        self.staging = self.output / "_staging"
        self.staging.mkdir(parents=True)

    def stage(self, tweet="123", count=2):
        for idx in range(1, count + 1):
            source = self.staging / f"{tweet}_{idx}.jpg"
            source.write_bytes(bytes([idx]))
            Path(str(source) + ".json").write_text(
                json.dumps({
                    "author": {"name": "alice"},
                    "date": "2026-09-27 12:34:56",
                    "type": "image",
                }),
                encoding="utf-8",
            )

    def records(self):
        return [
            json.loads(line)
            for line in (self.output / "metadata.jsonl").read_text(
                encoding="utf-8"
            ).splitlines()
        ]

    def test_corrupt_seen_stops_without_overwriting_history_or_files(self):
        path = self.output / "seen.json"
        path.write_text("{truncated", encoding="utf-8")
        self.stage()
        with self.assertRaisesRegex(RuntimeError, "復旧"):
            main.process_staging(str(self.output), str(self.staging), 1)
        self.assertEqual(path.read_text(encoding="utf-8"), "{truncated")
        self.assertEqual(len(list(self.staging.glob("*.jpg"))), 2)
        self.assertFalse((self.output / "metadata.jsonl").exists())

    def test_failed_atomic_replace_preserves_prior_history_then_replays(self):
        seen_path = self.output / "seen.json"
        main.save_seen({"older"}, str(seen_path))
        self.stage()
        real_replace = os.replace
        attempts = 0

        def fail_second(src, dst):
            nonlocal attempts
            attempts += 1
            if attempts == 2:  # First is journal, second is seen index.
                raise OSError("simulated disk-full during index swap")
            return real_replace(src, dst)

        with mock.patch.object(main.os, "replace", side_effect=fail_second):
            with self.assertRaises(OSError):
                main.process_staging(str(self.output), str(self.staging), 1)
        self.assertEqual(json.loads(seen_path.read_text(encoding="utf-8")), ["older"])
        self.assertEqual(len(self.records()), 1)
        self.assertEqual(len(list(self.output.glob(".ingest-*.json"))), 1)

        main.process_staging(str(self.output), str(self.staging), 1)
        self.assertEqual(
            json.loads(seen_path.read_text(encoding="utf-8")),
            ["123", "older"],
        )
        self.assertEqual(len(self.records()), 1)
        self.assertFalse(list(self.output.glob(".ingest-*.json")))

    def test_interrupted_second_media_move_is_replayed_without_duplicates(self):
        self.stage(count=2)
        real_rename = os.rename
        moves = 0

        def stop_after_first(src, dst):
            nonlocal moves
            moves += 1
            if moves == 2:
                raise OSError("simulated process interruption")
            return real_rename(src, dst)

        with mock.patch.object(main.os, "rename", side_effect=stop_after_first):
            with self.assertRaises(OSError):
                main.process_staging(str(self.output), str(self.staging), 1)
        self.assertEqual(len(list(self.output.glob("20260927_123_*.jpg"))), 1)
        self.assertTrue(list(self.output.glob(".ingest-*.json")))

        main.process_staging(str(self.output), str(self.staging), 1)
        self.assertEqual(len(list(self.output.glob("20260927_123_*.jpg"))), 2)
        self.assertEqual(len(self.records()), 1)
        self.assertEqual(self.records()[0]["saved_files"], [
            "20260927_123_0_alice.jpg",
            "20260927_123_1_alice.jpg",
        ])
        self.assertFalse(list(self.output.glob(".ingest-*.json")))

    def test_metadata_committed_before_seen_is_reconciled_once(self):
        self.stage(count=1)
        real = main.save_seen
        called = 0

        def fail_seen(*args):
            nonlocal called
            called += 1
            if called == 1:
                raise OSError("power failure")
            return real(*args)

        with mock.patch.object(main, "save_seen", side_effect=fail_seen):
            with self.assertRaises(OSError):
                main.process_staging(str(self.output), str(self.staging), 1)
        self.assertEqual(len(self.records()), 1)
        self.assertEqual(len(list(self.output.glob(".ingest-*.json"))), 1)

        main.process_staging(str(self.output), str(self.staging), 1)
        main.process_staging(str(self.output), str(self.staging), 1)
        self.assertEqual(len(self.records()), 1)
        self.assertEqual(
            json.loads((self.output / "seen.json").read_text(encoding="utf-8")),
            ["123"],
        )


if __name__ == "__main__":
    unittest.main()
