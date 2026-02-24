"""
tts_generator.py - Generate TTS voiceover using edge-tts (Microsoft, FREE).
Falls back to OpenAI TTS if edge-tts fails.

edge-tts cost: FREE (uses Microsoft Edge TTS unofficially)
OpenAI TTS fallback cost: ~$0.015 per 1000 chars
"""
import asyncio
import logging
import os
import subprocess
import config

logger = logging.getLogger(__name__)


async def _generate_edge_tts(text: str, output_path: str, voice: str):
    """Generate audio using edge-tts (free)."""
    import edge_tts
    communicate = edge_tts.Communicate(text, voice)
    await communicate.save(output_path)


def generate_voiceover(script: str, output_path: str) -> str:
    """
    Generate a voiceover MP3 from script text.
    Tries edge-tts first (free), falls back to OpenAI TTS.
    Returns path to the generated audio file.
    """
    os.makedirs(os.path.dirname(output_path) if os.path.dirname(output_path) else ".", exist_ok=True)
    voice = config.TTS_VOICE

    # Try edge-tts (free)
    try:
        asyncio.run(_generate_edge_tts(script, output_path, voice))
        if os.path.exists(output_path) and os.path.getsize(output_path) > 1000:
            duration = _get_audio_duration(output_path)
            logger.info(f"edge-tts voiceover generated: {output_path} ({duration:.1f}s)")
            return output_path
    except Exception as e:
        logger.warning(f"edge-tts failed: {e} — trying OpenAI TTS fallback")

    # Fallback: OpenAI TTS
    try:
        from openai import OpenAI
        client = OpenAI(api_key=config.OPENAI_API_KEY)
        response = client.audio.speech.create(
            model="tts-1",
            voice="nova",  # Warm female voice
            input=script,
        )
        response.stream_to_file(output_path)
        duration = _get_audio_duration(output_path)
        logger.info(f"OpenAI TTS voiceover generated: {output_path} ({duration:.1f}s)")
        return output_path
    except Exception as e:
        logger.error(f"Both TTS methods failed: {e}")
        raise


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
