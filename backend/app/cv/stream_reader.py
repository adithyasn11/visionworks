# backend/app/cv/stream_reader.py
import cv2
import time
import threading
from queue import Queue

class ThreadedStreamReader:
    """
    Asynchronous threaded stream reader for RTSP camera feeds or local test video files.
    Ingests frames at a fixed target FPS without blocking the main AI inference loop.
    """
    def __init__(self, source_path: str, fps_target: int = 8, queue_size: int = 15):
        self.source_path = source_path
        self.fps_target = fps_target
        self.frame_delay = 1.0 / fps_target
        self.queue = Queue(maxsize=queue_size)
        self.cap = None
        self.is_running = False
        self.thread = None

    def start(self):
        self.cap = cv2.VideoCapture(self.source_path)
        if not self.cap.isOpened():
            raise ValueError(f"Failed to open video source: {self.source_path}")
        self.is_running = True
        self.thread = threading.Thread(target=self._capture_loop, daemon=True)
        self.thread.start()

    def _capture_loop(self):
        last_time = time.time()
        while self.is_running and self.cap.isOpened():
            current_time = time.time()
            if (current_time - last_time) >= self.frame_delay:
                ret, frame = self.cap.read()
                if not ret:
                    # Loop video if test file ends
                    self.cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                    continue
                last_time = current_time
                if self.queue.full():
                    self.queue.get_nowait() # Drop oldest frame to prevent accumulation latency
                self.queue.put(frame)
            else:
                time.sleep(0.005)

    def read_frame(self):
        return self.queue.get() if not self.queue.empty() else None

    def stop(self):
        self.is_running = False
        if self.cap:
            self.cap.release()
