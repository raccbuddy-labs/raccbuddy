"""Tests for src.core.voice — base classes, providers, manager, and handler."""

from __future__ import annotations

import os
import struct
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.voice.base import (
    BaseSTTProvider,
    BaseTTSProvider,
    SynthesisResult,
    TranscriptionResult,
)
from src.core.voice.manager import VoiceManager
from src.core.voice.providers import get_stt_provider, get_tts_provider

# ===================================================================
# Helpers
# ===================================================================


def _make_wav_file(
    path: Path, duration_seconds: float = 0.5, sample_rate: int = 16000
) -> None:
    """Write a minimal valid WAV file (silence) for testing."""
    num_samples = int(sample_rate * duration_seconds)
    data_size = num_samples * 2  # 16-bit mono
    with open(path, "wb") as f:
        # RIFF header
        f.write(b"RIFF")
        f.write(struct.pack("<I", 36 + data_size))
        f.write(b"WAVE")
        # fmt chunk
        f.write(b"fmt ")
        f.write(struct.pack("<I", 16))  # chunk size
        f.write(struct.pack("<H", 1))  # PCM format
        f.write(struct.pack("<H", 1))  # mono
        f.write(struct.pack("<I", sample_rate))
        f.write(struct.pack("<I", sample_rate * 2))  # byte rate
        f.write(struct.pack("<H", 2))  # block align
        f.write(struct.pack("<H", 16))  # bits per sample
        # data chunk
        f.write(b"data")
        f.write(struct.pack("<I", data_size))
        f.write(b"\x00" * data_size)


class StubSTTProvider(BaseSTTProvider):
    """Concrete STT provider for testing."""

    @property
    def name(self) -> str:
        return "stub_stt"

    @property
    def model_id(self) -> str:
        return "stub/stt-model"

    async def transcribe(
        self,
        audio_path: Path,
        *,
        language: str | None = None,
    ) -> TranscriptionResult:
        return TranscriptionResult(
            text="Hello from stub",
            language=language or "en",
            confidence=0.95,
        )


class StubTTSProvider(BaseTTSProvider):
    """Concrete TTS provider for testing."""

    @property
    def name(self) -> str:
        return "stub_tts"

    @property
    def model_id(self) -> str:
        return "stub/tts-model"

    async def synthesize(
        self,
        text: str,
        *,
        output_path: Path | None = None,
        voice_preset: str | None = None,
    ) -> SynthesisResult:
        if output_path is None:
            fd, tmp = tempfile.mkstemp(suffix=".wav", prefix="stub_tts_")
            output_path = Path(tmp)
            os.close(fd)

        _make_wav_file(output_path)
        return SynthesisResult(
            audio_path=output_path,
            sample_rate=16_000,
            duration_seconds=0.5,
            format="wav",
        )


# ===================================================================
# Base class tests
# ===================================================================


class TestTranscriptionResult:
    """Validate TranscriptionResult dataclass."""

    def test_defaults(self) -> None:
        r = TranscriptionResult()
        assert r.text == ""
        assert r.language == ""
        assert r.confidence == 0.0
        assert r.segments == []

    def test_custom_values(self) -> None:
        r = TranscriptionResult(
            text="Hello world",
            language="en",
            confidence=0.99,
            segments=[{"start": 0.0, "end": 1.0, "text": "Hello world"}],
        )
        assert r.text == "Hello world"
        assert r.language == "en"
        assert r.confidence == 0.99
        assert len(r.segments) == 1


class TestSynthesisResult:
    """Validate SynthesisResult dataclass."""

    def test_defaults(self) -> None:
        r = SynthesisResult(audio_path=Path("/tmp/test.wav"))
        assert r.sample_rate == 24_000
        assert r.duration_seconds == 0.0
        assert r.format == "wav"

    def test_custom_values(self) -> None:
        r = SynthesisResult(
            audio_path=Path("/tmp/test.ogg"),
            sample_rate=16_000,
            duration_seconds=3.5,
            format="ogg",
        )
        assert r.sample_rate == 16_000
        assert r.format == "ogg"


class TestBaseSTTProvider:
    """Validate the abstract base class contract."""

    def test_concrete_provider_implements_interface(self) -> None:
        provider = StubSTTProvider()
        assert provider.name == "stub_stt"
        assert provider.model_id == "stub/stt-model"

    @pytest.mark.asyncio
    async def test_transcribe_returns_result(self) -> None:
        provider = StubSTTProvider()
        result = await provider.transcribe(Path("/fake/audio.wav"))
        assert isinstance(result, TranscriptionResult)
        assert result.text == "Hello from stub"

    @pytest.mark.asyncio
    async def test_warmup_and_cleanup_are_noops_by_default(self) -> None:
        provider = StubSTTProvider()
        await provider.warmup()
        await provider.cleanup()

    def test_cannot_instantiate_abstract(self) -> None:
        with pytest.raises(TypeError):
            BaseSTTProvider()  # type: ignore[abstract]


class TestBaseTTSProvider:
    """Validate the abstract base class contract."""

    def test_concrete_provider_implements_interface(self) -> None:
        provider = StubTTSProvider()
        assert provider.name == "stub_tts"
        assert provider.model_id == "stub/tts-model"

    @pytest.mark.asyncio
    async def test_synthesize_returns_result(self) -> None:
        provider = StubTTSProvider()
        result = await provider.synthesize("Hello world")
        assert isinstance(result, SynthesisResult)
        assert result.audio_path.exists()
        # Clean up
        result.audio_path.unlink(missing_ok=True)

    def test_cannot_instantiate_abstract(self) -> None:
        with pytest.raises(TypeError):
            BaseTTSProvider()  # type: ignore[abstract]


# ===================================================================
# Provider registry tests
# ===================================================================


class TestProviderRegistry:
    """Validate provider factory functions."""

    def test_get_stt_provider_whisper(self) -> None:
        from src.core.voice.providers.whisper_stt import WhisperSTTProvider

        provider = get_stt_provider("whisper", "openai/whisper-tiny")
        assert isinstance(provider, WhisperSTTProvider)
        assert provider.name == "whisper"
        assert provider.model_id == "openai/whisper-tiny"

    def test_get_stt_provider_unknown_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown STT provider"):
            get_stt_provider("nonexistent", "model")

    def test_get_tts_provider_bark(self) -> None:
        from src.core.voice.providers.bark_tts import BarkTTSProvider

        provider = get_tts_provider("bark", "suno/bark-small")
        assert isinstance(provider, BarkTTSProvider)
        assert provider.name == "bark"
        assert provider.model_id == "suno/bark-small"

    def test_get_tts_provider_unknown_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown TTS provider"):
            get_tts_provider("nonexistent", "model")


# ===================================================================
# VoiceManager tests
# ===================================================================


@pytest.fixture
def _voice_enabled():
    """Patch settings to enable voice for tests."""
    with patch("src.core.voice.manager.settings") as mock_settings:
        mock_settings.voice_enabled = True
        mock_settings.voice_language = ""
        mock_settings.stt_provider = "whisper"
        mock_settings.stt_model = "openai/whisper-small"
        mock_settings.tts_provider = "bark"
        mock_settings.tts_model = "suno/bark-small"
        mock_settings.tts_voice_preset = "v2/en_speaker_6"
        mock_settings.voice_reply_mode = "text"
        yield mock_settings


@pytest.fixture
def _voice_disabled():
    """Patch settings to disable voice for tests."""
    with patch("src.core.voice.manager.settings") as mock_settings:
        mock_settings.voice_enabled = False
        yield mock_settings


class TestVoiceManagerDisabled:
    """Verify behaviour when voice is disabled."""

    def test_is_enabled_returns_false(self, _voice_disabled: MagicMock) -> None:
        mgr = VoiceManager()
        assert mgr.is_enabled is False

    @pytest.mark.asyncio
    async def test_transcribe_raises_when_disabled(
        self, _voice_disabled: MagicMock
    ) -> None:
        mgr = VoiceManager()
        with pytest.raises(RuntimeError, match="Voice processing is disabled"):
            await mgr.transcribe(Path("/fake.wav"))

    @pytest.mark.asyncio
    async def test_synthesize_raises_when_disabled(
        self, _voice_disabled: MagicMock
    ) -> None:
        mgr = VoiceManager()
        with pytest.raises(RuntimeError, match="Voice processing is disabled"):
            await mgr.synthesize("Hello")


class TestVoiceManagerEnabled:
    """Verify core VoiceManager behaviour with stubbed providers."""

    @pytest.mark.asyncio
    async def test_transcribe_delegates_to_stt(self, _voice_enabled: MagicMock) -> None:
        mgr = VoiceManager()
        stub = StubSTTProvider()
        mgr._stt = stub

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            wav_path = Path(f.name)
            _make_wav_file(wav_path)

        try:
            result = await mgr.transcribe(wav_path)
            assert result.text == "Hello from stub"
            assert result.language == "en"
        finally:
            wav_path.unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_synthesize_delegates_to_tts_wav(
        self, _voice_enabled: MagicMock
    ) -> None:
        mgr = VoiceManager()
        stub = StubTTSProvider()
        mgr._tts = stub

        result = await mgr.synthesize("Hello", output_format="wav")
        assert result.format == "wav"
        assert result.audio_path.exists()
        result.audio_path.unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_synthesize_converts_to_ogg(self, _voice_enabled: MagicMock) -> None:
        """When output_format='ogg', manager converts WAV → OGG via ffmpeg."""
        mgr = VoiceManager()
        stub = StubTTSProvider()
        mgr._tts = stub

        # Mock ffmpeg conversion (assume it works)
        async def _fake_ffmpeg(*args: str) -> None:
            # Simulate: create the output file
            for i, arg in enumerate(args):
                if arg.endswith(".ogg"):
                    # Write a tiny fake OGG file
                    Path(arg).write_bytes(b"OggS" + b"\x00" * 100)
                    return

        with patch.object(mgr, "_run_ffmpeg", side_effect=_fake_ffmpeg):
            result = await mgr.synthesize("Hello", output_format="ogg")
            assert result.format == "ogg"
            assert result.audio_path.suffix == ".ogg"
            result.audio_path.unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_cleanup_releases_providers(self, _voice_enabled: MagicMock) -> None:
        mgr = VoiceManager()
        stub_stt = StubSTTProvider()
        stub_tts = StubTTSProvider()
        mgr._stt = stub_stt
        mgr._tts = stub_tts

        await mgr.cleanup()
        assert mgr._stt is None
        assert mgr._tts is None

    @pytest.mark.asyncio
    async def test_warmup_skipped_when_disabled(
        self, _voice_disabled: MagicMock
    ) -> None:
        mgr = VoiceManager()
        # Should not raise
        await mgr.warmup()

    @pytest.mark.asyncio
    async def test_ensure_wav_passthrough(self, _voice_enabled: MagicMock) -> None:
        """WAV files are returned as-is without conversion."""
        mgr = VoiceManager()
        wav_path = Path("/tmp/test.wav")
        result = await mgr._ensure_wav(wav_path)
        assert result == wav_path

    @pytest.mark.asyncio
    async def test_ensure_wav_converts_oga(self, _voice_enabled: MagicMock) -> None:
        """Non-WAV files trigger ffmpeg conversion."""
        mgr = VoiceManager()
        oga_path = Path("/tmp/test.oga")

        converted = []

        async def _fake_ffmpeg(*args: str) -> None:
            # Find the output path and create it
            for arg in args:
                if arg.endswith(".wav"):
                    Path(arg).write_bytes(b"\x00" * 100)
                    converted.append(arg)

        with patch.object(mgr, "_run_ffmpeg", side_effect=_fake_ffmpeg):
            result = await mgr._ensure_wav(oga_path)
            assert result.suffix == ".wav"
            assert result != oga_path
            assert len(converted) == 1
            result.unlink(missing_ok=True)


class TestVoiceManagerFfmpeg:
    """Test ffmpeg helper method."""

    @pytest.mark.asyncio
    async def test_ffmpeg_not_found_raises(self) -> None:
        """RuntimeError when ffmpeg binary is missing."""
        with patch("asyncio.create_subprocess_exec", side_effect=FileNotFoundError()):
            with pytest.raises(RuntimeError, match="ffmpeg is required"):
                await VoiceManager._run_ffmpeg("-i", "in.wav", "out.ogg")

    @pytest.mark.asyncio
    async def test_ffmpeg_failure_raises(self) -> None:
        """RuntimeError when ffmpeg exits with non-zero code."""
        mock_proc = AsyncMock()
        mock_proc.returncode = 1
        mock_proc.communicate = AsyncMock(return_value=(b"", b"encoder error"))

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            with pytest.raises(RuntimeError, match="ffmpeg failed"):
                await VoiceManager._run_ffmpeg("-i", "in.wav", "out.ogg")

    @pytest.mark.asyncio
    async def test_ffmpeg_success(self) -> None:
        """No error when ffmpeg succeeds."""
        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(b"", b""))

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            await VoiceManager._run_ffmpeg("-i", "in.wav", "out.ogg")


# ===================================================================
# Whisper STT provider tests (mocked — no real model loading)
# ===================================================================


class TestWhisperSTTProvider:
    """Validate WhisperSTTProvider logic without downloading models."""

    def test_name_and_model_id(self) -> None:
        from src.core.voice.providers.whisper_stt import WhisperSTTProvider

        p = WhisperSTTProvider(model_id="openai/whisper-tiny")
        assert p.name == "whisper"
        assert p.model_id == "openai/whisper-tiny"

    def test_custom_device(self) -> None:
        from src.core.voice.providers.whisper_stt import WhisperSTTProvider

        p = WhisperSTTProvider(model_id="openai/whisper-tiny", device="cpu")
        assert p._device == "cpu"

    @pytest.mark.asyncio
    async def test_transcribe_file_not_found(self) -> None:
        from src.core.voice.providers.whisper_stt import WhisperSTTProvider

        p = WhisperSTTProvider()
        with pytest.raises(FileNotFoundError, match="Audio file not found"):
            await p.transcribe(Path("/nonexistent/audio.wav"))

    @pytest.mark.asyncio
    async def test_transcribe_missing_deps(self) -> None:
        from src.core.voice.providers.whisper_stt import WhisperSTTProvider

        p = WhisperSTTProvider()

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            wav_path = Path(f.name)
            _make_wav_file(wav_path)

        try:
            with patch(
                "src.core.voice.providers.whisper_stt._check_dependencies",
                return_value=False,
            ):
                with pytest.raises(ImportError, match="torch"):
                    await p.transcribe(wav_path)
        finally:
            wav_path.unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_transcribe_with_mocked_pipeline(self) -> None:
        from src.core.voice.providers.whisper_stt import WhisperSTTProvider

        p = WhisperSTTProvider()

        # Mock the pipeline callable
        mock_pipe = MagicMock()
        mock_pipe.return_value = {
            "text": "Hello world",
            "chunks": [
                {"timestamp": (0.0, 1.5), "text": "Hello world"},
            ],
        }
        p._pipe = mock_pipe

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            wav_path = Path(f.name)
            _make_wav_file(wav_path)

        try:
            with patch(
                "src.core.voice.providers.whisper_stt._check_dependencies",
                return_value=True,
            ):
                result = await p.transcribe(wav_path)
                assert result.text == "Hello world"
                assert len(result.segments) == 1
                assert result.segments[0]["start"] == 0.0
                # No language → generate_kwargs must NOT be passed at all
                call_kwargs = mock_pipe.call_args[1]
                assert "generate_kwargs" not in call_kwargs
        finally:
            wav_path.unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_transcribe_with_language_forced(self) -> None:
        from src.core.voice.providers.whisper_stt import WhisperSTTProvider

        p = WhisperSTTProvider()

        mock_pipe = MagicMock()
        mock_pipe.return_value = {"text": "Hallo Welt", "chunks": []}
        p._pipe = mock_pipe

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            wav_path = Path(f.name)
            _make_wav_file(wav_path)

        try:
            with patch(
                "src.core.voice.providers.whisper_stt._check_dependencies",
                return_value=True,
            ):
                result = await p.transcribe(wav_path, language="de")
                assert result.language == "de"
                # Verify generate_kwargs were passed
                call_kwargs = mock_pipe.call_args[1]
                assert call_kwargs["generate_kwargs"]["language"] == "de"
        finally:
            wav_path.unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_cleanup_unloads_model(self) -> None:
        from src.core.voice.providers.whisper_stt import WhisperSTTProvider

        p = WhisperSTTProvider()
        p._pipe = MagicMock()  # Simulate loaded model
        await p.cleanup()
        assert p._pipe is None


# ===================================================================
# Bark TTS provider tests (mocked — no real model loading)
# ===================================================================


class TestBarkTTSProvider:
    """Validate BarkTTSProvider logic without downloading models."""

    def test_name_and_model_id(self) -> None:
        from src.core.voice.providers.bark_tts import BarkTTSProvider

        p = BarkTTSProvider(model_id="suno/bark-small")
        assert p.name == "bark"
        assert p.model_id == "suno/bark-small"

    def test_default_voice_preset(self) -> None:
        from src.core.voice.providers.bark_tts import BarkTTSProvider

        p = BarkTTSProvider()
        assert p._default_voice_preset == "v2/en_speaker_6"

    @pytest.mark.asyncio
    async def test_synthesize_missing_deps(self) -> None:
        from src.core.voice.providers.bark_tts import BarkTTSProvider

        p = BarkTTSProvider()
        with patch(
            "src.core.voice.providers.bark_tts._check_dependencies",
            return_value=False,
        ):
            with pytest.raises(ImportError, match="torch"):
                await p.synthesize("Hello")

    @pytest.mark.asyncio
    async def test_synthesize_with_mocked_model(self) -> None:
        import numpy as np

        from src.core.voice.providers.bark_tts import BarkTTSProvider

        p = BarkTTSProvider()

        # Mock model and processor
        mock_processor = MagicMock()
        mock_processor.return_value = {"input_ids": MagicMock()}

        mock_model = MagicMock()
        # Simulate generate returning a tensor-like object
        fake_audio = MagicMock()
        fake_audio.cpu.return_value.numpy.return_value.squeeze.return_value = np.zeros(
            24_000,
            dtype=np.float32,  # 1 second of silence
        )
        mock_model.generate.return_value = fake_audio
        mock_model.parameters.return_value = iter(
            [MagicMock(device=MagicMock(__str__=lambda s: "cpu"))],
        )

        p._processor = mock_processor
        p._model = mock_model

        # Mock scipy since it may not be installed in test env
        mock_wavfile = MagicMock()
        mock_wavfile.write = MagicMock(
            side_effect=lambda path, sr, data: Path(path).write_bytes(
                b"RIFF" + b"\x00" * 100
            )
        )
        mock_scipy_io = MagicMock(wavfile=mock_wavfile)

        with patch(
            "src.core.voice.providers.bark_tts._check_dependencies",
            return_value=True,
        ), patch.dict(
            "sys.modules",
            {
                "scipy": MagicMock(io=mock_scipy_io),
                "scipy.io": mock_scipy_io,
                "scipy.io.wavfile": mock_wavfile,
            },
        ):
            result = await p.synthesize("Hello")
            assert result.audio_path.exists()
            assert result.format == "wav"
            assert result.sample_rate == 24_000
            assert result.duration_seconds == pytest.approx(1.0, abs=0.1)
            result.audio_path.unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_synthesize_custom_output_path(self) -> None:
        import numpy as np

        from src.core.voice.providers.bark_tts import BarkTTSProvider

        p = BarkTTSProvider()

        mock_processor = MagicMock()
        mock_processor.return_value = {"input_ids": MagicMock()}

        mock_model = MagicMock()
        fake_audio = MagicMock()
        fake_audio.cpu.return_value.numpy.return_value.squeeze.return_value = np.zeros(
            12_000,
            dtype=np.float32,
        )
        mock_model.generate.return_value = fake_audio
        mock_model.parameters.return_value = iter(
            [MagicMock(device=MagicMock(__str__=lambda s: "cpu"))],
        )

        p._processor = mock_processor
        p._model = mock_model

        mock_wavfile = MagicMock()
        mock_wavfile.write = MagicMock(
            side_effect=lambda path, sr, data: Path(path).write_bytes(
                b"RIFF" + b"\x00" * 100
            )
        )
        mock_scipy_io = MagicMock(wavfile=mock_wavfile)

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            out_path = Path(f.name)

        try:
            with patch(
                "src.core.voice.providers.bark_tts._check_dependencies",
                return_value=True,
            ), patch.dict(
                "sys.modules",
                {
                    "scipy": MagicMock(io=mock_scipy_io),
                    "scipy.io": mock_scipy_io,
                    "scipy.io.wavfile": mock_wavfile,
                },
            ):
                result = await p.synthesize("Hi", output_path=out_path)
                assert result.audio_path == out_path
                assert result.audio_path.exists()
        finally:
            out_path.unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_synthesize_custom_voice_preset(self) -> None:
        import numpy as np

        from src.core.voice.providers.bark_tts import BarkTTSProvider

        p = BarkTTSProvider()

        mock_processor = MagicMock()
        mock_processor.return_value = {"input_ids": MagicMock()}

        mock_model = MagicMock()
        fake_audio = MagicMock()
        fake_audio.cpu.return_value.numpy.return_value.squeeze.return_value = np.zeros(
            24_000,
            dtype=np.float32,
        )
        mock_model.generate.return_value = fake_audio
        mock_model.parameters.return_value = iter(
            [MagicMock(device=MagicMock(__str__=lambda s: "cpu"))],
        )

        p._processor = mock_processor
        p._model = mock_model

        mock_wavfile = MagicMock()
        mock_wavfile.write = MagicMock(
            side_effect=lambda path, sr, data: Path(path).write_bytes(
                b"RIFF" + b"\x00" * 100
            )
        )
        mock_scipy_io = MagicMock(wavfile=mock_wavfile)

        with patch(
            "src.core.voice.providers.bark_tts._check_dependencies",
            return_value=True,
        ), patch.dict(
            "sys.modules",
            {
                "scipy": MagicMock(io=mock_scipy_io),
                "scipy.io": mock_scipy_io,
                "scipy.io.wavfile": mock_wavfile,
            },
        ):
            result = await p.synthesize("Hello", voice_preset="v2/de_speaker_3")
            # Verify the custom preset was passed to the processor
            mock_processor.assert_called_once_with(
                "Hello",
                voice_preset="v2/de_speaker_3",
            )
            result.audio_path.unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_cleanup_unloads_model(self) -> None:
        from src.core.voice.providers.bark_tts import BarkTTSProvider

        p = BarkTTSProvider()
        p._model = MagicMock()
        p._processor = MagicMock()
        await p.cleanup()
        assert p._model is None
        assert p._processor is None


# ===================================================================
# Config tests
# ===================================================================


class TestVoiceConfig:
    """Verify voice-related config defaults."""

    def test_voice_settings_defaults(self) -> None:
        from src.core.config import Settings

        fields = Settings.model_fields
        assert fields["voice_enabled"].default is False
        assert fields["voice_reply_mode"].default == "text"
        assert fields["voice_language"].default == ""
        assert fields["stt_provider"].default == "whisper"
        assert fields["stt_model"].default == "openai/whisper-small"
        assert fields["tts_provider"].default == "bark"
        assert fields["tts_model"].default == "suno/bark-small"
        assert fields["tts_voice_preset"].default == "v2/en_speaker_6"

    def test_voice_enabled_overridable(self) -> None:
        from src.core.config import Settings

        env = {
            "TELEGRAM_BOT_TOKEN": "test-token",
            "VOICE_ENABLED": "true",
            "STT_MODEL": "openai/whisper-tiny",
            "TTS_MODEL": "suno/bark",
        }
        with patch.dict(os.environ, env, clear=False):
            s = Settings()
        assert s.voice_enabled is True
        assert s.stt_model == "openai/whisper-tiny"
        assert s.tts_model == "suno/bark"


# ===================================================================
# Voice handler tests (Telegram handler integration)
# ===================================================================


_AUTH_PATCH = patch(
    "src.handlers.voice.reject_non_owner",
    new_callable=AsyncMock,
    return_value=False,
)
_OWNER_PATCH = patch("src.handlers.voice._owner_id", return_value=100)


@pytest.mark.asyncio
class TestVoiceHandler:
    """Validate voice handler processing flow."""

    @_AUTH_PATCH
    @_OWNER_PATCH
    async def test_voice_disabled_returns_message(
        self,
        mock_owner: MagicMock,
        mock_auth: AsyncMock,
    ) -> None:
        from src.handlers.voice import voice_handler

        with patch("src.handlers.voice.voice_manager") as mock_vm:
            mock_vm.is_enabled = False

            update = MagicMock()
            update.message.text = None
            update.message.voice = MagicMock()
            update.message.audio = None
            update.effective_user.id = 100
            update.effective_chat.id = 200
            update.message.reply_text = AsyncMock()

            await voice_handler(update, MagicMock())
            update.message.reply_text.assert_called_once()
            call_text = update.message.reply_text.call_args[0][0]
            assert "VOICE_ENABLED" in call_text

    @_AUTH_PATCH
    @_OWNER_PATCH
    async def test_voice_no_attachment_returns(
        self,
        mock_owner: MagicMock,
        mock_auth: AsyncMock,
    ) -> None:
        from src.handlers.voice import voice_handler

        with patch("src.handlers.voice.voice_manager") as mock_vm:
            mock_vm.is_enabled = True

            update = MagicMock()
            update.message.voice = None
            update.message.audio = None
            update.effective_user.id = 100
            update.effective_chat.id = 200
            update.message.reply_text = AsyncMock()

            await voice_handler(update, MagicMock())
            # Should return early without any reply
            update.message.reply_text.assert_not_called()

    @_AUTH_PATCH
    @_OWNER_PATCH
    @patch("src.handlers.chat._enrich_after_message", new_callable=AsyncMock)
    @patch("src.handlers.voice.provider_supports_tools", return_value=False)
    @patch("src.handlers.voice.generate_chat", new_callable=AsyncMock)
    @patch("src.handlers.voice.context_builder.build_messages", new_callable=AsyncMock)
    @patch("src.handlers.voice.persist_incoming", new_callable=AsyncMock)
    async def test_full_voice_pipeline_text_reply(
        self,
        mock_save: AsyncMock,
        mock_build: AsyncMock,
        mock_generate: AsyncMock,
        mock_supports_tools: MagicMock,
        mock_enrich: AsyncMock,
        mock_owner: MagicMock,
        mock_auth: AsyncMock,
    ) -> None:
        from src.handlers.voice import voice_handler

        mock_build.return_value = [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "Hello there"},
        ]
        mock_generate.return_value = "Hey! Nice to hear from you 🦝"

        with patch("src.handlers.voice.voice_manager") as mock_vm, patch(
            "src.handlers.voice.settings"
        ) as mock_settings:
            mock_vm.is_enabled = True
            mock_vm.transcribe = AsyncMock(
                return_value=TranscriptionResult(
                    text="Hello there",
                    language="en",
                    confidence=0.95,
                ),
            )
            mock_settings.voice_enabled = True
            mock_settings.voice_reply_mode = "text"
            mock_settings.voice_language = ""
            mock_settings.tts_voice_preset = ""

            # Build update mock
            update = MagicMock()
            update.message.voice = MagicMock()
            update.message.audio = None
            update.effective_user.id = 100
            update.effective_chat.id = 200
            update.message.reply_voice = AsyncMock()

            # The first reply_text returns a processing_msg mock with edit_text
            processing_msg = AsyncMock()
            update.message.reply_text = AsyncMock(return_value=processing_msg)

            # Mock file download
            mock_file = AsyncMock()
            mock_file.download_to_drive = AsyncMock()
            update.message.voice.get_file = AsyncMock(return_value=mock_file)

            await voice_handler(update, MagicMock())

            # Should have transcribed
            mock_vm.transcribe.assert_called_once()

            # First call: "🎤 Processing voice message…" → returns processing_msg
            # Then processing_msg.edit_text("🎤 *You said:* Hello there")
            processing_msg.edit_text.assert_called()
            edit_args = [str(c) for c in processing_msg.edit_text.call_args_list]
            assert any("Hello there" in a for a in edit_args)

            # reply_text is called multiple times: first for processing msg, then for LLM reply
            all_calls = [str(c) for c in update.message.reply_text.call_args_list]
            assert any("Nice to hear" in c for c in all_calls)

    @_AUTH_PATCH
    @_OWNER_PATCH
    async def test_empty_transcription_returns_message(
        self,
        mock_owner: MagicMock,
        mock_auth: AsyncMock,
    ) -> None:
        from src.handlers.voice import voice_handler

        with patch("src.handlers.voice.voice_manager") as mock_vm, patch(
            "src.handlers.voice.settings"
        ) as mock_settings:
            mock_vm.is_enabled = True
            mock_vm.transcribe = AsyncMock(
                return_value=TranscriptionResult(
                    text="  ", language="", confidence=0.0
                ),
            )
            mock_settings.voice_enabled = True
            mock_settings.voice_language = ""

            update = MagicMock()
            update.message.voice = MagicMock()
            update.message.audio = None
            update.effective_user.id = 100
            update.effective_chat.id = 200

            processing_msg = AsyncMock()
            update.message.reply_text = AsyncMock(return_value=processing_msg)

            mock_file = AsyncMock()
            mock_file.download_to_drive = AsyncMock()
            update.message.voice.get_file = AsyncMock(return_value=mock_file)

            await voice_handler(update, MagicMock())

            # Should have told user the transcription was empty
            processing_msg.edit_text.assert_called()
            edit_text = processing_msg.edit_text.call_args[0][0]
            assert "couldn't make out" in edit_text


# ===================================================================
# Module-level import tests
# ===================================================================


class TestVoiceModuleImports:
    """Verify the voice module public API is importable."""

    def test_import_voice_module(self) -> None:
        from src.core.voice import VoiceManager, synthesize, transcribe, voice_manager

        assert isinstance(voice_manager, VoiceManager)
        assert callable(transcribe)
        assert callable(synthesize)

    def test_import_base_classes(self) -> None:
        from src.core.voice.base import (
            BaseSTTProvider,
            BaseTTSProvider,
            SynthesisResult,
            TranscriptionResult,
        )

        assert BaseSTTProvider is not None
        assert BaseTTSProvider is not None
        assert TranscriptionResult is not None
        assert SynthesisResult is not None

    def test_import_providers(self) -> None:
        from src.core.voice.providers import get_stt_provider, get_tts_provider
        from src.core.voice.providers.bark_tts import BarkTTSProvider
        from src.core.voice.providers.whisper_stt import WhisperSTTProvider

        assert WhisperSTTProvider is not None
        assert BarkTTSProvider is not None
        assert callable(get_stt_provider)
        assert callable(get_tts_provider)
