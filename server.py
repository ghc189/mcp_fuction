import contextlib
import base64
import json
import mimetypes
import os
import re
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any
from urllib import error, request

APP_ROOT = Path(__file__).resolve().parent
for extra_dir in ("pywin32_system32", "win32", str(Path("win32") / "lib")):
    candidate = APP_ROOT / extra_dir
    if candidate.exists():
        sys.path.insert(0, str(candidate))

from mcp.server.fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.middleware.cors import CORSMiddleware
from starlette.routing import Mount
import uvicorn


APP_NAME = "bailian-voice-clone-mcp"
DEFAULT_REGION = os.getenv("DASHSCOPE_REGION", "cn-beijing")
DEFAULT_TARGET_MODEL = os.getenv("BAILIAN_TTS_MODEL", "cosyvoice-v3.5-plus")
DEFAULT_QWEN_VC_MODEL = os.getenv("BAILIAN_QWEN_VC_MODEL", "qwen3-tts-vc-2026-01-22")
DEFAULT_INLINE_AUDIO_LIMIT = int(os.getenv("INLINE_AUDIO_BASE64_LIMIT", "300000"))
DEFAULT_TRANSPORT = os.getenv("MCP_TRANSPORT", "stdio").strip().lower()
DEFAULT_HTTP_HOST = os.getenv("MCP_HOST", "0.0.0.0").strip() or "0.0.0.0"
DEFAULT_HTTP_PORT = int(
    os.getenv("MCP_PORT")
    or os.getenv("PORT")
    or os.getenv("FC_SERVER_PORT")
    or "8080"
)

HTTP_ENDPOINTS = {
    "cn-beijing": "https://dashscope.aliyuncs.com/api/v1/services/audio/tts/customization",
    "intl-singapore": "https://dashscope-intl.aliyuncs.com/api/v1/services/audio/tts/customization",
}

WS_ENDPOINTS = {
    "cn-beijing": "wss://dashscope.aliyuncs.com/api-ws/v1/inference",
    "intl-singapore": "wss://dashscope-intl.aliyuncs.com/api-ws/v1/inference",
}

HTTP_BASE_ENDPOINTS = {
    "cn-beijing": "https://dashscope.aliyuncs.com/api/v1",
    "intl-singapore": "https://dashscope-intl.aliyuncs.com/api/v1",
}

READY_STATUSES = {"OK", "SUCCESS", "SUCCEEDED"}
FAILED_STATUSES = {"FAILED", "FAIL", "ERROR"}

mcp = FastMCP(
    APP_NAME,
    host=DEFAULT_HTTP_HOST,
    port=DEFAULT_HTTP_PORT,
    stateless_http=True,
    json_response=True,
    streamable_http_path="/mcp",
)


def _require_api_key() -> str:
    api_key = os.getenv("DASHSCOPE_API_KEY", "").strip()
    if not api_key:
        raise ValueError("缺少环境变量 DASHSCOPE_API_KEY。请先在 Function AI 或本地环境中配置。")
    return api_key


def _normalize_region(region: str | None) -> str:
    raw = (region or DEFAULT_REGION).strip().lower()
    aliases = {
        "cn": "cn-beijing",
        "beijing": "cn-beijing",
        "cn-beijing": "cn-beijing",
        "intl": "intl-singapore",
        "sg": "intl-singapore",
        "singapore": "intl-singapore",
        "intl-singapore": "intl-singapore",
    }
    normalized = aliases.get(raw)
    if not normalized:
        raise ValueError("region 只支持 cn-beijing 或 intl-singapore。")
    return normalized


def _http_endpoint(region: str | None) -> str:
    normalized = _normalize_region(region)
    return HTTP_ENDPOINTS[normalized]


def _ws_endpoint(region: str | None) -> str:
    normalized = _normalize_region(region)
    return WS_ENDPOINTS[normalized]


def _http_base_endpoint(region: str | None) -> str:
    normalized = _normalize_region(region)
    return HTTP_BASE_ENDPOINTS[normalized]


def _validate_prefix(prefix: str) -> str:
    value = prefix.strip()
    if not re.fullmatch(r"[a-z0-9_]{1,10}", value):
        raise ValueError("prefix 只允许小写字母、数字、下划线，长度 1 到 10。示例：myvoice01")
    return value


def _is_qwen_voice_id(voice_id: str) -> bool:
    return voice_id.strip().startswith("qwen-tts-vc-")


def _qwen_follow_up_error(tool_name: str) -> ValueError:
    return ValueError(
        f"{tool_name} only supports CosyVoice voice-enrollment voices created by create_voice_clone. "
        "For Qwen voice clones returned by create_qwen_voice_clone_* tools, call "
        "synthesize_with_cloned_voice directly with the returned voice_id and target_model. "
        "Do not call query_voice, wait_for_voice_ready, list_voices, or delete_voice."
    )


def _ensure_voice_enrollment_voice_id(voice_id: str, tool_name: str) -> str:
    clean_voice_id = voice_id.strip()
    if not clean_voice_id:
        raise ValueError("voice_id cannot be empty.")
    if _is_qwen_voice_id(clean_voice_id):
        raise _qwen_follow_up_error(tool_name)
    return clean_voice_id


def _resolve_synthesis_target_model(voice_id: str, target_model: str) -> str:
    clean_voice_id = voice_id.strip()
    requested_model = target_model.strip()

    if _is_qwen_voice_id(clean_voice_id):
        if not requested_model or requested_model == DEFAULT_TARGET_MODEL:
            return DEFAULT_QWEN_VC_MODEL
        if requested_model == DEFAULT_QWEN_VC_MODEL or requested_model.startswith("qwen3-tts-vc"):
            return requested_model
        raise ValueError(
            "Qwen voice_id requires a Qwen TTS VC target_model. "
            "Use the target_model returned by create_qwen_voice_clone_* or omit it and let the tool auto-select "
            f"{DEFAULT_QWEN_VC_MODEL}."
        )

    if requested_model.startswith("qwen3-tts-vc"):
        raise ValueError(
            "Non-Qwen voice_id cannot be synthesized with a Qwen TTS VC target_model. "
            "Use a voice_id returned by create_qwen_voice_clone_* or switch target_model back to the matching CosyVoice model."
        )

    return requested_model or DEFAULT_TARGET_MODEL


def _post_customization(payload: dict[str, Any], region: str | None) -> dict[str, Any]:
    api_key = _require_api_key()
    endpoint = _http_endpoint(region)
    body = json.dumps(payload).encode("utf-8")
    req = request.Request(
        endpoint,
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=120) as resp:
            content = resp.read().decode("utf-8")
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"DashScope HTTP {exc.code}: {detail}") from exc
    except error.URLError as exc:
        raise RuntimeError(f"请求 DashScope 失败: {exc.reason}") from exc

    try:
        data = json.loads(content)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"DashScope 返回了无法解析的 JSON: {content[:500]}") from exc

    if isinstance(data, dict) and data.get("code"):
        raise RuntimeError(f"DashScope 返回错误: {data.get('code')} - {data.get('message')}")
    return data


def _validate_preferred_name(preferred_name: str) -> str:
    value = preferred_name.strip()
    if not re.fullmatch(r"[A-Za-z0-9_]{1,16}", value):
        raise ValueError("preferred_name only allows letters, numbers, underscore, max length 16.")
    return value


def _guess_audio_mime_type(file_name: str, fallback: str = "audio/mpeg") -> str:
    guessed, _ = mimetypes.guess_type(file_name)
    return guessed or fallback


def _ensure_audio_data_url(audio_base64_or_data_url: str, audio_mime_type: str) -> str:
    value = audio_base64_or_data_url.strip()
    if not value:
        raise ValueError("audio_base64_or_data_url cannot be empty.")
    if value.startswith("data:"):
        return value
    compact = re.sub(r"\s+", "", value)
    return f"data:{audio_mime_type};base64,{compact}"


def _read_local_audio_as_data_url(local_file_path: str, audio_mime_type: str = "") -> tuple[str, str, int]:
    path = Path(local_file_path.strip())
    if not path.exists():
        raise FileNotFoundError(f"Audio file not found: {path}")
    payload = path.read_bytes()
    mime_type = audio_mime_type.strip() or _guess_audio_mime_type(path.name)
    data_url = f"data:{mime_type};base64,{base64.b64encode(payload).decode('ascii')}"
    return data_url, mime_type, len(payload)


def _create_qwen_voice(
    audio_data_url: str,
    preferred_name: str,
    target_model: str,
    region: str,
    text: str = "",
    language: str = "",
) -> dict[str, Any]:
    payload_input: dict[str, Any] = {
        "action": "create",
        "target_model": target_model,
        "preferred_name": _validate_preferred_name(preferred_name),
        "audio": {"data": audio_data_url},
    }
    if text.strip():
        payload_input["text"] = text.strip()
    if language.strip():
        payload_input["language"] = language.strip()

    payload = {
        "model": "qwen-voice-enrollment",
        "input": payload_input,
    }
    data = _post_customization(payload, region)
    output = data.get("output", {})
    voice_id = output.get("voice")
    resolved_target_model = output.get("target_model", target_model)
    return {
        "message": "Qwen voice clone created. Use synthesize_with_cloned_voice directly; do not call query_voice or wait_for_voice_ready.",
        "voice": voice_id,
        "voice_id": voice_id,
        "ready": True,
        "target_model": resolved_target_model,
        "request_id": data.get("request_id"),
        "usage": data.get("usage", {}),
        "region": _normalize_region(region),
        "recommended_next_tool": "synthesize_with_cloned_voice",
        "recommended_next_args": {
            "voice_id": voice_id,
            "target_model": resolved_target_model,
        },
        "do_not_call": [
            "query_voice",
            "wait_for_voice_ready",
            "list_voices",
            "delete_voice",
        ],
        "raw_output": output,
    }


def _extract_voice_status(payload: dict[str, Any]) -> str | None:
    output = payload.get("output", {})
    if isinstance(output, dict):
        if output.get("status"):
            return str(output.get("status"))
        voice_list = output.get("voice_list")
        if isinstance(voice_list, list) and voice_list:
            status = voice_list[0].get("status")
            if status:
                return str(status)
    return None


def _configure_dashscope(region: str | None) -> None:
    import dashscope

    dashscope.api_key = _require_api_key()
    dashscope.base_websocket_api_url = _ws_endpoint(region)


def _default_output_path(voice_id: str, suffix: str) -> str:
    safe_voice = re.sub(r"[^A-Za-z0-9_.-]+", "_", voice_id)
    file_name = f"{safe_voice}-{uuid.uuid4().hex[:8]}{suffix}"
    return str(Path(tempfile.gettempdir()) / file_name)


def _parse_time_to_seconds(value: str) -> float:
    raw = value.strip()
    if not raw:
        raise ValueError("time value cannot be empty.")
    if re.fullmatch(r"\d+(\.\d+)?", raw):
        return float(raw)

    parts = raw.split(":")
    if not 1 <= len(parts) <= 3:
        raise ValueError("time format must be seconds or HH:MM:SS[.ms].")
    try:
        parts = [part.strip() for part in parts]
        if len(parts) == 3:
            hours = float(parts[0])
            minutes = float(parts[1])
            seconds = float(parts[2])
        elif len(parts) == 2:
            hours = 0.0
            minutes = float(parts[0])
            seconds = float(parts[1])
        else:
            hours = 0.0
            minutes = 0.0
            seconds = float(parts[0])
    except ValueError as exc:
        raise ValueError("time format must be seconds or HH:MM:SS[.ms].") from exc

    total = hours * 3600 + minutes * 60 + seconds
    if total < 0:
        raise ValueError("time must be >= 0.")
    return total


def _normalize_time_range(start_time: str, end_time: str) -> tuple[float, float]:
    start_seconds = _parse_time_to_seconds(start_time)
    end_seconds = _parse_time_to_seconds(end_time)
    if end_seconds <= start_seconds:
        raise ValueError("end_time must be greater than start_time.")
    return start_seconds, end_seconds


def _video_source_to_ffmpeg_input(source: str, source_kind: str) -> str:
    clean = source.strip()
    if not clean:
        raise ValueError("video source cannot be empty.")
    if source_kind == "url":
        return clean
    if source_kind == "local_file":
        path = Path(clean).expanduser()
        if not path.exists():
            raise FileNotFoundError(f"Video file not found: {path}")
        return str(path.resolve())
    raise ValueError("source_kind must be 'url' or 'local_file'.")


def _speech_enhancement_filter(speech_enhancement: bool) -> str:
    if not speech_enhancement:
        return "aresample=16000"
    return ",".join(
        [
            "highpass=f=80",
            "lowpass=f=7000",
            "afftdn=nf=-25",
            "speechnorm=e=6.25:r=0.0001:l=1",
            "aresample=16000",
        ]
    )


def _extract_audio_segment_from_video(
    source: str,
    source_kind: str,
    start_time: str,
    end_time: str,
    speech_enhancement: bool,
) -> dict[str, Any]:
    import imageio_ffmpeg

    start_seconds, end_seconds = _normalize_time_range(start_time, end_time)
    ffmpeg_input = _video_source_to_ffmpeg_input(source, source_kind)
    output_path = Path(tempfile.gettempdir()) / f"voice-segment-{uuid.uuid4().hex[:8]}.wav"
    ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
    filter_chain = _speech_enhancement_filter(speech_enhancement)

    cmd = [
        ffmpeg_exe,
        "-y",
        "-ss",
        f"{start_seconds:.3f}",
        "-to",
        f"{end_seconds:.3f}",
        "-i",
        ffmpeg_input,
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-sample_fmt",
        "s16",
        "-af",
        filter_chain,
        str(output_path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0 or not output_path.exists():
        detail = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(f"ffmpeg extract failed: {detail[-2000:]}")

    payload = output_path.read_bytes()
    return {
        "audio_bytes": len(payload),
        "audio_mime_type": "audio/wav",
        "audio_data_url": f"data:audio/wav;base64,{base64.b64encode(payload).decode('ascii')}",
        "saved_path": str(output_path),
        "start_seconds": round(start_seconds, 3),
        "end_seconds": round(end_seconds, 3),
        "duration_seconds": round(end_seconds - start_seconds, 3),
        "speech_enhancement": speech_enhancement,
        "source_kind": source_kind,
        "source": source.strip(),
    }


@mcp.tool()
def create_voice_clone(
    audio_url: str,
    prefix: str,
    language_hint: str = "zh",
    target_model: str = DEFAULT_TARGET_MODEL,
    region: str = DEFAULT_REGION,
) -> dict[str, Any]:
    """
    通过公网可访问的音频 URL 创建声音克隆。

    说明:
    - 适用于 cosyvoice-v3.5-plus / cosyvoice-v3.5-flash。
    - 音频 URL 必须公网可访问。
    - prefix 建议用角色名或业务名，便于后续筛选。
    """
    payload = {
        "model": "voice-enrollment",
        "input": {
            "action": "create_voice",
            "target_model": target_model,
            "prefix": _validate_prefix(prefix),
            "url": audio_url.strip(),
            "language_hints": [language_hint.strip()],
        },
    }
    data = _post_customization(payload, region)
    output = data.get("output", {})
    return {
        "message": "音色创建请求已提交。声音克隆是异步任务，请继续调用 query_voice 或 wait_for_voice_ready。",
        "voice_id": output.get("voice_id"),
        "status": output.get("status"),
        "ready": False,
        "target_model": target_model,
        "request_id": data.get("request_id"),
        "region": _normalize_region(region),
        "recommended_next_tool": "wait_for_voice_ready",
        "recommended_next_args": {
            "voice_id": output.get("voice_id"),
            "target_model": target_model,
        },
        "raw_output": output,
    }


@mcp.tool()
def create_qwen_voice_clone_from_audio_base64(
    audio_base64_or_data_url: str,
    preferred_name: str,
    audio_mime_type: str = "audio/mpeg",
    target_model: str = DEFAULT_QWEN_VC_MODEL,
    region: str = DEFAULT_REGION,
    text: str = "",
    language: str = "",
) -> dict[str, Any]:
    """
    Create a Qwen voice clone from base64 or a full Data URL.

    This is the remote-friendly option for Bailian / Function AI, because the
    caller can pass audio content directly without a public URL.

    Follow-up:
    - After success, call synthesize_with_cloned_voice directly with the returned
      voice_id.
    - Do not call query_voice / wait_for_voice_ready / list_voices for Qwen
      voice clones.
    """
    data_url = _ensure_audio_data_url(
        audio_base64_or_data_url=audio_base64_or_data_url,
        audio_mime_type=audio_mime_type.strip() or "audio/mpeg",
    )
    result = _create_qwen_voice(
        audio_data_url=data_url,
        preferred_name=preferred_name,
        target_model=target_model,
        region=region,
        text=text,
        language=language,
    )
    result["audio_input_mode"] = "base64_or_data_url"
    result["audio_mime_type"] = audio_mime_type.strip() or "audio/mpeg"
    return result


@mcp.tool()
def create_qwen_voice_clone_from_local_file(
    local_file_path: str,
    preferred_name: str,
    audio_mime_type: str = "",
    target_model: str = DEFAULT_QWEN_VC_MODEL,
    region: str = DEFAULT_REGION,
    text: str = "",
    language: str = "",
) -> dict[str, Any]:
    """
    Create a Qwen voice clone from a local audio file path.

    Note:
    - Best for local stdio deployment.
    - In Function AI, the path is resolved inside the cloud container, not on
      your personal computer.
    - After success, call synthesize_with_cloned_voice directly with the returned
      voice_id.
    """
    data_url, resolved_mime_type, audio_bytes = _read_local_audio_as_data_url(
        local_file_path=local_file_path,
        audio_mime_type=audio_mime_type,
    )
    result = _create_qwen_voice(
        audio_data_url=data_url,
        preferred_name=preferred_name,
        target_model=target_model,
        region=region,
        text=text,
        language=language,
    )
    result["audio_input_mode"] = "local_file"
    result["audio_mime_type"] = resolved_mime_type
    result["audio_bytes"] = audio_bytes
    result["source_path"] = str(Path(local_file_path).expanduser().resolve())
    return result


@mcp.tool()
def create_qwen_voice_clone_from_video_url_segment(
    video_url: str,
    preferred_name: str,
    start_time: str,
    end_time: str,
    speech_enhancement: bool = True,
    target_model: str = DEFAULT_QWEN_VC_MODEL,
    region: str = DEFAULT_REGION,
    text: str = "",
    language: str = "",
) -> dict[str, Any]:
    """
    Create a Qwen voice clone from a public video URL and a selected time range.

    This is the recommended remote-friendly workflow when the voice you want is
    inside a video.

    Follow-up:
    - After success, call synthesize_with_cloned_voice directly with the returned
      voice_id.
    - Do not call query_voice / wait_for_voice_ready / list_voices for Qwen
      voice clones.
    """
    extracted = _extract_audio_segment_from_video(
        source=video_url,
        source_kind="url",
        start_time=start_time,
        end_time=end_time,
        speech_enhancement=speech_enhancement,
    )
    result = _create_qwen_voice(
        audio_data_url=extracted["audio_data_url"],
        preferred_name=preferred_name,
        target_model=target_model,
        region=region,
        text=text,
        language=language,
    )
    result["audio_input_mode"] = "video_url_segment"
    result["segment"] = extracted
    return result


@mcp.tool()
def create_qwen_voice_clone_from_local_video_segment(
    local_video_path: str,
    preferred_name: str,
    start_time: str,
    end_time: str,
    speech_enhancement: bool = True,
    target_model: str = DEFAULT_QWEN_VC_MODEL,
    region: str = DEFAULT_REGION,
    text: str = "",
    language: str = "",
) -> dict[str, Any]:
    """
    Create a Qwen voice clone from a local video file and a selected time range.

    Note:
    - Best for local stdio deployment.
    - In Function AI, the path is resolved inside the cloud container, not on
      your personal computer.
    - After success, call synthesize_with_cloned_voice directly with the returned
      voice_id.
    """
    extracted = _extract_audio_segment_from_video(
        source=local_video_path,
        source_kind="local_file",
        start_time=start_time,
        end_time=end_time,
        speech_enhancement=speech_enhancement,
    )
    result = _create_qwen_voice(
        audio_data_url=extracted["audio_data_url"],
        preferred_name=preferred_name,
        target_model=target_model,
        region=region,
        text=text,
        language=language,
    )
    result["audio_input_mode"] = "local_video_segment"
    result["segment"] = extracted
    return result


@mcp.tool()
def query_voice(
    voice_id: str,
    region: str = DEFAULT_REGION,
) -> dict[str, Any]:
    """
    查询单个音色的状态和元数据。
    """
    clean_voice_id = _ensure_voice_enrollment_voice_id(voice_id, "query_voice")
    payload = {
        "model": "voice-enrollment",
        "input": {
            "action": "query_voice",
            "voice_id": clean_voice_id,
        },
    }
    data = _post_customization(payload, region)
    output = data.get("output", {})
    return {
        "voice_id": output.get("voice_id", clean_voice_id),
        "status": output.get("status"),
        "target_model": output.get("target_model"),
        "gmt_create": output.get("gmt_create"),
        "gmt_modified": output.get("gmt_modified"),
        "resource_link": output.get("resource_link"),
        "region": _normalize_region(region),
        "request_id": data.get("request_id"),
        "raw_output": output,
    }


@mcp.tool()
def wait_for_voice_ready(
    voice_id: str,
    timeout_seconds: int = 180,
    poll_interval_seconds: int = 5,
    region: str = DEFAULT_REGION,
) -> dict[str, Any]:
    """
    轮询音色状态，直到完成或超时。
    """
    clean_voice_id = _ensure_voice_enrollment_voice_id(voice_id, "wait_for_voice_ready")
    timeout = max(10, timeout_seconds)
    interval = max(1, poll_interval_seconds)
    started = time.time()
    last_result: dict[str, Any] | None = None

    while time.time() - started <= timeout:
        result = query_voice(voice_id=clean_voice_id, region=region)
        last_result = result
        status = str(result.get("status") or "").upper()
        if status in READY_STATUSES:
            result["ready"] = True
            result["waited_seconds"] = round(time.time() - started, 2)
            return result
        if status in FAILED_STATUSES:
            result["ready"] = False
            result["waited_seconds"] = round(time.time() - started, 2)
            return result
        time.sleep(interval)

    return {
        "voice_id": clean_voice_id,
        "ready": False,
        "status": (last_result or {}).get("status"),
        "waited_seconds": round(time.time() - started, 2),
        "message": "等待超时。请稍后继续调用 query_voice 查看最终状态。",
        "last_result": last_result,
    }


@mcp.tool()
def list_voices(
    prefix: str = "",
    page_index: int = 0,
    page_size: int = 10,
    region: str = DEFAULT_REGION,
) -> dict[str, Any]:
    """
    按 prefix 分页列出已创建的音色。
    """
    payload_input: dict[str, Any] = {
        "action": "list_voice",
        "page_index": max(0, page_index),
        "page_size": max(1, min(page_size, 1000)),
    }
    if prefix.strip():
        payload_input["prefix"] = _validate_prefix(prefix)

    payload = {
        "model": "voice-enrollment",
        "input": payload_input,
    }
    data = _post_customization(payload, region)
    output = data.get("output", {})
    return {
        "voice_list": output.get("voice_list", []),
        "count": (data.get("usage") or {}).get("count"),
        "request_id": data.get("request_id"),
        "region": _normalize_region(region),
        "raw_output": output,
    }


@mcp.tool()
def delete_voice(
    voice_id: str,
    region: str = DEFAULT_REGION,
) -> dict[str, Any]:
    """
    删除一个不再需要的音色。
    """
    clean_voice_id = _ensure_voice_enrollment_voice_id(voice_id, "delete_voice")
    payload = {
        "model": "voice-enrollment",
        "input": {
            "action": "delete_voice",
            "voice_id": clean_voice_id,
        },
    }
    data = _post_customization(payload, region)
    return {
        "message": "删除请求已提交。",
        "voice_id": clean_voice_id,
        "request_id": data.get("request_id"),
        "region": _normalize_region(region),
        "raw_output": data.get("output", {}),
    }


@mcp.tool()
def synthesize_with_cloned_voice(
    text: str,
    voice_id: str,
    target_model: str = DEFAULT_TARGET_MODEL,
    region: str = DEFAULT_REGION,
    save_path: str = "",
    inline_base64: bool = False,
) -> dict[str, Any]:
    """
    使用已复刻成功的 voice_id 进行语音合成。

    说明:
    - `voice_id` 必须来自同一 target_model。
    - 默认会把音频保存到系统临时目录。
    - 如果 inline_base64=true，或音频较小，会返回 base64 方便调试或二次上传。
    """
    clean_text = text.strip()
    if not clean_text:
        raise ValueError("text 不能为空。")

    clean_voice_id = voice_id.strip()
    if not clean_voice_id:
        raise ValueError("voice_id cannot be empty.")
    resolved_target_model = _resolve_synthesis_target_model(clean_voice_id, target_model)

    _configure_dashscope(region)

    from dashscope.audio.tts_v2 import SpeechSynthesizer

    synthesizer = SpeechSynthesizer(model=resolved_target_model, voice=clean_voice_id)
    audio = synthesizer.call(clean_text)
    if not isinstance(audio, (bytes, bytearray)):
        raise RuntimeError("语音合成返回了空音频。")

    output_path = save_path.strip() or _default_output_path(clean_voice_id, ".mp3")
    output_file = Path(output_path)
    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_bytes(audio)

    result: dict[str, Any] = {
        "voice_id": clean_voice_id,
        "target_model": resolved_target_model,
        "region": _normalize_region(region),
        "saved_path": str(output_file),
        "audio_bytes": len(audio),
        "request_id": synthesizer.get_last_request_id(),
        "first_package_delay_ms": synthesizer.get_first_package_delay(),
        "content_type": "audio/mpeg",
    }
    if inline_base64 or len(audio) <= DEFAULT_INLINE_AUDIO_LIMIT:
        result["audio_base64"] = base64.b64encode(audio).decode("ascii")
    else:
        result["audio_base64_omitted"] = True
        result["inline_limit_bytes"] = DEFAULT_INLINE_AUDIO_LIMIT
    return result


@contextlib.asynccontextmanager
async def app_lifespan(_: Starlette):
    async with mcp.session_manager.run():
        yield


http_app = CORSMiddleware(
    Starlette(
        routes=[Mount("/", app=mcp.streamable_http_app())],
        lifespan=app_lifespan,
    ),
    allow_origins=["*"],
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)


if __name__ == "__main__":
    if DEFAULT_TRANSPORT == "streamable-http":
        uvicorn.run(http_app, host=DEFAULT_HTTP_HOST, port=DEFAULT_HTTP_PORT)
    else:
        mcp.run(transport="stdio")
