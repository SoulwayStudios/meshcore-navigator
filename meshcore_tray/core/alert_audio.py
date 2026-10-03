"""Audio alert playback service for MeshCore Navigator.

Provides tactical proximity audio alerts for ADS-B tracking with support for:
1. Built-in tactical radar chirp alert tone (synthesized PCM WAV).
2. User-configured custom audio files (.wav, .mp3, .ogg, etc.) with robust Windows path normalization.
3. Native Windows waveform audio playback via winsound fallback and Qt6 Multimedia (QSoundEffect / QMediaPlayer).
"""

import io
import logging
import math
import os
from pathlib import Path
import struct
import sys
from typing import Optional, Union

from PyQt6.QtCore import QCoreApplication, QObject, QUrl
try:
    from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer, QSoundEffect
    QT_MULTIMEDIA_AVAILABLE = True
except ImportError:
    QT_MULTIMEDIA_AVAILABLE = False

logger = logging.getLogger("meshcore_tray.alert_audio")

DEFAULT_SOUNDS_DIR = Path.home() / ".cache" / "meshcore-pixoo-tray" / "sounds"


def normalize_sound_path(path_str: Union[str, Path, None]) -> Optional[Path]:
    """Sanitizes, strips quotes, and normalizes a file path across Windows and POSIX systems.

    Handles paths copied from Windows Explorer (e.g., wrapped in quotes or using mixed slashes)
    and verifies that the target file exists.
    """
    if not path_str:
        return None
    cleaned = str(path_str).strip().strip('"').strip("'").strip()
    if not cleaned:
        return None
    try:
        # Resolve path (handles ~ expansion and Windows drive letters)
        p = Path(cleaned).expanduser().resolve()
        if p.exists() and p.is_file():
            return p
    except Exception as e:
        logger.debug(f"Failed to normalize sound path '{path_str}': {e}")
    return None


def generate_tactical_alert_wav(target_path: Path) -> Path:
    """Synthesizes a distinct, high-tech tactical radar alert tone in uncompressed 44.1kHz 16-bit PCM WAV.

    Generates three sharp rising frequency pulses (880Hz -> 1320Hz -> 1760Hz)
    with exponential decay envelopes for tactical military/aviation radar proximity warning.
    """
    import wave

    target_path.parent.mkdir(parents=True, exist_ok=True)
    sample_rate = 44100
    tones = [
        (880, 0.07, 0.05),   # A5 pulse (70ms)
        (1320, 0.07, 0.05),  # E6 pulse (70ms)
        (1760, 0.11, 0.08),  # A6 pulse (110ms with smooth fade)
    ]
    samples = []
    for freq, duration, decay in tones:
        n_samples = int(sample_rate * duration)
        for i in range(n_samples):
            t = i / sample_rate
            env = math.exp(-i / (sample_rate * decay))
            val = int(32767 * 0.75 * env * math.sin(2 * math.pi * freq * t))
            samples.append(struct.pack("<h", max(-32768, min(32767, val))))
        # 30ms inter-pulse silence
        for _ in range(int(sample_rate * 0.03)):
            samples.append(struct.pack("<h", 0))

    with wave.open(str(target_path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(b"".join(samples))

    logger.debug(f"Synthesized tactical alert WAV: {target_path} ({len(samples)} samples)")
    return target_path


def get_default_tactical_wav_path() -> Path:
    """Returns the cached path to the default tactical alert WAV, generating it if needed."""
    wav_path = DEFAULT_SOUNDS_DIR / "tactical_alert.wav"
    if not wav_path.exists() or wav_path.stat().st_size < 1000:
        try:
            generate_tactical_alert_wav(wav_path)
        except Exception as e:
            logger.warning(f"Could not generate default tactical alert WAV: {e}")
    return wav_path


class AlertAudioManager(QObject):
    """Coordinates audio playback for tactical proximity and system alerts."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._media_player: Optional[Any] = None
        self._audio_output: Optional[Any] = None

    def _init_multimedia(self):
        """Lazily initialize Qt multimedia objects when required."""
        if not QT_MULTIMEDIA_AVAILABLE or QCoreApplication.instance() is None:
            return
        if self._media_player is None:
            try:
                self._audio_output = QAudioOutput(self)
                self._audio_output.setVolume(1.0)
                self._media_player = QMediaPlayer(self)
                self._media_player.setAudioOutput(self._audio_output)
            except Exception as e:
                logger.debug(f"Could not initialize QMediaPlayer: {e}")

    def play_sound(self, path: Union[str, Path]) -> bool:
        """Plays a sound file using the best available platform audio engine."""
        resolved = normalize_sound_path(path)
        if not resolved:
            logger.warning(f"Audio file not found or invalid: {path}")
            return False

        ext = resolved.suffix.lower()

        # 1. On Windows, use winsound for zero-latency direct sound card playback of WAV files
        if sys.platform == "win32":
            if ext == ".wav":
                try:
                    import winsound
                    winsound.PlaySound(str(resolved), winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
                    logger.debug(f"Played sound via Windows winsound: {resolved}")
                    return True
                except Exception as e:
                    logger.debug(f"winsound failed ({e}); falling back to Windows MCI / Qt Multimedia")

            # Try Windows MCI for MP3/WAV/etc.
            try:
                import ctypes
                winmm = ctypes.windll.winmm
                winmm.mciSendStringW("close mc_alert_sound", None, 0, None)
                res = winmm.mciSendStringW(f'open "{resolved}" alias mc_alert_sound', None, 0, None)
                if res == 0:
                    winmm.mciSendStringW("play mc_alert_sound from 0", None, 0, None)
                    logger.debug(f"Played sound via Windows MCI: {resolved}")
                    return True
            except Exception as e:
                logger.debug(f"Windows MCI failed ({e})")

        # 2. On Linux, use PipeWire (pw-play), PulseAudio (paplay), or ALSA (aplay) for zero-latency direct sound output
        if sys.platform.startswith("linux"):
            import shutil
            import subprocess

            linux_cmds = ["pw-play", "paplay"]
            if ext == ".wav":
                linux_cmds.append("aplay")
            elif ext in [".mp3", ".ogg", ".flac", ".m4a", ".aac"]:
                linux_cmds.extend(["mpv", "ffplay"])

            for cmd in linux_cmds:
                if shutil.which(cmd):
                    try:
                        args = [cmd]
                        if cmd == "mpv":
                            args.extend(["--no-video", "--really-quiet"])
                        elif cmd == "ffplay":
                            args.extend(["-nodisp", "-autoexit", "-loglevel", "quiet"])
                        args.append(str(resolved))
                        subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        logger.debug(f"Played sound via Linux audio tool {cmd}: {resolved}")
                        return True
                    except Exception as e:
                        logger.debug(f"Linux audio tool {cmd} failed: {e}")

        # 3. Cross-platform Qt Multimedia (QMediaPlayer with QAudioOutput)
        self._init_multimedia()

        if self._media_player is not None:
            try:
                self._media_player.stop()
                self._media_player.setSource(QUrl.fromLocalFile(str(resolved)))
                self._media_player.play()
                logger.debug(f"Played sound via QMediaPlayer: {resolved}")
                return True
            except Exception as e:
                logger.debug(f"QMediaPlayer playback failed: {e}")

        logger.warning(f"No suitable audio playback backend available for {resolved}")
        return False

    def play_adsb_alert(self, config=None) -> bool:
        """Plays the configured ADS-B tactical proximity alert.

        Respects user settings (enabled/disabled, sound mode, custom sound file).
        Gracefully falls back to default tactical chirp if custom file cannot be parsed or found.
        """
        meshcore_cfg = getattr(config, "meshcore", config) if config else None

        if meshcore_cfg:
            alert_enabled = getattr(meshcore_cfg, "adsb_alert_enabled", True)
            sound_enabled = getattr(meshcore_cfg, "adsb_alert_sound_enabled", True)
            if not alert_enabled or not sound_enabled:
                logger.debug("ADS-B alert or alert sound is disabled in config.")
                return False

            sound_mode = getattr(meshcore_cfg, "adsb_alert_sound_mode", "tactical")
            custom_path_str = getattr(meshcore_cfg, "adsb_alert_sound_file", "")

            if sound_mode == "custom" and custom_path_str:
                resolved_custom = normalize_sound_path(custom_path_str)
                if resolved_custom:
                    if self.play_sound(resolved_custom):
                        return True
                logger.warning(
                    f"Custom ADS-B alert sound file '{custom_path_str}' not found or could not be played. "
                    "Falling back to default tactical alert tone."
                )

        # Default tactical radar alert tone
        default_wav = get_default_tactical_wav_path()
        return self.play_sound(default_wav)


_global_audio_manager: Optional[AlertAudioManager] = None


def get_alert_audio_manager() -> AlertAudioManager:
    """Returns the singleton AlertAudioManager instance."""
    global _global_audio_manager
    if _global_audio_manager is None:
        _global_audio_manager = AlertAudioManager()
    return _global_audio_manager


def play_adsb_proximity_alert(config=None) -> bool:
    """Convenience helper to trigger ADS-B tactical proximity audio alert."""
    return get_alert_audio_manager().play_adsb_alert(config)
