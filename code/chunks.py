import os
import sys
import time
import hashlib
from PySide6.QtCore import QThread, Signal

class MemoryTrapHeartbeat(QThread):
    tamper_detected_signal = Signal()
    fake_error_signal = Signal(str)

    def __init__(self, target_file_path: str, shared_key_container: dict):
        super().__init__()
        self.target_file_path = target_file_path
        self.shared_key_container = shared_key_container
        self.is_running = True

    def run(self):
        if not os.path.exists(self.target_file_path):
            return

        last_check_time = time.perf_counter()
        
        while self.is_running:
            start_time = time.perf_counter()
            
            try:
                with open(self.target_file_path, "rb") as f:
                    f.seek(1000 if os.path.getsize(self.target_file_path) > 1000 else 0)
                    chunk = f.read(128)
                    current_hash = hashlib.sha256(chunk).hexdigest()
            except Exception:
                current_hash = ""

            end_time = time.perf_counter()
            duration = end_time - start_time
            gap = start_time - last_check_time
            
            if (duration > 0.1) or (gap > 1.5 and last_check_time > 0):
                if "subkey" in self.shared_key_container:
                    self.shared_key_container["subkey"] = b'\x00' * len(self.shared_key_container["subkey"])
                if "raw_key" in self.shared_key_container:
                    self.shared_key_container["raw_key"] = b'\x00' * len(self.shared_key_container["raw_key"])
                self.tamper_detected_signal.emit()
                break
                
            last_check_time = start_time
            time.sleep(0.4)