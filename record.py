import subprocess
import shlex
import os
import signal
import sys
import datetime
import time
import threading

def execute_command(command):
    """Helper function to execute shell commands."""
    process = subprocess.Popen(command, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    stdout, stderr = process.communicate()
    return stdout, stderr, process.returncode

def create_virtual_sink():
    """Create a virtual sink and return its name and module ID."""
    command = 'pactl load-module module-null-sink sink_name=VirtualSink sink_properties=device.description=VirtualSink'
    stdout, stderr, code = execute_command(command)
    if code != 0:
        print(f"Error creating virtual sink: {stderr}")
        sys.exit(1)
    return 'VirtualSink', stdout.strip()

"""
def find_stream_state(app_name):
    stream_id=None
    state=None
    command = f'pw-cli ls Node | grep -B5 {app_name} | grep -oP "id \\d+" | awk \'{{print $2}}\''
    stdout, stderr, code = execute_command(command)
    if code != 0 or not stdout.strip():
        return None, None
    else:
        print(f"Stream ID for '{app_name}' found: {stdout.strip()}")
        stream_id = stdout.strip()

    command = f'pw-cli info {stream_id} | grep -oE \'state: "[^"]+"\' | awk \'{{print $2}}\' | sed \'s/"//g\''
    stdout, stderr, code = execute_command(command)
    if code != 0 or not stdout.strip():
        return stream_id, None
    else:
        print(f"State of '{app_name}' found: {stdout.strip()}")
        state = stdout.strip()

    return stream_id, state
"""
def find_stream_state(app_name):
    """Find the input stream ID of the given application."""
    # Ensure the app_name is properly quoted
    quoted_app_name = shlex.quote(app_name)
    stream_id = None
    state = None

    # Command to find the stream ID
    command = f'pw-cli ls Node | grep -B5 {quoted_app_name} | grep -oP "id \\d+" | awk \'{{print $2}}\' | head -n 1'
    command = command.replace('\n','')
    print(f"Find Command: {command}")
    stdout, stderr, code = execute_command(command)
    if code != 0 or not stdout.strip():
        return None, None
    else:
        stream_id = stdout.strip().replace('\n','')
        print(f"Stream ID for '{app_name}' found: {stream_id}")

    # Command to find the stream state
    command = f'pw-cli info {stream_id} | grep -oE \'state: "[^"]+"\' | awk \'{{print $2}}\' | sed \'s/"//g\''
    command = command.replace('\n','')
    print(f"Find State Command: {command}")
    stdout, stderr, code = execute_command(command)
    if code != 0 or not stdout.strip():
        return stream_id, None
    else:
        state = stdout.strip().replace('\n','')
        print(f"State of '{app_name}' found: {state} END")

    return stream_id, state

def record_from_sink(filename, stop_event):
    """Record audio from the virtual sink."""
    command = f"parec -d VirtualSink.monitor --file-format=wav --record > {filename}"
    print(f"Recording audio to {filename}. Press Ctrl+C to stop recording.")

    process = subprocess.Popen(command, shell=True, preexec_fn=os.setsid)
    try:
        while not stop_event.is_set():
            if process.poll() is not None:
                break
            time.sleep(1)
    finally:
        if process.poll() is None:  # If the process is still running
            print(f"Terminating recording process for {filename}.")
            os.killpg(os.getpgid(process.pid), signal.SIGTERM)

def connect_stream_to_sink(stream_id):
    """Connect source stream to virtual sink."""
    if stream_id is None:
        print("No stream ID to connect to the virtual sink.")
        return
    command = f"pw-link {stream_id} VirtualSink"
    print(f"Command: {command}")
    stdout, stderr, code = execute_command(command)
    if code != 0:
        print(f"Error connecting {stream_id} to VirtualSink: {stderr}")
        sys.exit(1)

def unload_virtual_sink(module_id):
    """Unload the virtual sink module."""
    command = f'pactl unload-module {module_id}'
    stdout, stderr, code = execute_command(command)
    if code != 0:
        print(f"Error unloading virtual sink: {stderr}")

def monitor_stream(app_name, stop_event):
    """Monitor the state of the stream and trigger stop event if it's idle."""
    while not stop_event.is_set():
        _, state = find_stream_state(app_name)
        if state == "idle":
            print("Stream is idle. Stopping recording.")
            stop_event.set()
            break
        time.sleep(5)

def unlink_stream_from_sink(stream_id):
    """Unlink source stream from virtual sink."""
    command = f"pw-link --disconnect {stream_id} VirtualSink"
    stdout, stderr, code = execute_command(command)
    if code != 0:
        print(f"Error unlinking {stream_id} from VirtualSink: {stderr}")

def main():
    if len(sys.argv) != 2:
        print("Usage: python script.py <application_name>")
        sys.exit(1)

    app_name = sys.argv[1]

    # Step 1: Create virtual sink
    sink_name, module_id = create_virtual_sink()

    try:
        while True:
            # Loop until the application's stream is detected
            while True:
                stream_id, state = find_stream_state(app_name)
                if stream_id and state != "idle":
                    # Step 2: Connect app stream to the virtual sink
                    connect_stream_to_sink(stream_id)
                    break

                print(f"No active stream found for '{app_name}' or state is idle. Checking again in 5 seconds...")
                time.sleep(5)

            # Step 3: Start recording and set up monitoring
            filename = f"{app_name}_{datetime.datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.wav"
            filename = filename.replace(' ','_')
            stop_event = threading.Event()

            # Start stream monitoring in a separate thread
            monitor_thread = threading.Thread(target=monitor_stream, args=(app_name, stop_event))
            monitor_thread.start()

            # Record while monitoring
            record_from_sink(filename, stop_event)
            
            # Ensure the monitor thread has finished
            monitor_thread.join()

            # Unlink the stream after recording
            unlink_stream_from_sink(stream_id)

            print(f"Recording to {filename} completed. Waiting for the stream to become active again.")

    finally:
        # Step 4: Clean up virtual sink
        unload_virtual_sink(module_id)

if __name__ == "__main__":
    main()
