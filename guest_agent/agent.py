#!/usr/bin/env python3
"""
Guest agent that runs inside the Firecracker microVM.
Communicates with the host via vsock.

This agent handles:
- Command execution
- File read/write operations
- Directory listing
"""

import socket
import json
import subprocess
import os
import sys
import base64
import signal
from pathlib import Path
from typing import Dict, Any

VSOCK_PORT = 5000
WORKSPACE_DIR = "/workspace"


class GuestAgent:
    """Agent running inside the microVM to handle host requests."""
    
    def __init__(self):
        self.running = True
        self.sock = None
        
        # Handle shutdown signals
        signal.signal(signal.SIGTERM, self._handle_shutdown)
        signal.signal(signal.SIGINT, self._handle_shutdown)
    
    def _handle_shutdown(self, signum, frame):
        """Handle shutdown signals gracefully."""
        print(f"Received signal {signum}, shutting down...")
        self.running = False
        if self.sock:
            self.sock.close()
    
    def start(self):
        """Start listening for connections."""
        self.sock = socket.socket(socket.AF_VSOCK, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        
        # Bind to any CID on our port
        # VMADDR_CID_ANY = -1 or 0xFFFFFFFF
        self.sock.bind((socket.VMADDR_CID_ANY, VSOCK_PORT))
        self.sock.listen(5)
        self.sock.settimeout(1.0)  # Allow periodic check of self.running
        
        print(f"Guest agent listening on vsock port {VSOCK_PORT}")
        
        # Ensure workspace directory exists
        Path(WORKSPACE_DIR).mkdir(parents=True, exist_ok=True)
    
    def handle_exec(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """Execute a shell command."""
        cmd = request.get("command", "")
        timeout = request.get("timeout", 300)
        working_dir = request.get("working_dir", WORKSPACE_DIR)
        env = request.get("env", {})
        
        # Merge with current environment
        full_env = os.environ.copy()
        full_env.update(env)
        
        try:
            # Ensure working directory exists
            Path(working_dir).mkdir(parents=True, exist_ok=True)
            
            result = subprocess.run(
                cmd,
                shell=True,
                capture_output=True,
                timeout=timeout,
                cwd=working_dir,
                text=True,
                env=full_env
            )
            return {
                "success": True,
                "exit_code": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr
            }
        except subprocess.TimeoutExpired:
            return {
                "success": False,
                "error": f"Command timed out after {timeout} seconds",
                "exit_code": -1,
                "stdout": "",
                "stderr": ""
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "exit_code": -1,
                "stdout": "",
                "stderr": ""
            }
    
    def handle_read_file(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """Read a file from the filesystem."""
        path = request.get("path", "")
        
        try:
            file_path = Path(path)
            if not file_path.exists():
                return {"success": False, "error": f"File not found: {path}"}
            
            if not file_path.is_file():
                return {"success": False, "error": f"Not a file: {path}"}
            
            # Read as binary and base64 encode
            with open(path, "rb") as f:
                content = base64.b64encode(f.read()).decode()
            
            return {
                "success": True,
                "content": content,
                "size": file_path.stat().st_size
            }
        except PermissionError:
            return {"success": False, "error": f"Permission denied: {path}"}
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def handle_write_file(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """Write a file to the filesystem."""
        path = request.get("path", "")
        content = request.get("content", "")
        is_base64 = request.get("is_base64", False)
        mode = request.get("mode", None)  # Optional file mode (e.g., 0o755)
        
        try:
            file_path = Path(path)
            
            # Create parent directories if needed
            file_path.parent.mkdir(parents=True, exist_ok=True)
            
            # Write content
            if is_base64:
                with open(path, "wb") as f:
                    f.write(base64.b64decode(content))
            else:
                with open(path, "w") as f:
                    f.write(content)
            
            # Set file mode if specified
            if mode is not None:
                os.chmod(path, mode)
            
            return {
                "success": True,
                "path": path,
                "size": file_path.stat().st_size
            }
        except PermissionError:
            return {"success": False, "error": f"Permission denied: {path}"}
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def handle_delete_file(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """Delete a file or directory."""
        path = request.get("path", "")
        recursive = request.get("recursive", False)
        
        try:
            file_path = Path(path)
            
            if not file_path.exists():
                return {"success": False, "error": f"Path not found: {path}"}
            
            if file_path.is_dir():
                if recursive:
                    import shutil
                    shutil.rmtree(path)
                else:
                    file_path.rmdir()
            else:
                file_path.unlink()
            
            return {"success": True, "path": path}
        except PermissionError:
            return {"success": False, "error": f"Permission denied: {path}"}
        except OSError as e:
            return {"success": False, "error": str(e)}
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def handle_list_files(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """List files in a directory."""
        path = request.get("path", WORKSPACE_DIR)
        recursive = request.get("recursive", False)
        
        try:
            dir_path = Path(path)
            
            if not dir_path.exists():
                return {"success": False, "error": f"Directory not found: {path}"}
            
            if not dir_path.is_dir():
                return {"success": False, "error": f"Not a directory: {path}"}
            
            entries = []
            
            if recursive:
                for entry in dir_path.rglob("*"):
                    try:
                        stat = entry.stat()
                        entries.append({
                            "name": str(entry.relative_to(dir_path)),
                            "path": str(entry),
                            "is_dir": entry.is_dir(),
                            "size": stat.st_size if entry.is_file() else 0,
                            "modified": stat.st_mtime
                        })
                    except (PermissionError, OSError):
                        continue
            else:
                for entry in dir_path.iterdir():
                    try:
                        stat = entry.stat()
                        entries.append({
                            "name": entry.name,
                            "path": str(entry),
                            "is_dir": entry.is_dir(),
                            "size": stat.st_size if entry.is_file() else 0,
                            "modified": stat.st_mtime
                        })
                    except (PermissionError, OSError):
                        continue
            
            return {"success": True, "entries": entries}
        except PermissionError:
            return {"success": False, "error": f"Permission denied: {path}"}
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def handle_mkdir(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """Create a directory."""
        path = request.get("path", "")
        parents = request.get("parents", True)
        
        try:
            dir_path = Path(path)
            dir_path.mkdir(parents=parents, exist_ok=True)
            return {"success": True, "path": path}
        except PermissionError:
            return {"success": False, "error": f"Permission denied: {path}"}
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def handle_stat(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """Get file/directory statistics."""
        path = request.get("path", "")
        
        try:
            file_path = Path(path)
            
            if not file_path.exists():
                return {"success": False, "error": f"Path not found: {path}"}
            
            stat = file_path.stat()
            
            return {
                "success": True,
                "path": path,
                "is_file": file_path.is_file(),
                "is_dir": file_path.is_dir(),
                "is_symlink": file_path.is_symlink(),
                "size": stat.st_size,
                "mode": stat.st_mode,
                "uid": stat.st_uid,
                "gid": stat.st_gid,
                "atime": stat.st_atime,
                "mtime": stat.st_mtime,
                "ctime": stat.st_ctime
            }
        except PermissionError:
            return {"success": False, "error": f"Permission denied: {path}"}
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def handle_ping(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """Health check endpoint."""
        return {
            "success": True,
            "message": "pong",
            "workspace": WORKSPACE_DIR,
            "pid": os.getpid()
        }
    
    def handle_request(self, data: bytes) -> bytes:
        """Route request to appropriate handler."""
        try:
            request = json.loads(data.decode())
            action = request.get("action", "")
            
            handlers = {
                "exec": self.handle_exec,
                "read_file": self.handle_read_file,
                "write_file": self.handle_write_file,
                "delete_file": self.handle_delete_file,
                "list_files": self.handle_list_files,
                "mkdir": self.handle_mkdir,
                "stat": self.handle_stat,
                "ping": self.handle_ping,
            }
            
            handler = handlers.get(action)
            if handler:
                response = handler(request)
            else:
                response = {"success": False, "error": f"Unknown action: {action}"}
            
            return json.dumps(response).encode()
        except json.JSONDecodeError as e:
            return json.dumps({"success": False, "error": f"Invalid JSON: {e}"}).encode()
        except Exception as e:
            return json.dumps({"success": False, "error": str(e)}).encode()
    
    def _recv_exact(self, conn: socket.socket, n: int) -> bytes:
        """Receive exactly n bytes from socket."""
        data = b""
        while len(data) < n:
            chunk = conn.recv(n - len(data))
            if not chunk:
                raise ConnectionError("Connection closed")
            data += chunk
        return data
    
    def handle_connection(self, conn: socket.socket, addr):
        """Handle a single connection."""
        print(f"Connection from CID {addr}")
        conn.settimeout(300.0)  # 5 minute timeout per request
        
        try:
            while self.running:
                try:
                    # Read length-prefixed message (4 bytes big-endian length)
                    length_bytes = self._recv_exact(conn, 4)
                    length = int.from_bytes(length_bytes, "big")
                    
                    if length > 10 * 1024 * 1024:  # 10MB max message size
                        raise ValueError(f"Message too large: {length} bytes")
                    
                    data = self._recv_exact(conn, length)
                    
                    # Process request
                    response = self.handle_request(data)
                    
                    # Send length-prefixed response
                    conn.send(len(response).to_bytes(4, "big"))
                    conn.send(response)
                    
                except socket.timeout:
                    continue
                except ConnectionError:
                    break
                    
        except Exception as e:
            print(f"Connection error: {e}")
        finally:
            conn.close()
            print(f"Connection from CID {addr} closed")
    
    def run(self):
        """Main loop accepting connections."""
        self.start()
        
        while self.running:
            try:
                conn, addr = self.sock.accept()
                # Handle connection in same thread (simple single-threaded model)
                # For production, could use threading or asyncio
                self.handle_connection(conn, addr)
            except socket.timeout:
                continue
            except Exception as e:
                if self.running:
                    print(f"Accept error: {e}")
        
        print("Guest agent stopped")


def main():
    """Entry point."""
    print("Starting guest agent...")
    agent = GuestAgent()
    agent.run()


if __name__ == "__main__":
    main()
