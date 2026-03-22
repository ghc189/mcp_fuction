import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DEPS = ROOT / ".deps"
if DEPS.exists():
    sys.path.insert(0, str(DEPS))
for extra_dir in ("pywin32_system32", "win32", str(Path("win32") / "lib")):
    candidate = DEPS / extra_dir
    if candidate.exists():
        sys.path.insert(0, str(candidate))

import server  # noqa: E402


def main() -> None:
    tool_names = [
        "create_voice_clone",
        "query_voice",
        "wait_for_voice_ready",
        "list_voices",
        "delete_voice",
        "synthesize_with_cloned_voice",
    ]
    missing = [name for name in tool_names if not callable(getattr(server, name, None))]
    if missing:
        raise SystemExit(f"Missing tools: {missing}")

    print("app:", server.APP_NAME)
    print("default_model:", server.DEFAULT_TARGET_MODEL)
    print("default_region:", server.DEFAULT_REGION)
    print("tools:", ", ".join(tool_names))


if __name__ == "__main__":
    main()
