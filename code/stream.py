import os
import math
import struct
import numpy as np
import hashlib

def verify_user_key(raw_key_text: str, stored_hash: bytes, salt: bytes) -> bool:
    if not raw_key_text:
        return False
        
    cleaned = raw_key_text.strip()
    current_hash = hashlib.pbkdf2_hmac(
        'sha256',
        cleaned.encode('utf-8'),
        salt,
        iterations=100000 
    )
    
    import hmac
    return hmac.compare_digest(current_hash, stored_hash)

def generate_decoy_media_stream(workspace_dir: str, orig_ext: str) -> str:
    decoy_file_path = os.path.join(workspace_dir, f"temp_decoy_stream{orig_ext}").replace('\\', '/')
    nal_vps = b'\x00\x00\x00\x01\x40\x01\x0c\x01\xff\xff\x01\x40\x00\x00\x03\x00\x90\x00\x03\x00\x00\x03\x00\x5d\x95\x98\x09'
    nal_sps = b'\x00\x00\x00\x01\x42\x01\x01\x01\x60\x00\x00\x03\x00\x90\x00\x03\x00\x00\x03\x00\x5d\xa0\x02\x80\x80\x2d\x16\x59\x59\xa4\x93\x2b\x9a\x02\x00\x03\x00\x02\x00\x03\x00\x00\x03\x00\x02\x00\x78\x20'
    nal_pps = b'\x00\x00\x00\x01\x44\x01\xc0\xf7\xc0\xcc\x90'
    
    with open(decoy_file_path, "wb") as f:
        f.write(nal_vps)
        f.write(nal_sps)
        f.write(nal_pps)
        for i in range(120):
            frame_type = b'\x26\x01' if i % 30 == 0 else b'\x02\x01'
            payload = np.random.bytes(12800)
            f.write(b'\x00\x00\x00\x01' + frame_type + payload)
            
    return decoy_file_path