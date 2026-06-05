"""系统音频抓取 + Whisper 实时转字幕

工作流：
- pyaudiowpatch 用 WASAPI loopback 抓 Windows 默认扬声器输出（系统声音）
- 每 N 秒攒一段
- 送 faster-whisper tiny 模型（CPU int8）转字幕
- 回调推给主循环
"""
from __future__ import annotations

import queue
import threading
import time
import wave
from io import BytesIO
from typing import Callable

try:
    import pyaudiowpatch as pyaudio
    _HAS_PYAUDIO = True
except ImportError:
    _HAS_PYAUDIO = False

try:
    from faster_whisper import WhisperModel
    _HAS_WHISPER = True
except ImportError:
    _HAS_WHISPER = False


class AudioTranscriber:
    """后台抓系统音频 + 实时转字幕

    用法：
        t = AudioTranscriber(on_text=lambda txt, ts: print(txt))
        t.start()
        # ... 主循环跑着 ...
        t.stop()
    """

    def __init__(
        self,
        model_size: str = 'tiny',          # tiny / base / small (越大越准但越慢)
        chunk_seconds: float = 8.0,         # 每 N 秒切一段送 Whisper
        language: str = 'auto',             # 'zh' / 'en' / None (auto detect)
        on_text: Callable[[str, str], None] | None = None,  # (text, ts_iso) -> None
        min_text_len: int = 3,
    ):
        if not _HAS_PYAUDIO:
            raise RuntimeError('需要 pyaudiowpatch: pip install pyaudiowpatch')
        if not _HAS_WHISPER:
            raise RuntimeError('需要 faster-whisper: pip install faster-whisper')

        self.model_size = model_size
        self.chunk_seconds = chunk_seconds
        self.language = None if language == 'auto' else language
        self.on_text = on_text or (lambda t, ts: None)
        self.min_text_len = min_text_len

        self._stop = threading.Event()
        self._audio_queue: queue.Queue = queue.Queue(maxsize=10)
        self._capture_thread: threading.Thread | None = None
        self._transcribe_thread: threading.Thread | None = None
        self._model: WhisperModel | None = None

    def _load_model(self):
        """懒加载 Whisper 模型"""
        if self._model is None:
            print(f'[whisper] loading model "{self.model_size}" (首次会下载，可能要几分钟)...')
            self._model = WhisperModel(
                self.model_size,
                device='cpu',
                compute_type='int8',  # 量化省 RAM
            )
            print(f'[whisper] model loaded')

    def _find_loopback_device(self, pa: 'pyaudio.PyAudio') -> dict:
        """找 Windows 默认扬声器的 WASAPI loopback 设备"""
        # 默认输出设备
        try:
            wasapi_info = pa.get_host_api_info_by_type(pyaudio.paWASAPI)
        except OSError:
            raise RuntimeError('系统不支持 WASAPI')

        default_speakers = pa.get_device_info_by_index(wasapi_info['defaultOutputDevice'])
        if not default_speakers.get('isLoopbackDevice', False):
            # 找匹配的 loopback
            for loop in pa.get_loopback_device_info_generator():
                if default_speakers['name'] in loop['name']:
                    return loop
            raise RuntimeError('找不到默认扬声器的 loopback 设备')
        return default_speakers

    def _capture_loop(self):
        """抓系统音频 → 攒满 chunk_seconds 秒 → 入队"""
        with pyaudio.PyAudio() as pa:
            try:
                device = self._find_loopback_device(pa)
            except Exception as e:
                print(f'[whisper] 找 loopback 失败: {e}')
                return

            sample_rate = int(device['defaultSampleRate'])
            channels = device['maxInputChannels']
            chunk_size = 1024
            frames_per_chunk = int(sample_rate * self.chunk_seconds)

            print(f'[whisper] capture device: {device["name"]} @ {sample_rate}Hz x{channels}ch')

            stream = pa.open(
                format=pyaudio.paInt16,
                channels=channels,
                rate=sample_rate,
                input=True,
                input_device_index=device['index'],
                frames_per_buffer=chunk_size,
            )

            buf = bytearray()
            try:
                while not self._stop.is_set():
                    data = stream.read(chunk_size, exception_on_overflow=False)
                    buf.extend(data)
                    # 攒够 chunk_seconds 秒 → 入队
                    bytes_per_frame = 2 * channels  # paInt16 = 2 bytes
                    if len(buf) >= frames_per_chunk * bytes_per_frame:
                        # 包装成 WAV 内存对象
                        wav_io = BytesIO()
                        with wave.open(wav_io, 'wb') as wf:
                            wf.setnchannels(channels)
                            wf.setsampwidth(2)
                            wf.setframerate(sample_rate)
                            wf.writeframes(bytes(buf))
                        wav_io.seek(0)
                        try:
                            self._audio_queue.put_nowait((wav_io, time.time()))
                        except queue.Full:
                            pass  # 转写跟不上就 drop
                        buf = bytearray()
            finally:
                stream.stop_stream()
                stream.close()

    def _transcribe_loop(self):
        """从队列拿音频 chunk → Whisper 转写 → 回调"""
        from datetime import datetime
        self._load_model()
        while not self._stop.is_set():
            try:
                wav_io, captured_ts = self._audio_queue.get(timeout=1.0)
            except queue.Empty:
                continue

            try:
                segments, info = self._model.transcribe(
                    wav_io,
                    language=self.language,
                    beam_size=1,         # tiny 模型 beam_size=1 快
                    vad_filter=True,      # voice activity detection 过滤静音
                    vad_parameters={'min_silence_duration_ms': 500},
                )
                texts = []
                for seg in segments:
                    t = seg.text.strip()
                    if len(t) >= self.min_text_len:
                        texts.append(t)
                if texts:
                    joined = ' '.join(texts)
                    ts_iso = datetime.fromtimestamp(captured_ts).isoformat()
                    try:
                        self.on_text(joined, ts_iso)
                    except Exception as e:
                        print(f'[whisper] callback err: {e}')
            except Exception as e:
                print(f'[whisper] transcribe err: {type(e).__name__}: {e}')

    def start(self):
        self._stop.clear()
        self._capture_thread = threading.Thread(target=self._capture_loop, daemon=True)
        self._transcribe_thread = threading.Thread(target=self._transcribe_loop, daemon=True)
        self._capture_thread.start()
        self._transcribe_thread.start()
        print('[whisper] started')

    def stop(self):
        self._stop.set()
        if self._capture_thread:
            self._capture_thread.join(timeout=3)
        if self._transcribe_thread:
            self._transcribe_thread.join(timeout=3)
        print('[whisper] stopped')
