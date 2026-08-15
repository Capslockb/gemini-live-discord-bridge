import importlib.util
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = REPO_ROOT / "bridge_config.py"
GOOGLE_HELPER_SUFFIX = Path(
    "hermes-agent/skills/productivity/google-workspace/scripts/google_api.py"
)


def _load_bridge_config(env, home):
    module_name = f"bridge_config_google_path_test_{id(env)}_{id(home)}"
    spec = importlib.util.spec_from_file_location(module_name, CONFIG_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load bridge_config.py")

    module = importlib.util.module_from_spec(spec)
    numpy_stub = types.ModuleType("numpy")
    user_profiles_stub = types.ModuleType("user_profiles")
    user_profiles_stub.register_known_tool = lambda *args, **kwargs: None

    with (
        patch.dict(os.environ, env, clear=True),
        patch.object(Path, "home", return_value=Path(home)),
        patch.dict(
            sys.modules,
            {"numpy": numpy_stub, "user_profiles": user_profiles_stub},
        ),
    ):
        spec.loader.exec_module(module)
    return module


class GoogleApiBinPathTests(unittest.TestCase):
    def test_default_uses_home_dot_hermes(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            module = _load_bridge_config({}, home)

            expected = home / ".hermes" / GOOGLE_HELPER_SUFFIX
            self.assertEqual(Path(module.GOOGLE_API_BIN), expected)
            self.assertEqual(module._SCRIPTS_DIR, expected.parent)

    def test_hermes_home_relocates_default_helper(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            hermes_home = Path(tmp) / "custom-hermes"
            module = _load_bridge_config(
                {"HERMES_HOME": str(hermes_home)},
                home,
            )

            expected = hermes_home / GOOGLE_HELPER_SUFFIX
            self.assertEqual(Path(module.GOOGLE_API_BIN), expected)

    def test_explicit_helper_path_has_highest_precedence(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            hermes_home = Path(tmp) / "custom-hermes"
            explicit = Path(tmp) / "workspace" / "google_api.py"
            module = _load_bridge_config(
                {
                    "HERMES_HOME": str(hermes_home),
                    "DISCORD_VOICE_LIVE_GOOGLE_API_BIN": str(explicit),
                },
                home,
            )

            self.assertEqual(Path(module.GOOGLE_API_BIN), explicit)
            self.assertEqual(module._SCRIPTS_DIR, explicit.parent)

    def test_missing_explicit_helper_does_not_fall_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            hermes_home = Path(tmp) / "custom-hermes"
            missing = Path(tmp) / "missing" / "google_api.py"
            module = _load_bridge_config(
                {
                    "HERMES_HOME": str(hermes_home),
                    "DISCORD_VOICE_LIVE_GOOGLE_API_BIN": str(missing),
                },
                home,
            )

            self.assertEqual(Path(module.GOOGLE_API_BIN), missing)
            self.assertFalse(Path(module.GOOGLE_API_BIN).exists())
            self.assertNotEqual(
                Path(module.GOOGLE_API_BIN),
                hermes_home / GOOGLE_HELPER_SUFFIX,
            )


if __name__ == "__main__":
    unittest.main()
