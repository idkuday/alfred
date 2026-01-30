"""
Script to verify STT endpoint functionality.
"""
import asyncio
import io
import wave
import struct
import math
import logging
from typing import BinaryIO

# Configure logging to verify server logs during test
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Mock dependencies to test main.py handlers directly? 
# Or start actual server?
# Starting actual server with uvicorn might be complex in a script.
# Let's use FastAPI TestClient which runs the app in-process.

from fastapi.testclient import TestClient
from ai_server.main import app

def generate_sine_wave_wav(duration_sec: float = 1.0) -> BinaryIO:
    """
    Generate a simple sine wave WAV file in memory.
    Whisper will likely transcribe this as silence or hallucinate,
    but it verifies the pipeline (upload -> process -> return).
    """
    sample_rate = 16000
    num_samples = int(sample_rate * duration_sec)
    
    # Create in-memory bytes buffer
    wav_buffer = io.BytesIO()
    
    with wave.open(wav_buffer, 'wb') as wav_file:
        wav_file.setnchannels(1) # Mono
        wav_file.setsampwidth(2) # 16-bit
        wav_file.setframerate(sample_rate)
        
        # Generate sine wave (440Hz A4)
        for i in range(num_samples):
            value = int(32767.0 * 0.5 * math.sin(2.0 * math.pi * 440.0 * i / sample_rate))
            wav_file.writeframes(struct.pack('<h', value))
            
    wav_buffer.seek(0)
    return wav_buffer

def test_transcription_endpoint():
    print("Initializing TestClient (triggers lifespan startup)...")
    # Using TestClient context manager to trigger lifespan events (startup)
    with TestClient(app) as client:
        print("Client initialized. Loading real audio file (jfk.wav)...")
        
        # 1. Load real audio
        try:
            with open("jfk.wav", "rb") as f:
                audio_data = f.read()
        except FileNotFoundError:
            print("❌ ERROR: jfk.wav not found. Please download it first.")
            return
        
        # 2. Upload to /transcribe
        print("Sending request to /transcribe...")
        files = {'file': ('jfk.wav', audio_data, 'audio/wav')}
        
        response = client.post("/transcribe", files=files)
        
        # 3. Output results
        print(f"Status Code: {response.status_code}")
        # print(f"Response JSON: {response.json()}")
        
        if response.status_code == 200:
            print("✅ SUCECSS: Endpoint returned 200 OK")
            print("--------------------------------------------------")
            print(f"TRANSCRIBED TEXT:\n{response.json().get('text')}")
            print("--------------------------------------------------")
        else:
            print(f"❌ FAILED: {response.text}")

if __name__ == "__main__":
    # Check if faster-whisper is installed first
    try:
        import faster_whisper
        print("faster-whisper detected.")
        test_transcription_endpoint()
    except ImportError:
        print("faster-whisper not installed. Skipping test.")
        
