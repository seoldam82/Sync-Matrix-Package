import os
import struct

def embed_file_into_mp4(cover_mp4_path: str, secret_file_path: str, output_mp4_path: str):
    with open(secret_file_path, "rb") as f:
        secret_data = f.read()
    secret_len = len(secret_data)
    
    ext_bytes = os.path.splitext(secret_file_path)[1].encode('utf-8')
    ext_len = len(ext_bytes)
    
    with open(cover_mp4_path, "rb") as f_cover:
        cover_data = f_cover.read()
        
    with open(output_mp4_path, "wb") as f_out:
        f_out.write(cover_data)
        f_out.write(secret_data) 
        f_out.write(ext_bytes)
        f_out.write(struct.pack(">II4s", ext_len, secret_len, b"STGO"))
        
def extract_file_from_mp4(generated_mp4_path: str, output_dir: str) -> str:
    generated_mp4_path = os.path.normpath(os.path.abspath(generated_mp4_path))
    output_dir = os.path.normpath(os.path.abspath(output_dir))
    
    os.makedirs(output_dir, exist_ok=True)

    with open(generated_mp4_path, "rb") as f:
        f.seek(-12, os.SEEK_END) 
        tail_bytes = f.read(12)
        if len(tail_bytes) < 12:
            raise ValueError("This is not a normal security package file or data corruption.")
            
        ext_len, secret_len, sig = struct.unpack(">II4s", tail_bytes)
        
        if sig != b"STGO":
            raise ValueError("Unique security signature (STGO) does not match.")
            
        f.seek(-12 - ext_len, os.SEEK_END)
        ext_bytes = f.read(ext_len)
        orig_ext = ext_bytes.decode('utf-8', errors='ignore')
        
        f.seek(-12 - ext_len - secret_len, os.SEEK_END)
        secret_data = f.read(secret_len)
        
    out_filename = f"extracted_raw_payload{orig_ext}"
    out_path = os.path.normpath(os.path.join(output_dir, out_filename))
    
    with open(out_path, "wb") as f_out:
        f_out.write(secret_data)
        
    return out_path.replace('\\', '/')