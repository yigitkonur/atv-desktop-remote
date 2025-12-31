#!/usr/bin/env python3
"""
Smoke test for pyatv-server sidecar binary.

Validates that the PyInstaller-built binary:
1. Starts successfully and emits 'ready' event
2. Responds to JSON-RPC health check
3. Has no ModuleNotFoundError or ImportError
4. Shuts down cleanly

Exit codes:
  0 - All tests passed
  1 - Binary failed to start
  2 - Health check failed
  3 - Module import error detected
  4 - Shutdown timeout
"""

import json
import os
import platform
import signal
import subprocess
import sys
import time
from pathlib import Path
from threading import Thread
from queue import Queue, Empty

# Configuration
STARTUP_TIMEOUT = 60  # seconds - PyInstaller extraction can be slow
HEALTH_TIMEOUT = 10   # seconds
SHUTDOWN_TIMEOUT = 5  # seconds


def get_binary_path() -> Path:
    """Get the platform-specific binary path."""
    system = platform.system().lower()
    machine = platform.machine().lower()
    
    # Architecture mapping
    arch_map = {
        'arm64': 'aarch64',
        'aarch64': 'aarch64',
        'x86_64': 'x86_64',
        'amd64': 'x86_64',
    }
    
    arch = arch_map.get(machine, 'x86_64')
    
    # Build target triple
    if system == 'darwin':
        target = f'{arch}-apple-darwin'
    elif system == 'linux':
        target = f'{arch}-unknown-linux-gnu'
    elif system == 'windows':
        target = 'x86_64-pc-windows-msvc'
    else:
        raise RuntimeError(f'Unsupported platform: {system}')
    
    binary_name = f'pyatv-server-{target}'
    if system == 'windows':
        binary_name += '.exe'
    
    # Check dist directory first (after build)
    script_dir = Path(__file__).parent
    dist_path = script_dir / 'dist' / binary_name
    
    if dist_path.exists():
        return dist_path
    
    # Check src-tauri/binaries (after copy)
    tauri_path = script_dir.parent / 'src-tauri' / 'binaries' / binary_name
    if tauri_path.exists():
        return tauri_path
    
    raise FileNotFoundError(
        f'Binary not found. Checked:\n'
        f'  - {dist_path}\n'
        f'  - {tauri_path}'
    )


def read_stderr_async(proc: subprocess.Popen, queue: Queue):
    """Read stderr in a separate thread to avoid blocking."""
    try:
        for line in iter(proc.stderr.readline, b''):
            if line:
                queue.put(line.decode('utf-8', errors='replace'))
    except Exception as e:
        queue.put(f'[STDERR READ ERROR] {e}')


def test_binary_startup(binary_path: Path) -> subprocess.Popen:
    """
    Test 1: Binary starts and emits 'ready' event.
    Returns the running process for further tests.
    """
    print(f'[TEST 1] Starting binary: {binary_path}')
    
    # Start the process
    proc = subprocess.Popen(
        [str(binary_path)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=0,  # Unbuffered
    )
    
    # Read stderr in background thread
    stderr_queue: Queue = Queue()
    stderr_thread = Thread(target=read_stderr_async, args=(proc, stderr_queue), daemon=True)
    stderr_thread.start()
    
    # Wait for 'ready' event
    start_time = time.time()
    stderr_lines = []
    ready_received = False
    
    while time.time() - start_time < STARTUP_TIMEOUT:
        # Check if process died
        if proc.poll() is not None:
            # Collect remaining stderr
            while not stderr_queue.empty():
                try:
                    stderr_lines.append(stderr_queue.get_nowait())
                except Empty:
                    break
            stderr_output = ''.join(stderr_lines)
            print(f'[FAIL] Process exited with code {proc.returncode}')
            print(f'[STDERR]\n{stderr_output}')
            sys.exit(1)
        
        # Check for ready event in stderr
        try:
            line = stderr_queue.get(timeout=0.5)
            stderr_lines.append(line)
            print(f'  [stderr] {line.rstrip()}')
            
            if 'ready' in line.lower() or '"ready"' in line:
                ready_received = True
                print('[PASS] Ready event received')
                break
        except Empty:
            pass
    
    if not ready_received:
        stderr_output = ''.join(stderr_lines)
        print(f'[FAIL] Timeout waiting for ready event ({STARTUP_TIMEOUT}s)')
        print(f'[STDERR]\n{stderr_output}')
        proc.terminate()
        sys.exit(1)
    
    # Store stderr lines for later analysis
    proc._stderr_lines = stderr_lines  # type: ignore
    proc._stderr_queue = stderr_queue  # type: ignore
    
    return proc


def test_health_check(proc: subprocess.Popen) -> None:
    """
    Test 2: Send JSON-RPC health request and validate response.
    """
    print('[TEST 2] Sending health check request')
    
    request = {
        'jsonrpc': '2.0',
        'method': 'health',
        'id': 1,
    }
    
    request_str = json.dumps(request) + '\n'
    proc.stdin.write(request_str.encode())
    proc.stdin.flush()
    
    # Read response from stdout
    start_time = time.time()
    response_line = None
    
    while time.time() - start_time < HEALTH_TIMEOUT:
        if proc.poll() is not None:
            print(f'[FAIL] Process died during health check')
            sys.exit(2)
        
        # Try to read from stdout
        try:
            # Use select on Unix, or polling on Windows
            import select
            if hasattr(select, 'select'):
                readable, _, _ = select.select([proc.stdout], [], [], 0.5)
                if readable:
                    response_line = proc.stdout.readline()
                    break
        except (ImportError, OSError):
            # Windows fallback - just try reading
            response_line = proc.stdout.readline()
            if response_line:
                break
            time.sleep(0.5)
    
    if not response_line:
        print(f'[FAIL] No response to health check within {HEALTH_TIMEOUT}s')
        sys.exit(2)
    
    # Parse response
    try:
        response = json.loads(response_line.decode('utf-8'))
        print(f'  [response] {json.dumps(response, indent=2)}')
        
        if 'result' in response:
            result = response['result']
            if result.get('status') == 'ok':
                print('[PASS] Health check returned status: ok')
            else:
                print(f'[WARN] Health check status: {result.get("status", "unknown")}')
        elif 'error' in response:
            print(f'[FAIL] Health check returned error: {response["error"]}')
            sys.exit(2)
        else:
            print(f'[WARN] Unexpected response format')
    except json.JSONDecodeError as e:
        print(f'[FAIL] Invalid JSON response: {e}')
        print(f'  Raw: {response_line}')
        sys.exit(2)


def test_no_import_errors(proc: subprocess.Popen) -> None:
    """
    Test 3: Check stderr for ModuleNotFoundError or ImportError.
    """
    print('[TEST 3] Checking for import errors')
    
    stderr_lines = getattr(proc, '_stderr_lines', [])
    stderr_queue = getattr(proc, '_stderr_queue', None)
    
    # Collect any remaining stderr
    if stderr_queue:
        while not stderr_queue.empty():
            try:
                stderr_lines.append(stderr_queue.get_nowait())
            except Empty:
                break
    
    stderr_output = ''.join(stderr_lines)
    
    error_patterns = [
        'ModuleNotFoundError',
        'ImportError',
        'No module named',
        'cannot import name',
    ]
    
    found_errors = []
    for pattern in error_patterns:
        if pattern in stderr_output:
            # Find the actual line with the error
            for line in stderr_lines:
                if pattern in line:
                    found_errors.append(line.strip())
    
    if found_errors:
        print('[FAIL] Import errors detected:')
        for err in found_errors:
            print(f'  - {err}')
        sys.exit(3)
    
    print('[PASS] No import errors detected')


def test_clean_shutdown(proc: subprocess.Popen) -> None:
    """
    Test 4: Process shuts down cleanly.
    """
    print('[TEST 4] Testing clean shutdown')
    
    # Send termination signal
    if platform.system().lower() == 'windows':
        proc.terminate()
    else:
        proc.send_signal(signal.SIGTERM)
    
    # Wait for exit
    try:
        exit_code = proc.wait(timeout=SHUTDOWN_TIMEOUT)
        
        # 0 = clean exit, 143 = SIGTERM on Linux, -15 = SIGTERM on macOS
        acceptable_codes = [0, 143, -15, -signal.SIGTERM]
        
        if exit_code in acceptable_codes:
            print(f'[PASS] Clean shutdown with exit code {exit_code}')
        else:
            print(f'[WARN] Exit code {exit_code} (expected 0 or 143)')
    except subprocess.TimeoutExpired:
        print(f'[FAIL] Process did not exit within {SHUTDOWN_TIMEOUT}s')
        proc.kill()
        sys.exit(4)


def main():
    """Run all smoke tests."""
    print('=' * 60)
    print('pyatv-server Smoke Test')
    print('=' * 60)
    print(f'Platform: {platform.system()} {platform.machine()}')
    print()
    
    try:
        binary_path = get_binary_path()
        print(f'Binary: {binary_path}')
        print(f'Size: {binary_path.stat().st_size / (1024*1024):.1f} MB')
        print()
    except FileNotFoundError as e:
        print(f'[FAIL] {e}')
        sys.exit(1)
    
    # Run tests
    proc = test_binary_startup(binary_path)
    test_health_check(proc)
    test_no_import_errors(proc)
    test_clean_shutdown(proc)
    
    print()
    print('=' * 60)
    print('ALL TESTS PASSED')
    print('=' * 60)
    sys.exit(0)


if __name__ == '__main__':
    main()
