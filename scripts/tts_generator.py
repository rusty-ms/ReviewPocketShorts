"""
tts_generator.py - Generate TTS voiceover using OpenAI TTS (primary).
Voice: alloy, echo, fable, onyx, nova, shimmer
nova = warm, natural female voice — perfect for review content.

Cost: ~$0.015 per 1,000 characters (~$0.01-0.02 per video)

edge-tts was removed — Microsoft's endpoint blocks server IPs with 403.
"""
import logging
import os
import subprocess
import config

logger = logging.getLogger(__name__)

# OpenAI TTS voice options:
# nova    = warm, natural female (default — best for reviews)
# shimmer = expressive female
# alloy   = neutral
# echo    = male, conversational
# fable   = expressive male
# onyx    = deep male
TTS_VOICE_MAP = {
    "en-US-JennyNeural": "nova",    # edge-tts compat alias
    "en-US-AriaNeural": "shimmer",
    "en-US-GuyNeural": "echo",
    "nova": "nova",
    "shimmer": "shimmer",
    "alloy": "alloy",
    "echo": "echo",
    "fable": "fable",
    "onyx": "onyx",
}


def _resolve_voice(voice_setting: str) -> str:
    """Map edge-tts voice names or OpenAI voice names to valid OpenAI voices."""
    return TTS_VOICE_MAP.get(voice_setting, "nova")


def generate_voiceover(script: str, output_path: str) -> str:
    """
    Generate a voiceover MP3 using OpenAI TTS.
    Returns path to the generated audio file.
    """
    os.makedirs(os.path.dirname(output_path) if os.path.dirname(output_path) else ".", exist_ok=True)

    voice = _resolve_voice(config.TTS_VOICE)

    if not config.OPENAI_API_KEY:
        raise EnvironmentError("OPENAI_API_KEY not set — cannot generate TTS")

    from openai import OpenAI
    client = OpenAI(api_key=config.OPENAI_API_KEY)

    logger.info(f"Generating TTS voiceover (voice={voice}, {len(script)} chars)...")

    response = client.audio.speech.create(
        model="tts-1",      # tts-1 = fast + cheap; tts-1-hd = higher quality
        voice=voice,
        input=script,
        response_format="mp3",
    )
    response.stream_to_file(output_path)

    if not os.path.exists(output_path) or os.path.getsize(output_path) < 1000:
        raise RuntimeError("TTS output file missing or empty")

    duration = _get_audio_duration(output_path)
    logger.info(f"TTS voiceover generated: {output_path} ({duration:.1f}s)")
    return output_path


def _get_audio_duration(audio_path: str) -> float:
    """Get duration of audio file in seconds using ffprobe."""
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", audio_path],
            capture_output=True, text=True, timeout=10
        )
        return float(result.stdout.strip())
    except Exception:
        return 0.0
