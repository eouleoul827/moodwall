"""Offline safety checks for the local bootstrap; never uses repository .env."""

import contextlib
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import setup_gemini as setup


FAKE_KEY = "AQ.not-a-real-provider-key-0123456789"
EXISTING = (
    "# Keep this comment\r\n"
    "POSTGRES_PASSWORD=existing-owner\r\n"
    "APP_DB_PASSWORD=existing-runtime\r\n"
    "MOODWALL_SECRET_KEY=existing-session-secret\r\n"
    "MOODWALL_QR_KEY=existing-qr-secret\r\n"
    "MOODWALL_ANALYZER=fake  # Keep this too\r\n"
    "OTHER_TOKEN='unchanged$literal#secret'\r\n"
    "MULTILINE='first\r\nMOODWALL_ANALYZER=inside-literal\r\nlast'\r\n"
)


class SetupTests(unittest.TestCase):
    def test_preserves_unrelated_secrets_comments_and_multiline(self):
        updated, changed = setup.update_env(EXISTING, FAKE_KEY)
        self.assertIn("POSTGRES_PASSWORD=existing-owner\r\n", updated)
        self.assertIn("APP_DB_PASSWORD=existing-runtime\r\n", updated)
        self.assertIn("OTHER_TOKEN='unchanged$literal#secret'\r\n", updated)
        self.assertIn("MOODWALL_ANALYZER=inside-literal\r\n", updated)
        self.assertIn("MOODWALL_ANALYZER=gemini  # Keep this too\r\n", updated)
        self.assertIn("postgresql://moodwall_app:existing-runtime@localhost", updated)
        self.assertIn(setup.KEY_NAME, changed)
        again, second = setup.update_env(updated, FAKE_KEY)
        self.assertEqual(again, updated)
        self.assertEqual(second, [])

    def test_literal_key_uses_runtime_token_alphabet(self):
        key = "synthetic_valid-KEY_123456789"
        updated, _ = setup.update_env("", key)
        parsed = setup.entries(updated)
        self.assertEqual(setup._literal_value(parsed[setup.KEY_NAME][3]), key)
        self.assertIn(f"{setup.KEY_NAME}='{key}'", updated)
        for key in ("synthetic$HOME#literal_12345", "A" * 19, "A" * 257, "é" * 20, "synthetic-valid-token!12345"):
            with self.assertRaises(setup.SetupError):
                setup.validate_key(key)
        setup.validate_key("A" * 20)
        setup.validate_key("A" * 256)

    def test_auth_key_dotenv_round_trip_does_not_expose_key(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {}, clear=True):
            destination = Path(folder) / "synthetic.env"
            destination.write_text(f"{setup.KEY_NAME}='{FAKE_KEY}'\n", encoding="utf-8")
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout), patch.object(setup.getpass, "getpass", side_effect=AssertionError("must reuse saved key")):
                self.assertEqual(setup.main(["--env-file", str(destination)]), 0)
            found = setup.entries(destination.read_text(encoding="utf-8"))
            self.assertEqual(setup._literal_value(found[setup.KEY_NAME][3]), FAKE_KEY)
            self.assertNotIn(FAKE_KEY, stdout.getvalue())

    def test_injection_invalid_keys_are_rejected(self):
        for key in ("short", "A" * 16 + "\nINJECT=yes", "A" * 16 + "\r", "A" * 16 + "'", "A" * 16 + '"', "A" * 16 + "`", "A" * 16 + "\x00"):
            with self.subTest(key_length=len(key)), self.assertRaises(setup.SetupError):
                setup.update_env("", key)

    def test_duplicate_and_unclosed_dotenv_are_rejected(self):
        for original in ("MODE=a\nMODE=b\n", "OTHER='unclosed\n"):
            with self.assertRaises(setup.SetupError):
                setup.update_env(original, FAKE_KEY)

    def test_never_generates_replacement_for_partial_existing_db(self):
        for text in ("APP_DB_PASSWORD=keep\n", "MOODWALL_DATABASE_URL=postgresql://keep\n"):
            with self.assertRaises(setup.SetupError):
                setup.update_env(text, FAKE_KEY)

    def test_fresh_secrets_are_random_and_different(self):
        updated, _ = setup.update_env("", FAKE_KEY)
        found = setup.entries(updated)
        self.assertNotEqual(found["MOODWALL_SECRET_KEY"][3], found["MOODWALL_QR_KEY"][3])
        self.assertNotIn("change-", updated)

    def test_dry_run_never_prompts_writes_or_starts(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {}, clear=True):
            destination = Path(folder) / ".env"
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout), patch.object(setup.getpass, "getpass", side_effect=AssertionError("must not prompt")), patch.object(setup, "start_stack", side_effect=AssertionError("must not start")):
                self.assertEqual(setup.main(["--env-file", str(destination), "--dry-run"]), 0)
            self.assertFalse(destination.exists())
            self.assertIn("MOODWALL_GEMINI_API_KEY=[REDACTED]", stdout.getvalue())

    def test_bom_newlines_permissions_and_secret_redaction(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {setup.KEY_NAME: FAKE_KEY}, clear=True):
            destination = Path(folder) / ".env"
            destination.write_bytes(b"\xef\xbb\xbf" + EXISTING.encode("utf-8"))
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                self.assertEqual(setup.main(["--env-file", str(destination)]), 0)
            value = destination.read_bytes()
            self.assertTrue(value.startswith(b"\xef\xbb\xbf"))
            self.assertNotIn(b"\n", value.replace(b"\r\n", b""))
            self.assertIn(FAKE_KEY.encode(), value)
            self.assertNotIn(FAKE_KEY, stdout.getvalue())
            self.assertEqual(list(Path(folder).glob(".moodwall-env-*")), [])
            if os.name != "nt":
                self.assertEqual(destination.stat().st_mode & 0o777, 0o600)

    def test_existing_key_is_reused_without_prompt(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {}, clear=True):
            destination = Path(folder) / ".env"
            value, _ = setup.update_env("", FAKE_KEY)
            destination.write_text(value, encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()), patch.object(setup.getpass, "getpass", side_effect=AssertionError("must not prompt")):
                self.assertEqual(setup.main(["--env-file", str(destination)]), 0)

    def test_invalid_key_preserves_existing_file_and_never_prints_key(self):
        invalid = "sensitive-invalid-key'123456789"
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {setup.KEY_NAME: invalid}, clear=True):
            destination = Path(folder) / ".env"
            destination.write_bytes(EXISTING.encode("utf-8"))
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                self.assertEqual(setup.main(["--env-file", str(destination)]), 1)
            self.assertEqual(destination.read_bytes(), EXISTING.encode("utf-8"))
            self.assertNotIn(invalid, stderr.getvalue())

    def test_start_is_explicit_and_receives_key_without_command_argument(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {setup.KEY_NAME: FAKE_KEY}, clear=True):
            destination = Path(folder) / ".env"
            with contextlib.redirect_stdout(io.StringIO()), patch.object(setup, "start_stack") as start:
                self.assertEqual(setup.main(["--env-file", str(destination)]), 0)
                start.assert_not_called()
                self.assertEqual(setup.main(["--env-file", str(destination), "--start"]), 0)
                start.assert_called_once_with(destination.absolute(), FAKE_KEY, create_operator=False, operator_name="admin", emotion_bundle=None)

    def test_frontend_build_never_receives_provider_credentials(self):
        with patch.dict(os.environ, {"GEMINI_API_KEY": FAKE_KEY, "OTHER_COPY": FAKE_KEY, "GOOGLE_API_KEY": "another-key"}, clear=True), patch.object(setup.shutil, "which", side_effect=lambda name: name), patch.object(setup, "run_command") as command:
            with contextlib.redirect_stdout(io.StringIO()):
                setup.start_stack(Path("synthetic.env"), FAKE_KEY, create_operator=False, operator_name="admin")
            for call in command.call_args_list:
                argv = call.args[0]
                self.assertNotIn(FAKE_KEY, argv)
                env = call.kwargs["env"]
                if "frontend" in argv:
                    self.assertNotIn(setup.KEY_NAME, env)
                    self.assertNotIn("GEMINI_API_KEY", env)
                    self.assertNotIn("GOOGLE_API_KEY", env)
                    self.assertNotIn(FAKE_KEY, env.values())

    def test_no_insecure_getpass_fallback(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {}, clear=True), patch.object(setup.getpass, "getpass", side_effect=setup.getpass.GetPassWarning("no private input")):
            destination = Path(folder) / ".env"
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(setup.main(["--env-file", str(destination)]), 1)
            self.assertFalse(destination.exists())

    def test_emotion_bundle_is_explicit_persisted_and_reused(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {setup.KEY_NAME: FAKE_KEY}, clear=True):
            bundle = Path(folder) / "emotion bundle"
            bundle.mkdir()
            (bundle / "manifest.json").write_text("{}", encoding="utf-8")
            destination = Path(folder) / "synthetic.env"
            with contextlib.redirect_stdout(io.StringIO()), patch.object(setup, "start_stack") as start:
                self.assertEqual(setup.main(["--env-file", str(destination), "--emotion-model", str(bundle)]), 0)
                start.assert_not_called()
                self.assertEqual(setup.main(["--env-file", str(destination), "--start"]), 0)
            start.assert_called_once_with(destination.absolute(), FAKE_KEY, create_operator=False, operator_name="admin", emotion_bundle=bundle.resolve())
            found = setup.entries(destination.read_text(encoding="utf-8"))
            self.assertEqual(setup._literal_value(found[setup.EMOTION_DIR_NAME][3]), bundle.resolve().as_posix())

    def test_invalid_emotion_bundle_cannot_write_or_start(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {setup.KEY_NAME: FAKE_KEY}, clear=True):
            destination = Path(folder) / "synthetic.env"
            destination.write_bytes(EXISTING.encode("utf-8"))
            with contextlib.redirect_stderr(io.StringIO()), patch.object(setup, "start_stack") as start:
                self.assertEqual(setup.main(["--env-file", str(destination), "--emotion-model", str(Path(folder) / "missing"), "--start"]), 1)
                start.assert_not_called()
            self.assertEqual(destination.read_bytes(), EXISTING.encode("utf-8"))
            for value in ("$HOME/model", "model\nother", "model'quoted"):
                with self.assertRaises(setup.SetupError):
                    setup.emotion_bundle_path(value)

    def test_emotion_override_only_reaches_selected_stack(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(setup.shutil, "which", side_effect=lambda name: name), patch.object(setup, "run_command") as command:
            with contextlib.redirect_stdout(io.StringIO()):
                setup.start_stack(Path("synthetic.env"), FAKE_KEY, create_operator=False, operator_name="admin", emotion_bundle=Path("local-model"))
            for call in command.call_args_list:
                argv = call.args[0]
                if "compose" in argv:
                    self.assertIn("compose.gemini-emotion.yaml", argv)
                    self.assertEqual(call.kwargs["env"][setup.EMOTION_DIR_NAME], "local-model")
                    self.assertNotIn(FAKE_KEY, argv)

    def test_production_file_or_environment_is_never_modified(self):
        for original, environment in (
            (EXISTING + "MOODWALL_ENVIRONMENT=production\r\n", {}),
            (EXISTING, {"MOODWALL_ENVIRONMENT": "production"}),
        ):
            with self.subTest(process_environment=bool(environment)), tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {setup.KEY_NAME: FAKE_KEY, **environment}, clear=True):
                destination = Path(folder) / ".env"
                destination.write_bytes(original.encode("utf-8"))
                stderr = io.StringIO()
                with contextlib.redirect_stderr(stderr), patch.object(setup, "start_stack") as start:
                    self.assertEqual(setup.main(["--env-file", str(destination), "--start"]), 1)
                self.assertEqual(destination.read_bytes(), original.encode("utf-8"))
                self.assertNotIn(FAKE_KEY, stderr.getvalue())
                self.assertIn("local-only", stderr.getvalue())
                start.assert_not_called()


if __name__ == "__main__":
    unittest.main()
