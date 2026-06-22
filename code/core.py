import sys
import os
import math
import json
import hashlib  
import xml.etree.ElementTree as ET
import numpy as np
import zipfile  
import time
from PySide6.QtCore import Qt, QThread, Signal, QPoint, QTimer
from PySide6.QtGui import QImage, QPixmap, QColor, QPainter, QBrush, QPen, QPolygon
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, 
    QHBoxLayout, QLabel, QPushButton, QFileDialog, QLineEdit, 
    QTabWidget, QFrame, QMessageBox
)
from shiboken6 import isValid 

from Crypto.Cipher import AES
import ui as ui
from chunks import MemoryTrapHeartbeat

class UIStructureCryptWorker(QThread):
    status_signal = Signal(str)
    finished_signal = Signal(tuple)

    def __init__(self, file_path, raw_key_text):
        super().__init__()
        self.file_path = file_path
        self.key_container = {"raw_key": raw_key_text, "subkey": b""}
        self.heartbeat_protector = None

    def run(self):
        self.heartbeat_protector = MemoryTrapHeartbeat(self.file_path, self.key_container)
        self.heartbeat_protector.tamper_detected_signal.connect(
            lambda: self.status_signal.emit("⚠️ Anti-debug/modulation detection! Memory trap activated.")
        )
        self.heartbeat_protector.start()

        time.sleep(0.1)
        
        out_package, res_msg = ui.execute_encryption_pipeline(
            file_path=self.file_path,
            raw_key_text=self.key_container["raw_key"],
            status_callback=lambda msg: self.status_signal.emit(msg)
        )

        if self.heartbeat_protector:
            self.heartbeat_protector.is_running = False
            self.heartbeat_protector.wait()

        if out_package:
            self.finished_signal.emit((True, res_msg))
        else:
            self.finished_signal.emit((False, res_msg))


class UIStructureDecryptWorker(QThread):
    status_signal = Signal(str)
    finished_signal = Signal(tuple)

    def __init__(self, svg_container_path, raw_key_text, current_pitch, current_yaw):
        super().__init__()
        self.svg_container_path = svg_container_path
        self.raw_key_text = raw_key_text
        self.current_pitch = current_pitch
        self.current_yaw = current_yaw

    def run(self):
        out_restored, res_msg, is_success = ui.execute_decryption_pipeline(
            svg_container_path=self.svg_container_path,
            raw_key_text=self.raw_key_text,
            current_pitch=self.current_pitch,
            current_yaw=self.current_yaw,
            status_callback=lambda msg: self.status_signal.emit(msg)
        )

        self.finished_signal.emit((is_success, res_msg))

class UIStructureDecryptWorker(QThread):
    status_signal = Signal(str)
    finished_signal = Signal(tuple) 

    def __init__(self, svg_container_path, raw_key_text, current_pitch, current_yaw):
        super().__init__()
        self.svg_container_path = svg_container_path
        self.key_container = {"raw_key": raw_key_text, "subkey": b""}
        self.current_pitch = current_pitch
        self.current_yaw = current_yaw
        self.heartbeat_protector = None

    def run(self):
        self.heartbeat_protector = MemoryTrapHeartbeat(self.svg_container_path, self.key_container)
        self.heartbeat_protector.tamper_detected_signal.connect(
            lambda: self.status_signal.emit("⚠️ 리버스 엔지니어링 공격 감지! 메모리 초기화.")
        )
        self.heartbeat_protector.start()

        result_tuple = ui.execute_decryption_pipeline(
            svg_container_path=self.svg_container_path, 
            raw_key_text=self.key_container["raw_key"], 
            current_pitch=self.current_pitch, 
            current_yaw=self.current_yaw,
            status_callback=self.status_signal.emit
        )
        
        if self.heartbeat_protector:
            self.heartbeat_protector.is_running = False
            self.heartbeat_protector.wait()

        self.finished_signal.emit(result_tuple)

class InteractiveCubeViewport(QLabel):
    angle_changed_signal = Signal(float, float, bool, float)
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.pitch = 0.0  
        self.yaw = 0.0
        self.last_mouse_pos = QPoint()
        self.is_dragging = False 
        self.current_speed = 0.0 
        self.main_app_ref = None  
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAlignment(Qt.AlignCenter)

    def mousePressEvent(self, event):
        if self.main_app_ref and (not self.main_app_ref.is_unlocked() or self.main_app_ref.is_lens_active):
            event.ignore()
            return
        if event.button() == Qt.LeftButton:
            self.last_mouse_pos = event.position().toPoint()
            self.is_dragging = True
            self.current_speed = 0.0
            event.accept()

    def mouseMoveEvent(self, event):
        if self.main_app_ref and (not self.main_app_ref.is_unlocked() or self.main_app_ref.is_lens_active):
            event.ignore()
            return
        if event.buttons() & Qt.LeftButton:
            curr_pos = event.position().toPoint()
            diff = curr_pos - self.last_mouse_pos
            self.last_mouse_pos = curr_pos
            self.current_speed = math.sqrt(diff.x()**2 + diff.y()**2)
            
            self.yaw += diff.x() * 0.4
            self.pitch -= diff.y() * 0.4
            self.yaw = (self.yaw + 180) % 360 - 180
            self.pitch = (self.pitch + 180) % 360 - 180
            
            self.angle_changed_signal.emit(self.pitch, self.yaw, True, self.current_speed)
            event.accept()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.is_dragging = False
            self.current_speed = 0.0
            self.angle_changed_signal.emit(self.pitch, self.yaw, False, 0.0)
            event.accept()

class IntegratedSpatioTemporalApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.target_svg_container = None
        self.meta_header_cache = None
        self.base_seed_cache = 0
        self.btn_run_bake = None
        self.visual_offsets = None 
        
        self.local_target_pitch = 0.0
        self.local_target_yaw = 0.0
        
        self.worker = None
        self.dec_real_worker = None
        self.is_lens_active = False
        self.lens_time = 0.0
        self.last_decryption_result = None
        
        self.lens_timer = QTimer()
        self.lens_timer.setInterval(40)  
        self.lens_timer.timeout.connect(self.update_lens_animation)
        
        self.cached_is_valid_key = False
        
        self.setWindowTitle("EN/DECRYPTION SYSTEM")
        self.resize(1100, 850)
        self.setAcceptDrops(True)
        self.init_composite_ui()
        self.apply_premium_dark_theme()

    def is_unlocked(self):
        return bool(self.txt_dec_key.text().strip())

    def init_composite_ui(self):
        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)
        
        enc_page = QWidget()
        enc_layout = QVBoxLayout(enc_page)
        enc_layout.setSpacing(15)
        enc_layout.setContentsMargins(30, 30, 30, 30)

        header_card = QFrame()
        header_card.setObjectName("HeaderCard")
        hl = QVBoxLayout(header_card)
        lbl = QLabel("SYNC MATRIX PACKAGE")
        lbl.setObjectName("MainTitle")
        hl.addWidget(lbl)
        enc_layout.addWidget(header_card)

        self.drop_board = QFrame()
        self.drop_board.setObjectName("DragDropBoard")
        board_layout = QVBoxLayout(self.drop_board)
        board_layout.setAlignment(Qt.AlignCenter)
        
        self.lbl_drop_hint = QLabel("📂 DRAG & DROP VIDEO FILE HERE")
        self.lbl_drop_hint.setObjectName("DropHintLabel")
        self.lbl_drop_hint.setAlignment(Qt.AlignCenter)
        board_layout.addWidget(self.lbl_drop_hint)
        
        self.btn_browse = QPushButton("or Browse Native File")
        self.btn_browse.setObjectName("BtnSecondary")
        self.btn_browse.setFixedWidth(220)
        self.btn_browse.clicked.connect(self.select_source_file)
        board_layout.addWidget(self.btn_browse, alignment=Qt.AlignCenter)
        
        enc_layout.addWidget(self.drop_board, stretch=6)

        io_card = QFrame()
        io_card.setObjectName("CentralCard")
        iol = QVBoxLayout(io_card)
        
        key_layout = QHBoxLayout()
        lbl_key = QLabel("🔑 Master Passkey:")
        lbl_key.setObjectName("FormLabel")
        self.txt_enc_key = QLineEdit()
        self.txt_enc_key.setEchoMode(QLineEdit.Password)
        self.txt_enc_key.setPlaceholderText("Enter secure passkey for encryption matrix...")
        key_layout.addWidget(lbl_key)
        key_layout.addWidget(self.txt_enc_key)
        iol.addLayout(key_layout)
        enc_layout.addWidget(io_card)

        ctrl_card = QFrame()
        ctrl_card.setObjectName("CentralCard")
        cl = QVBoxLayout(ctrl_card)
        cl.setSpacing(12)

        self.lbl_enc_status = QLabel("🔒 Encoder Standby\n[Awaiting target video resource pack]")
        self.lbl_enc_status.setObjectName("HologramScreen")
        self.lbl_enc_status.setAlignment(Qt.AlignCenter)
        self.lbl_enc_status.setFixedHeight(65)
        
        self.btn_run_bake = QPushButton("Run Encryption Package Process")
        self.btn_run_bake.setEnabled(False)
        self.btn_run_bake.clicked.connect(self.execute_encoder_pipeline)
        
        cl.addWidget(self.lbl_enc_status)
        cl.addWidget(self.btn_run_bake)
        enc_layout.addWidget(ctrl_card)
        
        self.tabs.addTab(enc_page, "Video Encoder")

        dec_page = QWidget()
        dec_layout = QVBoxLayout(dec_page)
        dec_layout.setSpacing(15)
        dec_layout.setContentsMargins(30, 25, 30, 25)

        dec_top_card = QFrame()
        dec_top_card.setObjectName("CentralCard")
        dt_layout = QVBoxLayout(dec_top_card)
        dt_layout.setSpacing(12)

        btn_load_svg = QPushButton("📂 Open Secure Package (.smp)")
        btn_load_svg.setObjectName("BtnSecondary")
        btn_load_svg.clicked.connect(self.select_vector_container)
        dt_layout.addWidget(btn_load_svg)

        dec_key_layout = QHBoxLayout()
        lbl_dec_key = QLabel("🔑 Decryption Passkey:")
        lbl_dec_key.setObjectName("FormLabel")
        self.txt_dec_key = QLineEdit()
        self.txt_dec_key.setEchoMode(QLineEdit.Password)
        self.txt_dec_key.setPlaceholderText("Enter master target passkey to initialize alignment...")
        self.txt_dec_key.textChanged.connect(self.on_master_key_typing)
        dec_key_layout.addWidget(lbl_dec_key)
        dec_key_layout.addWidget(self.txt_dec_key)
        dt_layout.addLayout(dec_key_layout)
        dec_layout.addWidget(dec_top_card)

        self.lbl_3d_viewport = InteractiveCubeViewport()
        self.lbl_3d_viewport.main_app_ref = self 
        self.lbl_3d_viewport.setObjectName("ViewportMonitor")
        self.lbl_3d_viewport.angle_changed_signal.connect(self.on_cube_viewport_rotated)
        dec_layout.addWidget(self.lbl_3d_viewport, stretch=5)

        dec_ctrl_card = QFrame()
        dec_ctrl_card.setObjectName("CentralCard")
        dc_layout = QVBoxLayout(dec_ctrl_card)
        dc_layout.setSpacing(10)

        self.lbl_hologram_screen = QLabel("🔒 System Locked\n[Step 1: Load .smp file]  [Step 2: Enter key & Match Angle to Center]")
        self.lbl_hologram_screen.setObjectName("HologramScreen")
        self.lbl_hologram_screen.setAlignment(Qt.AlignCenter)
        self.lbl_hologram_screen.setFixedHeight(65)
        dc_layout.addWidget(self.lbl_hologram_screen)

        angle_info_layout = QHBoxLayout()
        self.lbl_angle_indicator = QLabel("Spatial Matrix Offset -> Pitch: 0.0° | Yaw: 0.0°")
        self.lbl_angle_indicator.setObjectName("AngleLabel")
        angle_info_layout.addWidget(self.lbl_angle_indicator)
        
        dc_layout.addLayout(angle_info_layout)

        self.btn_decrypt = QPushButton("🔓 Decode Package & Restore Video")
        self.btn_decrypt.clicked.connect(self.force_trigger_decoding)
        dc_layout.addWidget(self.btn_decrypt)
        
        dec_layout.addWidget(dec_ctrl_card, stretch=2)
        self.tabs.addTab(dec_page, "Package Decoder")

        self.cached_encoder_source_path = ""

    def apply_premium_dark_theme(self):
        self.setStyleSheet("""
            QMainWindow { background-color: #121214; }
            QTabWidget::pane { border: 1px solid #232329; background: #121214; top: -1px; }
            QTabBar::tab { background: #1a1a1e; color: #b3b3b3; border: 1px solid #232329; padding: 10px 25px; min-width: 120px; font-weight: bold; border-top-left-radius: 6px; border-top-right-radius: 6px; }
            QTabBar::tab:selected { background: #121214; color: #2dd4bf; border-bottom-color: #121214; border-top: 2px solid #2dd4bf; }
            QFrame#HeaderCard { background-color: #1a1a1e; border: 1px solid #2bbbb4; border-left: 5px solid #2dd4bf; border-radius: 6px; }
            QFrame#CentralCard { background-color: #1a1a1e; border: 1px solid #232329; border-radius: 8px; padding: 10px; }
            QFrame#DragDropBoard { background-color: #0d0d0f; border: 2px dashed #2c2c35; border-radius: 12px; }
            QFrame#DragDropBoard:hover { border-color: #2dd4bf; }
            QLabel#DropHintLabel { color: #888896; font-size: 14px; font-weight: bold; font-family: 'Consolas', monospace; }
            QLabel#MainTitle { color: #ffffff; font-size: 16px; font-weight: bold; letter-spacing: 1px; }
            QLabel#FormLabel { color: #ffffff; font-size: 13px; font-weight: bold; min-width: 140px; }
            QLabel#AngleLabel { color: #ffffff; font-size: 12px; font-family: 'Consolas', monospace; }
            QLineEdit { background-color: #232329; color: #ffffff; padding: 8px 12px; border: 1px solid #2c2c35; border-radius: 6px; font-size: 13px; selection-background-color: #2dd4bf; }
            QLineEdit:focus { border: 1px solid #2dd4bf; }
            QPushButton { background-color: #2dd4bf; color: #121214; padding: 10px 18px; border: none; border-radius: 6px; font-size: 13px; font-weight: bold; }
            QPushButton:hover { background-color: #24b2a0; }
            QPushButton:disabled { background-color: #2a3534; color: #666666; }
            QPushButton#BtnSecondary { background-color: #232329; color: #ffffff; border: 1px solid #32323f; }
            QPushButton#BtnSecondary:hover { background-color: #2c2c35; border-color: #2dd4bf; }
            #ViewportMonitor { background-color: #0d0d0f; border: 1px solid #2c2c35; border-radius: 8px; color: #2dd4bf; }
            #HologramScreen { background-color: #0d0d0f; border: 1px solid #232329; border-left: 4px solid #2dd4bf; color: #ffffff; font-size: 12px; font-family: 'Consolas', monospace; padding: 6px 12px; border-radius: 4px; }
        """)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        if self.is_lens_active: return
        for url in event.mimeData().urls():
            file_path = url.toLocalFile()
            ext = os.path.splitext(file_path)[1].lower()
            
            current_tab = self.tabs.currentIndex()
            if current_tab == 0:  
                self.cached_encoder_source_path = file_path
                self.btn_run_bake.setEnabled(True)
                self.lbl_drop_hint.setText(f"🎯 Target Loaded Successfully:\n\n{os.path.basename(file_path)}")
                self.lbl_enc_status.setText(f"Ready: {os.path.basename(file_path)}")
            elif current_tab == 1:  
                if ext in ['.smk', '.smp']:
                    self.target_svg_container = file_path
                    self.load_dropped_decoder_package()
                    
    def load_dropped_decoder_package(self):
        fp = self.target_svg_container
        self.txt_dec_key.clear()
        self.cached_is_valid_key = False
        try:
            with zipfile.ZipFile(fp, 'r') as smp:
                smk_name = next((name for name in smp.namelist() if name.endswith('.smk')), None)
                if not smk_name: raise FileNotFoundError("Structure Error: Meta control file missing.")
                smk_data = smp.read(smk_name)
                root = ET.fromstring(smk_data)

            node = next(n for n in root.iter() if n.tag.split('}')[-1] == 'smv_header')
            self.meta_header_cache = json.loads(node.get("value"))
            
            token_str = self.meta_header_cache.get("key_verification_token", "default_salt")
            self.base_seed_cache = int(int(hashlib.md5(token_str.encode()).hexdigest(), 16) % (2**31 - 1))
            
            self.lbl_3d_viewport.pitch = 0.0
            self.lbl_3d_viewport.yaw = 0.0
            self.render_viewport(0.0, 0.0, False, 0.0)
            self.lbl_hologram_screen.setText(f"📦 Resource loaded: {os.path.basename(fp)}\nEnter passkey to initialize geometric matrix.")
        except Exception as e:
            self.lbl_hologram_screen.setText(f"Analysis Failure: {e}")

    def select_source_file(self):
        fp, _ = QFileDialog.getOpenFileName(
            self, 
            "Load Source File", 
            "", 
            "All Files (*.*);;Video Files (*.mp4 *.mkv *.avi *.hevc)"
        )
        if fp:
            self.cached_encoder_source_path = fp
            self.btn_run_bake.setEnabled(True)
            self.lbl_drop_hint.setText(f"🎯 Target Loaded Successfully:\n\n{os.path.basename(fp)}")
            self.lbl_enc_status.setText(f"Ready: {os.path.basename(fp)}")

    def execute_encoder_pipeline(self):
        if not self.txt_enc_key.text().strip() or not self.cached_encoder_source_path: return
        self.btn_run_bake.setEnabled(False)
        self.worker = UIStructureCryptWorker(self.cached_encoder_source_path, self.txt_enc_key.text())
        self.worker.status_signal.connect(self.lbl_enc_status.setText)
        self.worker.finished_signal.connect(self.on_encoder_finished)
        self.worker.start()

    def on_encoder_finished(self, result):
        self.btn_run_bake.setEnabled(True)
        self.lbl_enc_status.setText(result[1])

    def select_vector_container(self):
        if self.is_lens_active: return
        fp, _ = QFileDialog.getOpenFileName(self, "Open Secure Package File", "", "Package Target (*.smp)")
        if fp:
            self.target_svg_container = fp
            self.load_dropped_decoder_package()

    def generate_fixed_visual_offsets(self):
        if not self.meta_header_cache: return
        layers = self.meta_header_cache["mesh_3d_layers"]
        count = len(layers)
        rng = np.random.default_rng(self.base_seed_cache)
        self.visual_offsets = {
            "x": rng.uniform(-45.0, 45.0, count),
            "y": rng.uniform(-45.0, 45.0, count),
            "z": rng.uniform(-25.0, 25.0, count)
        }

    def on_master_key_typing(self, text):
        if self.is_lens_active: return
        if not self.meta_header_cache: return
        raw_key = text.strip()
        if raw_key:
            key_salt = int(hashlib.sha256(raw_key.encode()).hexdigest()[:6], 16) % 1000
            init_rng = np.random.default_rng(self.base_seed_cache + key_salt)
            self.lbl_3d_viewport.pitch = float(init_rng.uniform(30.0, 65.0)) * (1 if init_rng.random() > 0.5 else -1)
            self.lbl_3d_viewport.yaw = float(init_rng.uniform(45.0, 130.0)) * (1 if init_rng.random() > 0.5 else -1)
            self.generate_fixed_visual_offsets()
            
            try:
                salt_bytes = bytes.fromhex(self.meta_header_cache["salt"])
                expected_token = self.meta_header_cache.get("key_verification_token", "").strip()
                h1 = ui.PBKDF_heavy_derive(raw_key, salt_bytes, iterations=600000).hex()
                h2 = hashlib.sha256(h1.encode('utf-8')).hexdigest()
                self.cached_is_valid_key = (h2 == expected_token) and (expected_token != "")
                
                if self.cached_is_valid_key:
                    geom = ui.derive_dynamic_matrix_params(raw_key, salt_bytes)
                    self.local_target_pitch = geom["init_pitch"]
                    self.local_target_yaw = geom["init_yaw"]
            except Exception:
                self.cached_is_valid_key = False
        else:
            self.lbl_3d_viewport.pitch = 0.0
            self.lbl_3d_viewport.yaw = 0.0
            self.visual_offsets = None 
            self.cached_is_valid_key = False
            
        if isValid(self.lbl_angle_indicator):
            self.lbl_angle_indicator.setText(f"Spatial Matrix Offset -> Pitch: {self.lbl_3d_viewport.pitch:.1f}° | Yaw: {self.lbl_3d_viewport.yaw:.1f}°")
        self.render_viewport(self.lbl_3d_viewport.pitch, self.lbl_3d_viewport.yaw, False, 0.0)

    def on_cube_viewport_rotated(self, pitch, yaw, is_dragging, speed):
        if self.is_lens_active: return
        if isValid(self.lbl_angle_indicator):
            self.lbl_angle_indicator.setText(f"Spatial Matrix Offset -> Pitch: {pitch:.1f}° | Yaw: {yaw:.1f}°")
        self.render_viewport(pitch, yaw, is_dragging, speed)

    def update_lens_animation(self):
        self.lens_time += 0.1
        if self.lens_time >= math.pi: 
            self.lens_timer.stop()
            self.is_lens_active = False
            self.lens_time = 0.0
            
            result = self.last_decryption_result
            if result and result[2]:
                QMessageBox.information(self, "Success", result[1])
            else:
                msg = result[1] if result else "Decryption Failed."
                QMessageBox.warning(self, "Failed", msg)

            self.lbl_3d_viewport.pitch = 0.0
            self.lbl_3d_viewport.yaw = 0.0
            self.txt_dec_key.clear()
            self.cached_is_valid_key = False
            if isValid(self.lbl_angle_indicator):
                self.lbl_angle_indicator.setText("Spatial Matrix Offset -> Pitch: 0.0° | Yaw: 0.0°")
            self.render_viewport(0.0, 0.0, False, 0.0)
        else:
            self.render_viewport(self.lbl_3d_viewport.pitch, self.lbl_3d_viewport.yaw, False, 0.0)

    def render_viewport(self, pitch, yaw, is_dragging, speed):
        if not self.meta_header_cache: return
        w, h = 450, 450
        
        base_canvas = QImage(w, h, QImage.Format_RGB32)
        base_canvas.fill(QColor("#0d0d0f"))
        cx, cy = w // 2, h // 2

        with QPainter(base_canvas) as painter:
            painter.setRenderHint(QPainter.Antialiasing, True)

            if not self.is_unlocked() and not self.is_lens_active:
                self.lbl_3d_viewport.pitch = 0.0
                self.lbl_3d_viewport.yaw = 0.0
                if isValid(self.lbl_hologram_screen):
                    self.lbl_hologram_screen.setText("🔒 Locked - Decryption passkey verification token required.")
                del painter
                self.lbl_3d_viewport.setPixmap(QPixmap.fromImage(base_canvas))
                return

            is_valid_key = self.cached_is_valid_key
            visual_target_pitch = 0.0
            visual_target_yaw = 0.0
            dist = math.sqrt((pitch - visual_target_pitch)**2 + (yaw - visual_target_yaw)**2)

            orig_target_rows = self.meta_header_cache.get("target_rows", 5) 
            orig_target_cols = self.meta_header_cache.get("target_cols", 5)

            if is_valid_key:
                K_rows = orig_target_rows
                K_cols = orig_target_cols
            else:
                K_rows = orig_target_rows + 2
                K_cols = max(3, orig_target_cols - 1)

            if dist < 1.0:
                attenuation = 0.0
                if isValid(self.lbl_hologram_screen) and not self.is_lens_active:
                    self.lbl_hologram_screen.setText("🎯 [Aligned] Spatial coordinates matches perfectly. Ready to restore.")
            else:
                attenuation = min(2.5, dist / 25.0)
                if isValid(self.lbl_hologram_screen) and not self.is_lens_active:
                    self.lbl_hologram_screen.setText(f"🔄 Matrix Distortion Active... Radius Error: {dist:.1f}°")

            if self.visual_offsets is None:
                self.generate_fixed_visual_offsets()

            rad_p, rad_y = math.radians(pitch), math.radians(yaw)
            
            layers_dict = {}
            for idx, pt in enumerate(self.meta_header_cache["mesh_3d_layers"]):
                layer_idx = pt["layer"]
                if layer_idx not in layers_dict:
                    layers_dict[layer_idx] = []
                layers_dict[layer_idx].append((idx, pt))

            sophisticated_stars = [
                QColor("#ffffff"), QColor("#f4f5fa"), QColor("#ffeed0"), QColor("#ffdfa9"),
                QColor("#ffd5e3"), QColor("#ebdfff"), QColor("#cbeeff"), QColor("#aae3e8")
            ]

            for layer_idx, nodes in layers_dict.items():
                num_nodes = len(nodes)
                side = int(math.sqrt(num_nodes) + 0.5)
                for current_node_pos, (global_idx, pt) in enumerate(nodes):
                    orig_ky = current_node_pos // side if side > 0 else 0
                    orig_kx = current_node_pos % side if side > 0 else 0
                    norm_x = orig_kx / (side - 1) if side > 1 else 0.5
                    norm_y = orig_ky / (side - 1) if side > 1 else 0.5
                    
                    corrected_target_x = float((norm_x - 0.5) * (K_cols * 55))
                    corrected_target_y = float((norm_y - 0.5) * (K_rows * 55))
                    
                    ox = self.visual_offsets["x"][global_idx] * attenuation
                    oy = self.visual_offsets["y"][global_idx] * attenuation
                    oz = self.visual_offsets["z"][global_idx] * attenuation
                    
                    bx = pt["base_x"] * attenuation + corrected_target_x * (1.0 - attenuation)
                    by = pt["base_y"] * attenuation + corrected_target_y * (1.0 - attenuation)

                    x = bx + ox
                    y = by + oy
                    z = pt["base_z"] + oz
                    
                    x1 = x * math.cos(rad_y) - z * math.sin(rad_y)
                    y2 = y * math.cos(rad_p) - (x * math.sin(rad_y) + z * math.cos(rad_y)) * math.sin(rad_p)
                    px_i, py_i = int(cx + x1), int(cy + y2)
                    
                    if 0 <= px_i < w and 0 <= py_i < h:
                        color_select_idx = (global_idx * 13 + layer_idx * 7) % len(sophisticated_stars)
                        star_color = sophisticated_stars[color_select_idx]

                        glow_brush = QBrush(QColor(star_color.red(), star_color.green(), star_color.blue(), 16))
                        painter.setPen(Qt.NoPen)
                        painter.setBrush(glow_brush)
                        painter.drawEllipse(QPoint(px_i, py_i), 4.5, 4.5)

                        glow_pen = QPen(QColor(star_color.red(), star_color.green(), star_color.blue(), 40), 1)
                        painter.setPen(glow_pen)
                        painter.drawLine(px_i - 4, py_i, px_i + 4, py_i)
                        painter.drawLine(px_i, py_i - 4, px_i, py_i + 4)
                        
                        painter.setPen(Qt.NoPen)
                        painter.setBrush(QBrush(star_color))
                        r_size = max(1.1, min(2.4, 1.5 + (z / 160.0)))
                        painter.drawEllipse(QPoint(px_i, py_i), r_size, r_size)

            if is_dragging and speed > 0.0 and dist > 0.1 and not self.is_lens_active:
                start_x, start_y = cx, cy
                vector_len = min(65.0, 25.0 + dist * 0.4)
                dp = visual_target_pitch - pitch
                dy_a = visual_target_yaw - yaw
                v_dist = math.sqrt(dp**2 + dy_a**2) if (dp**2 + dy_a**2) > 0 else 1.0
                end_x = start_x + ((dy_a / v_dist) * vector_len)
                end_y = start_y - ((dp / v_dist) * vector_len)
                
                arrow_color = QColor("#C8D8E0")
                arrow_color.setAlpha(160) 
                painter.setPen(QPen(arrow_color, 1.5, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
                painter.setBrush(QBrush(arrow_color))
                painter.drawLine(int(start_x), int(start_y), int(end_x), int(end_y))
                
                angle = math.atan2(end_y - start_y, end_x - start_x)
                arrow_size = 6
                p1 = QPoint(int(end_x), int(end_y))
                p2 = QPoint(int(end_x - arrow_size * math.cos(angle - math.pi / 6)), int(end_y - arrow_size * math.sin(angle - math.pi / 6)))
                p3 = QPoint(int(end_x - arrow_size * math.cos(angle + math.pi / 6)), int(end_y - arrow_size * math.sin(angle + math.pi / 6)))
                painter.drawPolygon(QPolygon([p1, p2, p3]))

        if self.is_lens_active:
            ptr = base_canvas.bits()
            base_rgba = np.frombuffer(ptr, dtype=np.uint8).reshape((h, w, 4))
            final_rgba = np.full((h, w, 4), [15, 15, 13, 255], dtype=np.uint8)
            sin_val = math.sin(self.lens_time)
            current_mass = 0.15 * sin_val  
            bh_radius = 0.15 * sin_val     
            
            res_y = float(h)
            y_indices = np.arange(h)
            x_indices = np.arange(w)
            
            gl_y = h - 1 - y_indices
            p_y = (gl_y - h * 0.5) / res_y
            p_x = (x_indices - w * 0.5) / res_y
            
            P_X, P_Y = np.meshgrid(p_x, p_y)
            
            lens_norm_x = (float(cx) - w * 0.5) / res_y
            lens_norm_y = (float(cy) - h * 0.5) / res_y
            
            R_X = P_X - lens_norm_x
            R_Y = P_Y - lens_norm_y
            DIST_SQ = R_X * R_X + R_Y * R_Y
            DIST = np.sqrt(DIST_SQ)
            
            FACTOR = current_mass / (DIST_SQ + 0.004)
            OFFSET_X = R_X * FACTOR
            OFFSET_Y = R_Y * FACTOR
            
            SRC_P_X = P_X - OFFSET_X
            SRC_P_Y = P_Y - OFFSET_Y
            
            THETA = FACTOR * 0.6
            COS_T = np.cos(THETA)
            SIN_T = np.sin(THETA)
            
            S_X = SRC_P_X - lens_norm_x
            S_Y = SRC_P_Y - lens_norm_y
            
            ROT_SRC_P_X = lens_norm_x + (S_X * COS_T - S_Y * SIN_T)
            ROT_SRC_P_Y = lens_norm_y + (S_X * SIN_T + S_Y * COS_T)
            
            SRC_X = (ROT_SRC_P_X * res_y + w * 0.5).astype(np.int32)
            SRC_GL_Y = ROT_SRC_P_Y * res_y + h * 0.5
            SRC_Y = (h - 1 - SRC_GL_Y).astype(np.int32)
            
            SRC_X_CLIPPED = np.clip(SRC_X, 0, w - 1)
            SRC_Y_CLIPPED = np.clip(SRC_Y, 0, h - 1)
            
            valid_mask = (SRC_X >= 0) & (SRC_X < w) & (SRC_Y >= 0) & (SRC_Y < h)
            blackhole_mask = DIST < bh_radius
            
            final_rgba[valid_mask] = base_rgba[SRC_Y_CLIPPED[valid_mask], SRC_X_CLIPPED[valid_mask]]
            final_rgba[~valid_mask] = [13, 13, 15, 255]
            final_rgba[blackhole_mask] = [0, 0, 0, 255]
            
            final_canvas = QImage(final_rgba.data, w, h, QImage.Format_RGB32)
            self.lbl_3d_viewport.setPixmap(QPixmap.fromImage(final_canvas).copy())
        else:
            self.lbl_3d_viewport.setPixmap(QPixmap.fromImage(base_canvas))

    def force_trigger_decoding(self):
        if self.is_lens_active: return
        if not self.target_svg_container or not self.txt_dec_key.text().strip(): 
            QMessageBox.warning(self, "Warning", "Please provide a valid file and passkey.")
            return

        raw_key = self.txt_dec_key.text().strip()
        target_pitch = self.local_target_pitch if self.cached_is_valid_key else 999.0
        target_yaw = self.local_target_yaw if self.cached_is_valid_key else 999.0
        
        real_pitch = target_pitch + self.lbl_3d_viewport.pitch
        real_yaw = target_yaw + self.lbl_3d_viewport.yaw
            
        self.dec_real_worker = UIStructureDecryptWorker(
            svg_container_path=self.target_svg_container, 
            raw_key_text=raw_key, 
            current_pitch=real_pitch,  
            current_yaw=real_yaw
        )
        self.dec_real_worker.status_signal.connect(self.lbl_hologram_screen.setText)
        self.dec_real_worker.finished_signal.connect(self.on_decryption_finished)
        self.dec_real_worker.start()

    def on_decryption_finished(self, result):
        self.last_decryption_result = result
        self.is_lens_active = True
        self.lens_time = 0.0
        self.lens_timer.start()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    win = IntegratedSpatioTemporalApp()
    win.show()
    sys.exit(app.exec())