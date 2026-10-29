"""Optional offline F8 push-to-talk and queued text-to-speech workers."""

from __future__ import annotations

import ctypes
import json
from pathlib import Path
from queue import Empty, Full, PriorityQueue
from threading import Event, Thread
import time
from typing import Callable

from .logging_setup import LOGGER


class VoiceRadio:
    def __init__(self, answer: Callable[[str], str], model_path: Path, *, volume: float = 0.8,
                 input_device: int | None = None) -> None:
        if not 0 <= volume <= 1:
            raise ValueError("voice volume must be between zero and one")
        self.answer = answer
        self.model_path = Path(model_path)
        self.volume = volume
        self.input_device = input_device
        self.stop_event = Event()
        self.queue: PriorityQueue[tuple[int, int, str]] = PriorityQueue(maxsize=10)
        self.sequence = 0
        self.threads: list[Thread] = []
        self.status = "off"

    def start(self) -> None:
        if not self.model_path.is_dir():
            raise ValueError("Vosk model folder is missing")
        if not hasattr(ctypes, "windll"):
            raise RuntimeError("global F8 push-to-talk requires Windows")
        import sounddevice  # noqa: F401 - optional dependency check
        import vosk  # noqa: F401
        import pyttsx3  # noqa: F401
        self.threads = [Thread(target=self._listen, name="voice-input", daemon=True),
                        Thread(target=self._speak, name="voice-output", daemon=True)]
        self.status = "starting"
        for thread in self.threads:
            thread.start()

    def say(self, message: str, priority: int = 2) -> None:
        self.sequence += 1
        try:
            self.queue.put_nowait((priority, self.sequence, message[:300]))
        except Full:
            LOGGER.warning("Voice queue full; radio message dropped")

    def close(self) -> None:
        self.stop_event.set()
        for thread in self.threads:
            thread.join(timeout=2)
        self.status = "off"

    def _listen(self) -> None:
        import sounddevice as sd
        import vosk

        try:
            model = vosk.Model(str(self.model_path))
            self.status = "ready"
            while not self.stop_event.is_set():
                if not (ctypes.windll.user32.GetAsyncKeyState(0x77) & 0x8000):  # F8
                    self.stop_event.wait(0.04)
                    continue
                recognizer = vosk.KaldiRecognizer(model, 16000)
                started = time.monotonic()
                try:
                    with sd.RawInputStream(samplerate=16000, blocksize=4000, channels=1,
                                           dtype="int16", device=self.input_device) as stream:
                        while (not self.stop_event.is_set() and time.monotonic() - started < 8
                               and ctypes.windll.user32.GetAsyncKeyState(0x77) & 0x8000):
                            audio, _overflowed = stream.read(4000)
                            recognizer.AcceptWaveform(bytes(audio))
                    question = json.loads(recognizer.FinalResult()).get("text", "").strip()
                    if question:
                        reply = self.answer(question)
                        LOGGER.info("Voice question answered (content omitted from log)")
                        self.say(reply, 1)
                except Exception:
                    LOGGER.exception("Voice input failed; retrying on next F8 press")
                self.stop_event.wait(0.3)
        except Exception:
            self.status = "recognition error"
            LOGGER.exception("Voice recognition worker stopped")

    def _speak(self) -> None:
        import pyttsx3

        try:
            engine = pyttsx3.init()
            engine.setProperty("volume", self.volume)
            while not self.stop_event.is_set():
                try:
                    _priority, _sequence, message = self.queue.get(timeout=0.2)
                except Empty:
                    continue
                try:
                    engine.say(message)
                    engine.runAndWait()
                except Exception:
                    LOGGER.exception("Voice output failed; continuing without this message")
        except Exception:
            self.status = "output error"
            LOGGER.exception("Voice output worker stopped")
