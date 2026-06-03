import io
import os
import tempfile
import logging

logger = logging.getLogger(__name__)

_whisper_model = None
_loaded_model_size = None


def _get_model_size():
    from . import config as app_config
    model = getattr(app_config.settings, 'whisper_model', 'tiny')
    if model not in ('tiny', 'small'):
        model = 'tiny'
    return model


def get_model():
    global _whisper_model, _loaded_model_size
    size = _get_model_size()
    if _whisper_model is None or _loaded_model_size != size:
        import whisper
        logger.info(f'Loading Whisper model: {size}')
        _whisper_model = whisper.load_model(size)
        _loaded_model_size = size
        logger.info(f'Whisper model {size} loaded')
    return _whisper_model


def transcribe_audio(audio_bytes: bytes) -> str:
    model = get_model()
    suffix = '.webm'
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
        f.write(audio_bytes)
        tmp_path = f.name

    try:
        result = model.transcribe(tmp_path, language=None)
        text = result.get('text', '').strip()
        return text
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
