import os
import json
import hashlib  
import secrets  
import platform
import subprocess
import xml.etree.ElementTree as ET
import numpy as np
from Crypto.Cipher import AES
from Crypto.Protocol.KDF import HKDF
from Crypto.Hash import SHA256
import re
import sys
import time
import shutil  
import zipfile
import math
import tempfile

FILE_BLOCK_CHUNK_SIZE = 4096  

def get_local_ffmpeg_path():
    ext = ".exe" if platform.system() == "Windows" else ""
    if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
        path = os.path.join(sys._MEIPASS, "ffmpeg", "bin", f"ffmpeg{ext}")
        if os.path.exists(path): return path
    if getattr(sys, 'frozen', False):
        project_root_dir = os.path.dirname(sys.executable)
    else:
        current_code_dir = os.path.dirname(os.path.abspath(__file__))
        project_root_dir = os.path.dirname(current_code_dir)
    path = os.path.join(project_root_dir, "ffmpeg", "bin", f"ffmpeg{ext}")
    if os.path.exists(path): return path
    return f"ffmpeg{ext}"

def get_optimal_hardware_acceleration(ffmpeg_bin, creation_flag):
    c_flag = creation_flag if creation_flag is not None else 0
    cmd = [ffmpeg_bin, "-encoders"]
    try:
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=c_flag)
        encoders_text = res.stdout.decode('utf-8', errors='ignore')
    except Exception:
        encoders_text = ""
    if "hevc_qsv" in encoders_text:
        return {"type": "INTEL_H265", "vcodec": ["-vcodec", "hevc_qsv", "-global_quality", "25", "-g", "120", "-bf", "4"]}
    if "hevc_nvenc" in encoders_text:
        return {"type": "NVIDIA_H265", "vcodec": ["-vcodec", "hevc_nvenc", "-cq", "22", "-preset", "p4", "-g", "120", "-bf", "4"]}
    return {"type": "CPU_H265", "vcodec": ["-vcodec", "libx265", "-crf", "24", "-preset", "faster", "-g", "120", "-bf", "4"]}

def PBKDF_heavy_derive(raw_key_text, salt_bytes, iterations=600000):
    if not isinstance(salt_bytes, bytes) or len(salt_bytes) < 16:
        raise ValueError("Cryptographic error: Salt must be at least 16 bytes long.")
    return hashlib.pbkdf2_hmac(
        hash_name='sha256', password=raw_key_text.encode('utf-8'), salt=salt_bytes, iterations=iterations, dklen=32
    )

def derive_dynamic_matrix_params(raw_key_text, salt_bytes):
    key_hash = PBKDF_heavy_derive(raw_key_text, salt_bytes, iterations=600000)
    seed = int.from_bytes(key_hash[:8], 'big')
    rng = np.random.default_rng(seed)
    return {
        "chunks": int(rng.integers(4, 9)),
        "target_rows": int(rng.integers(3, 7)),
        "target_cols": int(rng.integers(3, 7)),
        "init_pitch": float(rng.uniform(-60.0, 60.0)),
        "init_yaw": float(rng.uniform(-90.0, 90.0)),
        "target_w": 450, "target_h": 450, "seed": seed 
    }

def get_geometric_secret_subkey(raw_key_text, salt_bytes, pitch, yaw, layer_density_map=None, stage_label="UNKNOWN"):
    grid_pitch = int(round(pitch))
    grid_yaw = int(round(yaw))
    base_key = PBKDF_heavy_derive(raw_key_text, salt_bytes, iterations=600000)
    info_str = f"P{grid_pitch}_Y{grid_yaw}"
    if layer_density_map:
        for layer_idx in sorted(layer_density_map.keys()):
            info_str += f"_L{layer_idx}_D{layer_density_map[layer_idx]}"
    derived_key = HKDF(base_key, 32, salt_bytes, SHA256, context=info_str.encode('utf-8'))
    return derived_key

def execute_encryption_pipeline(file_path, raw_key_text, status_callback=None, progress_callback=None):
    ffmpeg_bin = get_local_ffmpeg_path()
    if not os.path.exists(file_path): return None, "Initialization Error: File not found."

    if status_callback: status_callback("Step [1/5]: Generating secure key map...")
    salt_bytes = secrets.token_bytes(16)    
    geom = derive_dynamic_matrix_params(raw_key_text, salt_bytes)
    
    base_dir = os.path.dirname(file_path)
    raw_base_name = os.path.splitext(os.path.basename(file_path))[0]
    base_name = raw_base_name.replace("_secure", "").replace("_restored", "")
    orig_ext = os.path.splitext(os.path.basename(file_path))[1]
    
    output_smp_path = os.path.join(base_dir, f"{base_name}.smp").replace('\\', '/')
    is_general_file = orig_ext.lower() not in ['.mp4', '.mkv', '.avi', '.hevc', '.mov']

    with tempfile.TemporaryDirectory(prefix="smp_enc_") as workspace_dir:
        workspace_dir = workspace_dir.replace('\\', '/')
        temp_smk_path = os.path.join(workspace_dir, f"{base_name}.smk").replace('\\', '/')
        temp_smv_path = os.path.join(workspace_dir, f"{base_name}.smv").replace('\\', '/')
        temp_aud_path = os.path.join(workspace_dir, f"{base_name}.aac").replace('\\', '/')
        creation_flag = subprocess.CREATE_NO_WINDOW if platform.system() == "Windows" else 0
        
        if is_general_file:
            if status_callback: status_callback("Step [2/5]: Packaging general file data...")
            with open(file_path, "rb") as f_src:
                target_bytes = f_src.read()
            has_audio = False
            orig_fps = 30.0
        else:
            hw_config = get_optimal_hardware_acceleration(ffmpeg_bin, creation_flag)
            prob_cmd = [ffmpeg_bin, "-i", file_path]
            prob_res = subprocess.run(prob_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=creation_flag)
            prob_log = prob_res.stderr.decode('utf-8', errors='ignore')
            
            fps_match = re.search(r"(\d+(?:\.\d+)?)\s*fps", prob_log)
            orig_fps = float(fps_match.group(1)) if fps_match else 30.0
            is_already_hevc = "Video: hevc" in prob_log or "Stream #0:0: Video: hevc" in prob_log
            
            if status_callback: status_callback("Step [2/5]: Optimizing video container...")
            temp_compressed_mp4 = os.path.join(workspace_dir, "temp_comp.mp4").replace('\\', '/')
            
            if is_already_hevc:
                comp_cmd = [ffmpeg_bin, "-y", "-i", file_path, "-vcodec", "copy", "-an", "-movflags", "faststart", temp_compressed_mp4]
                subprocess.run(comp_cmd, creationflags=creation_flag)
            else:
                comp_cmd = [ffmpeg_bin, "-y", "-i", file_path, "-threads", "0"] + hw_config["vcodec"] + ["-an", "-movflags", "faststart", temp_compressed_mp4]
                process_comp = subprocess.run(comp_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=creation_flag)
                if process_comp.returncode != 0:
                    comp_cmd_cpu = [ffmpeg_bin, "-y", "-i", file_path, "-threads", "0", "-vcodec", "libx265", "-crf", "24", "-preset", "faster", "-an", "-movflags", "faststart", temp_compressed_mp4]
                    subprocess.run(comp_cmd_cpu, creationflags=creation_flag)

            if status_callback: status_callback("Step [3/5]: Extracting audio tracks...")
            audio_cmd = [ffmpeg_bin, "-y", "-i", file_path, "-map", "0:a?", "-vn", "-acodec", "aac", "-b:a", "192k", temp_aud_path]
            subprocess.run(audio_cmd, creationflags=creation_flag)
            has_audio = os.path.exists(temp_aud_path) and os.path.getsize(temp_aud_path) > 100

            if status_callback: status_callback("Step [4/5]: Encrypting data blocks...")
            extract_h265_cmd = [ffmpeg_bin, "-y", "-i", temp_compressed_mp4, "-vcodec", "copy", "-f", "hevc", "pipe:1"]
            process_pipe = subprocess.Popen(extract_h265_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=creation_flag)
            target_bytes, _ = process_pipe.communicate()

        comp_size = len(target_bytes)
        num_chunks = int(np.ceil(comp_size / FILE_BLOCK_CHUNK_SIZE))
        padded_size = num_chunks * FILE_BLOCK_CHUNK_SIZE
        padded_bytes = target_bytes + b'\x00' * (padded_size - comp_size)
        
        init_seed_key = int.from_bytes(hashlib.sha256(PBKDF_heavy_derive(raw_key_text, salt_bytes, iterations=600000)).digest()[:8], 'big')
        rng = np.random.default_rng(init_seed_key)

        rad_p = math.radians(geom["init_pitch"])
        rad_y = math.radians(geom["init_yaw"])
        mesh_3d_layers = []
        layer_density_map = {} 
        
        for z_idx in range(geom["chunks"]):
            z_depth = (z_idx - (geom["chunks"] - 1) / 2.0) * 45.0
            layer_rows = int(rng.integers(3, 10))
            layer_cols = int(rng.integers(3, 10))
            node_count = 0
            for ky in range(layer_rows):
                for kx in range(layer_cols):
                    rand_offset_x = float(rng.uniform(-15.0, 15.0))
                    rand_offset_y = float(rng.uniform(-15.0, 15.0))
                    rand_offset_z = float(rng.uniform(-5.0, 5.0))
                    target_x = float((kx - layer_cols/2.0) * 60 + rand_offset_x) * math.cos(rad_p)
                    target_y = float((ky - layer_rows/2.0) * 60 + rand_offset_y) * math.cos(rad_y)
                    mesh_3d_layers.append({
                        "base_x": float((kx - layer_cols/2.0) * 60), "base_y": float((ky - layer_rows/2.0) * 60), "base_z": float(z_depth),
                        "offset_x": rand_offset_x, "offset_y": rand_offset_y, "offset_z": rand_offset_z,
                        "target_x": target_x, "target_y": target_y, "layer": z_idx
                    })
                    node_count += 1
            layer_density_map[z_idx] = node_count

        crypto_subkey = get_geometric_secret_subkey(raw_key_text, salt_bytes, geom["init_pitch"], geom["init_yaw"], layer_density_map, stage_label="ENCRYPTION")
        
        init_seed = int.from_bytes(hashlib.sha256(crypto_subkey).digest()[:8], 'big')
        rng_shuffle = np.random.default_rng(init_seed)
        shuffled_indices = rng_shuffle.permutation(num_chunks)

        chunks_array = np.frombuffer(padded_bytes, dtype=np.uint8).reshape(num_chunks, FILE_BLOCK_CHUNK_SIZE)
        shuffled_chunks = chunks_array[shuffled_indices]

        session_nonce = secrets.token_bytes(12)
        cipher = AES.new(crypto_subkey, AES.MODE_GCM, nonce=session_nonce)
        encrypted_payload, auth_tag = cipher.encrypt_and_digest(shuffled_chunks.tobytes())

        with open(temp_smv_path, "wb") as f_bin:
            f_bin.write(encrypted_payload)

        aud_nonce, aud_tag = None, None
        if has_audio and not is_general_file:
            with open(temp_aud_path, "rb") as f_a: aud_raw = f_a.read()
            aud_nonce = secrets.token_bytes(12)
            aud_cipher = AES.new(crypto_subkey, AES.MODE_GCM, nonce=aud_nonce)
            aud_enc, aud_tag = aud_cipher.encrypt_and_digest(aud_raw)
            with open(temp_aud_path, "wb") as f_a_w: f_a_w.write(aud_enc)

        h1_enc = PBKDF_heavy_derive(raw_key_text, salt_bytes, iterations=600000).hex()
        h2_enc = hashlib.sha256(h1_enc.encode('utf-8')).hexdigest()
        
        kek = PBKDF_heavy_derive(raw_key_text, salt_bytes, iterations=600000)
        kek_nonce = secrets.token_bytes(12)
        kek_cipher = AES.new(kek[:32], AES.MODE_GCM, nonce=kek_nonce)
        
        secure_dict = {
            "v_nonce": session_nonce.hex(), "v_tag": auth_tag.hex(),
            "a_nonce": aud_nonce.hex() if has_audio else None, "a_tag": aud_tag.hex() if has_audio else None
        }
        enc_secure_payload, secure_payload_tag = kek_cipher.encrypt_and_digest(json.dumps(secure_dict).encode('utf-8'))

        meta_header = {
            "format_identifier": "QUANTUM-GCM-SPATIAL-v7-FFMPEG",
            "linked_binary_file": os.path.basename(temp_smv_path),
            "linked_audio_file": os.path.basename(temp_aud_path) if has_audio else None,
            "salt": salt_bytes.hex(), "key_verification_token": h2_enc,
            "compressed_size": comp_size, "original_extension": orig_ext, 
            "num_chunks": num_chunks, "encrypted_fps": orig_fps, 
            "mesh_3d_layers": mesh_3d_layers, "target_rows": geom["target_rows"], "target_cols": geom["target_cols"],
            "kek_nonce": kek_nonce.hex(), "kek_tag": secure_payload_tag.hex(), "secure_payload": enc_secure_payload.hex(),
            "is_general_file": is_general_file
        }

        svg_root = ET.Element("svg", {"xmlns": "http://www.w3.org/2000/svg", "width": "2048", "height": "2048"})
        meta_layer = ET.SubElement(svg_root, "metadata", {"id": "SpatioTemporalCoreData"})
        ET.SubElement(meta_layer, "smv_header", {"value": json.dumps(meta_header)})
        ET.ElementTree(svg_root).write(temp_smk_path, encoding="utf-8", xml_declaration=True)

        if status_callback: status_callback("Step [5/5]: Building final package...")
        with zipfile.ZipFile(output_smp_path, 'w', zipfile.ZIP_DEFLATED) as smp_pack:
            smp_pack.write(temp_smk_path, os.path.basename(temp_smk_path))
            smp_pack.write(temp_smv_path, os.path.basename(temp_smv_path))
            if has_audio: smp_pack.write(temp_aud_path, os.path.basename(temp_aud_path))

        return output_smp_path, f"Success: Package created -> {os.path.basename(output_smp_path)}"

def execute_decryption_pipeline(svg_container_path, raw_key_text, current_pitch, current_yaw, status_callback=None):
    try:
        if status_callback: status_callback("Processing [1/4]: Validating key maps...")
        ffmpeg_bin = get_local_ffmpeg_path()
        base_dir = os.path.dirname(svg_container_path)
        creation_flag = subprocess.CREATE_NO_WINDOW if platform.system() == "Windows" else 0
        base_filename = os.path.splitext(os.path.basename(svg_container_path))[0]

        with tempfile.TemporaryDirectory(prefix="smp_dec_") as workspace_dir:
            workspace_dir = workspace_dir.replace('\\', '/')
            if svg_container_path.endswith('.smp'):
                with zipfile.ZipFile(svg_container_path, 'r') as smp_pack: smp_pack.extractall(workspace_dir)
                real_smk_path = os.path.join(workspace_dir, next(f for f in os.listdir(workspace_dir) if f.endswith('.smk'))).replace('\\', '/')
                target_smv_dir = workspace_dir
            else:
                real_smk_path = svg_container_path
                target_smv_dir = base_dir
            
            tree = ET.parse(real_smk_path)
            root = tree.getroot()
            header_node = next(node for node in root.iter() if node.tag.split('}')[-1] == 'smv_header')
            meta_header = json.loads(header_node.get("value"))
            salt_bytes = bytes.fromhex(meta_header["salt"])

            try:
                kek = PBKDF_heavy_derive(raw_key_text, salt_bytes, iterations=600000)
                cipher_kek = AES.new(kek[:32], AES.MODE_GCM, nonce=bytes.fromhex(meta_header["kek_nonce"]))
                dec_payload = cipher_kek.decrypt_and_verify(bytes.fromhex(meta_header["secure_payload"]), bytes.fromhex(meta_header["kek_tag"]))
                secure_meta = json.loads(dec_payload.decode('utf-8'))
            except Exception:
                return None, "Decryption Error: Authentication failed. Invalid credentials.", False

            orig_ext = meta_header.get("original_extension", ".mp4")
            encrypted_fps = meta_header.get("encrypted_fps", 30.0)
            is_general_file = meta_header.get("is_general_file", False)
            mesh_3d_layers = meta_header.get("mesh_3d_layers", [])
            
            layer_density_map = {}
            for node in mesh_3d_layers:
                l_idx = node["layer"]
                layer_density_map[l_idx] = layer_density_map.get(l_idx, 0) + 1

            geom_target = derive_dynamic_matrix_params(raw_key_text, salt_bytes)
            if np.sqrt((current_pitch - geom_target["init_pitch"])**2 + (current_yaw - geom_target["init_yaw"])**2) < 1.5:
                effective_pitch, effective_yaw = geom_target["init_pitch"], geom_target["init_yaw"]
            else:
                effective_pitch, effective_yaw = current_pitch, current_yaw

            crypto_subkey = get_geometric_secret_subkey(raw_key_text, salt_bytes, effective_pitch, effective_yaw, layer_density_map, stage_label="DECRYPTION")
            target_smv_path = os.path.join(target_smv_dir, meta_header["linked_binary_file"]).replace('\\', '/')
            
            with open(target_smv_path, "rb") as f_bin: encrypted_payload = f_bin.read()
            v_nonce = bytes.fromhex(secure_meta["v_nonce"])
            v_tag = bytes.fromhex(secure_meta["v_tag"])

            if status_callback: status_callback("Processing [2/4]: Verifying security tokens...")
            try:
                cipher_verify = AES.new(crypto_subkey, AES.MODE_GCM, nonce=v_nonce)
                decrypted_bytes = cipher_verify.decrypt_and_verify(encrypted_payload, v_tag)
            except ValueError:
                return None, "Decryption Error: Authentication tag verification failed. Invalid credentials or angles.", False

            if status_callback: status_callback("Processing [3/4]: Reassembling data blocks...")
            num_chunks = meta_header["num_chunks"]
            compressed_size = meta_header["compressed_size"]

            init_seed = int.from_bytes(hashlib.sha256(crypto_subkey).digest()[:8], 'big')
            rng_shuffle = np.random.default_rng(init_seed)
            shuffled_indices = rng_shuffle.permutation(num_chunks)
            inv_indices = np.zeros_like(shuffled_indices)
            inv_indices[shuffled_indices] = np.arange(num_chunks)

            expected_len = num_chunks * FILE_BLOCK_CHUNK_SIZE
            decrypted_bytes = decrypted_bytes[:expected_len].ljust(expected_len, b'\x00')
            chunks_array = np.frombuffer(decrypted_bytes, dtype=np.uint8).reshape(num_chunks, FILE_BLOCK_CHUNK_SIZE)
            final_payload = chunks_array[inv_indices].tobytes()[:compressed_size]

            if is_general_file:
                output_restored_file = os.path.join(base_dir, f"{base_filename}_restored{orig_ext}").replace('\\', '/')
                if os.path.exists(output_restored_file): os.remove(output_restored_file)
                with open(output_restored_file, "wb") as f_out:
                    f_out.write(final_payload)
                return output_restored_file, "Restoration complete.", True

            temp_raw_path = os.path.join(workspace_dir, "temp_fast_raw.h265").replace('\\', '/')
            with open(temp_raw_path, "wb") as f_tmp: f_tmp.write(final_payload)

            has_audio = False
            temp_audio_path = os.path.join(workspace_dir, "temp_restored_audio.aac").replace('\\', '/')
            target_aud_name = meta_header.get("linked_audio_file")
            if target_aud_name and os.path.exists(os.path.join(target_smv_dir, target_aud_name)):
                with open(os.path.join(target_smv_dir, target_aud_name), "rb") as f_a: aud_file_bytes = f_a.read()
                try:
                    aud_cipher = AES.new(crypto_subkey, AES.MODE_GCM, nonce=bytes.fromhex(secure_meta["a_nonce"]))
                    dec_aud = aud_cipher.decrypt_and_verify(aud_file_bytes, bytes.fromhex(secure_meta["a_tag"]))
                    with open(temp_audio_path, "wb") as f_a_w: f_a_w.write(dec_aud)
                    has_audio = True
                except ValueError: pass

            if status_callback: status_callback("Processing [4/4]: Finalizing media container...")
            output_restored_path = os.path.join(base_dir, f"{base_filename}_restored{orig_ext}").replace('\\', '/')
            if os.path.exists(output_restored_path): os.remove(output_restored_path)
            
            temp_noaudio_container = os.path.join(workspace_dir, f"temp_noaudio{orig_ext}").replace('\\', '/')
            rebox_cmd = [ffmpeg_bin, "-y", "-fflags", "+genpts", "-f", "hevc", "-r", f"{encrypted_fps:.3f}", "-i", temp_raw_path, "-vcodec", "copy", "-movflags", "faststart", temp_noaudio_container]
            subprocess.run(rebox_cmd, creationflags=creation_flag)

            if has_audio:
                final_mux_cmd = [ffmpeg_bin, "-y", "-fflags", "+genpts", "-i", temp_noaudio_container, "-i", temp_audio_path, "-c:v", "copy", "-c:a", "copy", "-map", "0:v:0", "-map", "1:a:0?", "-async", "1", "-movflags", "faststart", output_restored_path]
            else:
                final_mux_cmd = [ffmpeg_bin, "-y", "-i", temp_noaudio_container, "-c", "copy", "-movflags", "faststart", output_restored_path]
            subprocess.run(final_mux_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=creation_flag)

            return output_restored_path, "Restoration complete.", True
    except Exception as e: 
        return None, f"Error: Process failed. {str(e)}", False