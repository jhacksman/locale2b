"""
CompyMac Workspace Provider using Firecracker sandboxes.

This module provides a workspace provider that integrates with CompyMac's
agent loop, providing isolated sandbox environments for code execution.
"""

import httpx
import base64
from typing import Optional, Dict, Any, List
from dataclasses import dataclass


@dataclass
class CommandResult:
    """Result of a command execution."""
    success: bool
    exit_code: int
    stdout: str
    stderr: str
    error: Optional[str] = None


@dataclass
class FileInfo:
    """Information about a file."""
    name: str
    path: str
    is_dir: bool
    size: int


class FirecrackerWorkspaceProvider:
    """
    CompyMac workspace provider using Firecracker sandboxes.
    
    This provider wraps the Firecracker Workspace Service REST API
    and provides a clean interface for CompyMac's agent loop.
    
    Example usage:
        provider = FirecrackerWorkspaceProvider("http://localhost:8080")
        
        # Create a new workspace
        sandbox_id = await provider.create_workspace()
        
        # Run commands
        result = await provider.run_command("pip install requests")
        if result.success:
            print(result.stdout)
        
        # Write files
        await provider.write_file("/workspace/main.py", "print('hello')")
        
        # Read files
        content = await provider.read_file("/workspace/main.py")
        
        # Pause for later (preserves state)
        await provider.pause_workspace()
        
        # Resume later
        await provider.resume_workspace(sandbox_id)
        
        # Cleanup
        await provider.destroy_workspace()
    """
    
    def __init__(
        self,
        service_url: str = "http://localhost:8080",
        default_memory_mb: int = 512,
        default_vcpu_count: int = 1,
        default_template: str = "default",
        timeout: float = 300.0
    ):
        """
        Initialize the workspace provider.
        
        Args:
            service_url: URL of the Firecracker Workspace Service
            default_memory_mb: Default memory allocation for new sandboxes
            default_vcpu_count: Default vCPU count for new sandboxes
            default_template: Default rootfs template to use
            timeout: Default timeout for HTTP requests
        """
        self.service_url = service_url.rstrip("/")
        self.default_memory_mb = default_memory_mb
        self.default_vcpu_count = default_vcpu_count
        self.default_template = default_template
        self.timeout = timeout
        
        self.client = httpx.AsyncClient(
            base_url=self.service_url,
            timeout=timeout
        )
        
        self.current_sandbox_id: Optional[str] = None
        self.workspace_id: Optional[str] = None
    
    async def close(self):
        """Close the HTTP client."""
        await self.client.aclose()
    
    async def __aenter__(self):
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()
    
    async def health_check(self) -> Dict[str, Any]:
        """Check if the workspace service is healthy."""
        response = await self.client.get("/health")
        response.raise_for_status()
        return response.json()
    
    async def create_workspace(
        self,
        workspace_id: Optional[str] = None,
        memory_mb: Optional[int] = None,
        vcpu_count: Optional[int] = None,
        template: Optional[str] = None
    ) -> str:
        """
        Create a new workspace sandbox.
        
        Args:
            workspace_id: Optional ID for workspace persistence
            memory_mb: Memory allocation in MB (default: 512)
            vcpu_count: Number of vCPUs (default: 1)
            template: Rootfs template to use (default: "default")
        
        Returns:
            The sandbox ID
        """
        response = await self.client.post("/sandboxes", json={
            "template": template or self.default_template,
            "memory_mb": memory_mb or self.default_memory_mb,
            "vcpu_count": vcpu_count or self.default_vcpu_count,
            "workspace_id": workspace_id
        })
        response.raise_for_status()
        
        data = response.json()
        self.current_sandbox_id = data["sandbox_id"]
        self.workspace_id = data.get("workspace_id", self.current_sandbox_id)
        
        return self.current_sandbox_id
    
    async def get_workspace_status(self) -> Dict[str, Any]:
        """Get the status of the current workspace."""
        if not self.current_sandbox_id:
            raise RuntimeError("No active workspace")
        
        response = await self.client.get(f"/sandboxes/{self.current_sandbox_id}")
        response.raise_for_status()
        return response.json()
    
    async def run_command(
        self,
        command: str,
        timeout: int = 300,
        working_dir: str = "/workspace"
    ) -> CommandResult:
        """
        Execute a command in the current workspace.
        
        Args:
            command: Shell command to execute
            timeout: Timeout in seconds
            working_dir: Working directory for the command
        
        Returns:
            CommandResult with exit code, stdout, stderr
        """
        if not self.current_sandbox_id:
            raise RuntimeError("No active workspace")
        
        response = await self.client.post(
            f"/sandboxes/{self.current_sandbox_id}/exec",
            json={
                "command": command,
                "timeout_seconds": timeout,
                "working_dir": working_dir
            },
            timeout=timeout + 10  # Extra buffer for HTTP overhead
        )
        response.raise_for_status()
        
        data = response.json()
        return CommandResult(
            success=data.get("success", False),
            exit_code=data.get("exit_code", -1),
            stdout=data.get("stdout", ""),
            stderr=data.get("stderr", ""),
            error=data.get("error")
        )
    
    async def write_file(
        self,
        path: str,
        content: str,
        binary: bool = False
    ) -> None:
        """
        Write a file to the workspace.
        
        Args:
            path: Absolute path in the sandbox
            content: File content (string or base64 for binary)
            binary: If True, content is base64-encoded binary
        """
        if not self.current_sandbox_id:
            raise RuntimeError("No active workspace")
        
        response = await self.client.post(
            f"/sandboxes/{self.current_sandbox_id}/files/write",
            json={
                "path": path,
                "content": content,
                "is_base64": binary
            }
        )
        response.raise_for_status()
    
    async def write_binary_file(self, path: str, data: bytes) -> None:
        """
        Write binary data to a file in the workspace.
        
        Args:
            path: Absolute path in the sandbox
            data: Binary data to write
        """
        content = base64.b64encode(data).decode()
        await self.write_file(path, content, binary=True)
    
    async def read_file(self, path: str) -> str:
        """
        Read a file from the workspace.
        
        Args:
            path: Absolute path in the sandbox
        
        Returns:
            File content as string (base64-encoded for binary files)
        """
        if not self.current_sandbox_id:
            raise RuntimeError("No active workspace")
        
        response = await self.client.get(
            f"/sandboxes/{self.current_sandbox_id}/files/read",
            params={"path": path}
        )
        response.raise_for_status()
        
        data = response.json()
        if not data.get("success"):
            raise FileNotFoundError(data.get("error", f"Failed to read {path}"))
        
        return data.get("content", "")
    
    async def read_binary_file(self, path: str) -> bytes:
        """
        Read binary data from a file in the workspace.
        
        Args:
            path: Absolute path in the sandbox
        
        Returns:
            Binary file content
        """
        content = await self.read_file(path)
        return base64.b64decode(content)
    
    async def list_files(
        self,
        path: str = "/workspace",
        recursive: bool = False
    ) -> List[FileInfo]:
        """
        List files in a directory.
        
        Args:
            path: Directory path to list
            recursive: If True, list recursively
        
        Returns:
            List of FileInfo objects
        """
        if not self.current_sandbox_id:
            raise RuntimeError("No active workspace")
        
        response = await self.client.get(
            f"/sandboxes/{self.current_sandbox_id}/files/list",
            params={"path": path}
        )
        response.raise_for_status()
        
        data = response.json()
        if not data.get("success"):
            raise FileNotFoundError(data.get("error", f"Failed to list {path}"))
        
        return [
            FileInfo(
                name=entry["name"],
                path=entry.get("path", f"{path}/{entry['name']}"),
                is_dir=entry["is_dir"],
                size=entry["size"]
            )
            for entry in data.get("entries", [])
        ]
    
    async def file_exists(self, path: str) -> bool:
        """Check if a file exists in the workspace."""
        try:
            result = await self.run_command(f"test -e {path} && echo exists")
            return "exists" in result.stdout
        except Exception:
            return False
    
    async def pause_workspace(self) -> None:
        """
        Pause the current workspace.
        
        This creates a snapshot of the VM state that can be resumed later.
        The workspace_id can be used to resume the same workspace.
        """
        if not self.current_sandbox_id:
            raise RuntimeError("No active workspace")
        
        response = await self.client.post(
            f"/sandboxes/{self.current_sandbox_id}/pause"
        )
        response.raise_for_status()
    
    async def resume_workspace(self, sandbox_id: str) -> None:
        """
        Resume a paused workspace.
        
        Args:
            sandbox_id: The sandbox ID to resume
        """
        response = await self.client.post(
            f"/sandboxes/{sandbox_id}/resume"
        )
        response.raise_for_status()
        
        self.current_sandbox_id = sandbox_id
    
    async def destroy_workspace(self) -> None:
        """Destroy the current workspace and clean up resources."""
        if not self.current_sandbox_id:
            return
        
        response = await self.client.delete(
            f"/sandboxes/{self.current_sandbox_id}"
        )
        response.raise_for_status()
        
        self.current_sandbox_id = None
    
    # Convenience methods for common operations
    
    async def install_package(self, package: str, manager: str = "pip") -> CommandResult:
        """
        Install a package in the workspace.
        
        Args:
            package: Package name to install
            manager: Package manager ("pip", "npm", "apt")
        
        Returns:
            CommandResult from the installation
        """
        commands = {
            "pip": f"pip install {package}",
            "pip3": f"pip3 install {package}",
            "npm": f"npm install {package}",
            "apt": f"apt-get install -y {package}",
            "apk": f"apk add {package}"
        }
        
        cmd = commands.get(manager, f"{manager} install {package}")
        return await self.run_command(cmd)
    
    async def clone_repo(self, url: str, path: str = "/workspace/repo") -> CommandResult:
        """
        Clone a git repository into the workspace.
        
        Args:
            url: Git repository URL
            path: Destination path
        
        Returns:
            CommandResult from git clone
        """
        return await self.run_command(f"git clone {url} {path}")
    
    async def run_python(self, script: str, args: str = "") -> CommandResult:
        """
        Run a Python script in the workspace.
        
        Args:
            script: Path to the Python script
            args: Command line arguments
        
        Returns:
            CommandResult from Python execution
        """
        return await self.run_command(f"python3 {script} {args}")
    
    async def run_tests(self, path: str = "/workspace", framework: str = "pytest") -> CommandResult:
        """
        Run tests in the workspace.
        
        Args:
            path: Path to test directory
            framework: Test framework ("pytest", "unittest", "npm")
        
        Returns:
            CommandResult from test execution
        """
        commands = {
            "pytest": f"cd {path} && pytest -v",
            "unittest": f"cd {path} && python -m unittest discover",
            "npm": f"cd {path} && npm test"
        }
        
        cmd = commands.get(framework, f"cd {path} && {framework}")
        return await self.run_command(cmd)


# Example usage
async def example():
    """Example of using the workspace provider."""
    async with FirecrackerWorkspaceProvider() as provider:
        # Check health
        health = await provider.health_check()
        print(f"Service health: {health}")
        
        # Create workspace
        sandbox_id = await provider.create_workspace()
        print(f"Created sandbox: {sandbox_id}")
        
        # Run a command
        result = await provider.run_command("echo 'Hello from Firecracker!'")
        print(f"Command output: {result.stdout}")
        
        # Write a file
        await provider.write_file("/workspace/hello.py", "print('Hello, World!')")
        
        # Run the file
        result = await provider.run_python("/workspace/hello.py")
        print(f"Python output: {result.stdout}")
        
        # List files
        files = await provider.list_files("/workspace")
        print(f"Files: {[f.name for f in files]}")
        
        # Cleanup
        await provider.destroy_workspace()
        print("Workspace destroyed")


if __name__ == "__main__":
    import asyncio
    asyncio.run(example())
