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
        "create_qwen_voice_clone_from_audio_base64",
        "create_qwen_voice_clone_from_local_file",
        "create_qwen_voice_clone_from_video_url_segment",
        "create_qwen_voice_clone_from_local_video_segment",
        "query_voice",
        "wait_for_voice_ready",
        "list_voices",
        "delete_voice",
        "synthesize_with_cloned_voice",
    ]
    missing = [name for name in tool_names if not callable(getattr(server, name, None))]
    if missing:
        raise SystemExit(f"Missing tools: {missing}")

    if not server._is_qwen_voice_id("qwen-tts-vc-demo_voice"):
        raise SystemExit("Expected qwen voice id detection to return True.")
    if server._resolve_synthesis_target_model(
        "qwen-tts-vc-demo_voice",
        server.DEFAULT_TARGET_MODEL,
    ) != server.DEFAULT_QWEN_VC_MODEL:
        raise SystemExit("Expected Qwen voice synthesis to auto-select the Qwen VC model.")
    try:
        server.query_voice("qwen-tts-vc-demo_voice")
    except ValueError as exc:
        if "synthesize_with_cloned_voice" not in str(exc):
            raise SystemExit(f"Unexpected Qwen guidance error: {exc}")
    else:
        raise SystemExit("Expected query_voice to reject Qwen voice ids before any API call.")

    print("app:", server.APP_NAME)
    print("default_model:", server.DEFAULT_TARGET_MODEL)
    print("default_qwen_model:", server.DEFAULT_QWEN_VC_MODEL)
    print("default_region:", server.DEFAULT_REGION)
    print("tools:", ", ".join(tool_names))


if __name__ == "__main__":
    main()
