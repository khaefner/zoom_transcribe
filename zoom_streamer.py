import asyncio
import os
import subprocess
import requests
import time
from datetime import datetime
from wyoming.audio import AudioStart, AudioChunk, AudioStop
from wyoming.client import AsyncTcpClient
from wyoming.asr import Transcript

# Configuration
WHISPER_HOST = "192.168.106.249"
WHISPER_PORT = 10300
OLLAMA_API_URL = "http://192.168.106.249:11434/api/generate" 
LLM_MODEL = "qwen3.5:35b"
OUTPUT_DIR = os.path.expanduser("~/Documents/Zoom")

# Audio constants for Wyoming (16kHz, 16-bit, Mono)
RATE = 16000
WIDTH = 2
CHANNELS = 1
CHUNK_SIZE = 4096

def is_zoom_in_meeting():
    """Checks if Zoom is actively routing audio (indicating an active meeting)."""
    try:
        # Check for active playback streams (sink-inputs) and recording streams (source-outputs)
        sink_result = subprocess.run(['pactl', 'list', 'sink-inputs'], capture_output=True, text=True)
        source_result = subprocess.run(['pactl', 'list', 'source-outputs'], capture_output=True, text=True)
        
        # Zoom only creates these streams when a call is actively in progress
        is_playing = "ZOOM VoiceEngine" in sink_result.stdout
        is_recording = "ZOOM VoiceEngine" in source_result.stdout
        
        return is_playing or is_recording
    except FileNotFoundError:
        print("❌ 'pactl' command not found. Ensure pulseaudio-utils is installed.")
        return False

async def capture_and_transcribe():
    """Captures live audio via parec and streams to Wyoming while Zoom is active."""
    client = AsyncTcpClient(WHISPER_HOST, WHISPER_PORT)
    
    cmd = [
        "parec",
        "--format=s16le",
        "--rate=16000",
        "--channels=1",
        "--device=@DEFAULT_SINK@.monitor"
    ]
    
    print(f"🎙️ Active Zoom meeting detected! Capturing and streaming to {WHISPER_HOST}...")
    process = subprocess.Popen(cmd, stdout=subprocess.PIPE)
    
    transcript_text = ""
    
    try:
        async with client:
            await client.write_event(AudioStart(rate=RATE, width=WIDTH, channels=CHANNELS).event())
            
            # Read from parec stdout and stream to Wyoming
            while is_zoom_in_meeting():
                chunk = process.stdout.read(CHUNK_SIZE)
                if not chunk:
                    break
                    
                await client.write_event(
                    AudioChunk(audio=chunk, rate=RATE, width=WIDTH, channels=CHANNELS).event()
                )
                await asyncio.sleep(0.01) # Yield to event loop
            
            print("🛑 Zoom meeting ended. Stopping stream and waiting for transcription...")
            process.terminate()
            await client.write_event(AudioStop().event())

            # Wait for the transcript to process and return
            while True:
                event = await client.read_event()
                if event is None:
                    break
                if Transcript.is_type(event.type):
                    transcript_text = Transcript.from_event(event).text
                    break # Exit loop once we get the transcript
                    
    except Exception as e:
        print(f"❌ Error during streaming: {e}")
        if process.poll() is None:
            process.terminate()
            
    return transcript_text.strip()

def summarize_meeting(text):
    """Sends the transcript to Ollama for a structured summary."""
    print(f"🤖 Summarizing with {LLM_MODEL}...")
    prompt = (
        "Provide a professional meeting summary from this transcript. "
        "Include Executive Summary, Key Points, and Action Items.\n\n"
        f"Transcript: {text}"
    )
    
    try:
        response = requests.post(OLLAMA_API_URL, 
                                 json={"model": LLM_MODEL, "prompt": prompt, "stream": False})
        return response.json().get('response', '')
    except Exception as e:
        return f"Error during summary: {e}"

async def main():
    print("🔍 Listening for active Zoom meetings...")
    
    while True:
        if is_zoom_in_meeting():
            start_time = time.time()
            
            transcript_text = await capture_and_transcribe()
            
            if not transcript_text:
                print("⚠️ No transcription received or meeting was too short. Waiting for next session...")
                time.sleep(5)
                continue

            print(f"✅ Transcript received ({len(transcript_text)} characters)")
            
            summary = summarize_meeting(transcript_text)
            
            # Create the output directory if it doesn't exist
            os.makedirs(OUTPUT_DIR, exist_ok=True)
            
            # Save Output with a timestamp to the target directory
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"zoom_meeting_{timestamp}.txt"
            output_file = os.path.join(OUTPUT_DIR, filename)
            
            with open(output_file, "w") as f:
                f.write(f"TRANSCRIPT:\n{transcript_text}\n\nSUMMARY:\n{summary}")

            print(f"✨ Done! Total time: {time.time() - start_time:.1f}s")
            print(f"📄 Summary saved to: {output_file}")
            
            print("\n🔍 Resuming listening for active Zoom meetings...")
            
        # Poll every 5 seconds to minimize CPU usage
        time.sleep(5)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n👋 Exiting...")
