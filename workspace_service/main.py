"""
Firecracker Workspace Service - REST API for sandbox management.

This service provides a REST API for creating, managing, and destroying
Firecracker microVM sandboxes for AI agent code execution.
"""

from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
from typing import Optional, List
import uuid
import base64
from datetime import datetime

from .sandbox_manager import SandboxManager, SandboxConfig

app = FastAPI(
    title="Firecracker Workspace Service",
    description="REST API for managing Firecracker microVM sandboxes",
    version="1.0.0"
)

# Global sandbox manager instance
sandbox_manager = SandboxManager()


# Request/Response Models

class CreateSandboxRequest(BaseModel):
    template: str = "default"
    memory_mb: int = 512
    vcpu_count: int = 1
    workspace_id: Optional[str] = None

class SandboxResponse(BaseModel):
    sandbox_id: str
    status: str
    template: str
    memory_mb: int
    vcpu_count: int
    workspace_id: str
    created_at: str
    ip_address: Optional[str] = None

class CommandRequest(BaseModel):
    command: str
    timeout_seconds: int = 300
    working_dir: str = "/workspace"

class CommandResponse(BaseModel):
    success: bool
    exit_code: int
    stdout: str
    stderr: str
    error: Optional[str] = None

class FileWriteRequest(BaseModel):
    path: str
    content: str
    is_base64: bool = False

class FileReadResponse(BaseModel):
    success: bool
    content: Optional[str] = None
    error: Optional[str] = None

class FileListEntry(BaseModel):
    name: str
    is_dir: bool
    size: int

class FileListResponse(BaseModel):
    success: bool
    entries: Optional[List[FileListEntry]] = None
    error: Optional[str] = None

class HealthResponse(BaseModel):
    status: str
    version: str
    active_sandboxes: int


# Health endpoint

@app.get("/health", response_model=HealthResponse)
async def health_check():
    """Check service health."""
    return HealthResponse(
        status="healthy",
        version="1.0.0",
        active_sandboxes=len(sandbox_manager._active_sandboxes)
    )


# Sandbox lifecycle endpoints

@app.post("/sandboxes", response_model=SandboxResponse)
async def create_sandbox(request: CreateSandboxRequest):
    """Create a new sandbox or resume an existing workspace."""
    try:
        config = await sandbox_manager.create_sandbox(
            template=request.template,
            memory_mb=request.memory_mb,
            vcpu_count=request.vcpu_count,
            workspace_id=request.workspace_id
        )
        return SandboxResponse(
            sandbox_id=config.sandbox_id,
            status=config.status,
            template=config.template,
            memory_mb=config.memory_mb,
            vcpu_count=config.vcpu_count,
            workspace_id=config.workspace_id,
            created_at=config.created_at,
            ip_address=config.ip_address
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/sandboxes", response_model=List[SandboxResponse])
async def list_sandboxes():
    """List all active sandboxes."""
    sandboxes = []
    for config in sandbox_manager._active_sandboxes.values():
        sandboxes.append(SandboxResponse(
            sandbox_id=config.sandbox_id,
            status=config.status,
            template=config.template,
            memory_mb=config.memory_mb,
            vcpu_count=config.vcpu_count,
            workspace_id=config.workspace_id,
            created_at=config.created_at,
            ip_address=config.ip_address
        ))
    return sandboxes


@app.get("/sandboxes/{sandbox_id}", response_model=SandboxResponse)
async def get_sandbox(sandbox_id: str):
    """Get sandbox status."""
    config = sandbox_manager._active_sandboxes.get(sandbox_id)
    if not config:
        raise HTTPException(status_code=404, detail="Sandbox not found")
    
    return SandboxResponse(
        sandbox_id=config.sandbox_id,
        status=config.status,
        template=config.template,
        memory_mb=config.memory_mb,
        vcpu_count=config.vcpu_count,
        workspace_id=config.workspace_id,
        created_at=config.created_at,
        ip_address=config.ip_address
    )


@app.delete("/sandboxes/{sandbox_id}")
async def destroy_sandbox(sandbox_id: str):
    """Destroy a sandbox and clean up resources."""
    if sandbox_id not in sandbox_manager._active_sandboxes:
        raise HTTPException(status_code=404, detail="Sandbox not found")
    
    try:
        await sandbox_manager.destroy_sandbox(sandbox_id)
        return {"status": "destroyed", "sandbox_id": sandbox_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/sandboxes/{sandbox_id}/pause")
async def pause_sandbox(sandbox_id: str):
    """Pause a sandbox (snapshot state for later resume)."""
    if sandbox_id not in sandbox_manager._active_sandboxes:
        raise HTTPException(status_code=404, detail="Sandbox not found")
    
    try:
        await sandbox_manager.pause_sandbox(sandbox_id)
        return {"status": "paused", "sandbox_id": sandbox_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/sandboxes/{sandbox_id}/resume")
async def resume_sandbox(sandbox_id: str):
    """Resume a paused sandbox."""
    try:
        config = await sandbox_manager.resume_sandbox(sandbox_id)
        return SandboxResponse(
            sandbox_id=config.sandbox_id,
            status=config.status,
            template=config.template,
            memory_mb=config.memory_mb,
            vcpu_count=config.vcpu_count,
            workspace_id=config.workspace_id,
            created_at=config.created_at,
            ip_address=config.ip_address
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# Command execution endpoint

@app.post("/sandboxes/{sandbox_id}/exec", response_model=CommandResponse)
async def exec_command(sandbox_id: str, request: CommandRequest):
    """Execute a command in the sandbox."""
    if sandbox_id not in sandbox_manager._active_sandboxes:
        raise HTTPException(status_code=404, detail="Sandbox not found")
    
    try:
        result = await sandbox_manager.exec_command(
            sandbox_id=sandbox_id,
            command=request.command,
            timeout=request.timeout_seconds,
            working_dir=request.working_dir
        )
        return CommandResponse(
            success=result.get("success", False),
            exit_code=result.get("exit_code", -1),
            stdout=result.get("stdout", ""),
            stderr=result.get("stderr", ""),
            error=result.get("error")
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# File operation endpoints

@app.post("/sandboxes/{sandbox_id}/files/write")
async def write_file(sandbox_id: str, request: FileWriteRequest):
    """Write a file to the sandbox filesystem."""
    if sandbox_id not in sandbox_manager._active_sandboxes:
        raise HTTPException(status_code=404, detail="Sandbox not found")
    
    try:
        result = await sandbox_manager.write_file(
            sandbox_id=sandbox_id,
            path=request.path,
            content=request.content,
            is_base64=request.is_base64
        )
        if result.get("success"):
            return {"status": "written", "path": request.path}
        else:
            raise HTTPException(status_code=500, detail=result.get("error", "Unknown error"))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/sandboxes/{sandbox_id}/files/read", response_model=FileReadResponse)
async def read_file(sandbox_id: str, path: str):
    """Read a file from the sandbox filesystem."""
    if sandbox_id not in sandbox_manager._active_sandboxes:
        raise HTTPException(status_code=404, detail="Sandbox not found")
    
    try:
        result = await sandbox_manager.read_file(sandbox_id=sandbox_id, path=path)
        return FileReadResponse(
            success=result.get("success", False),
            content=result.get("content"),
            error=result.get("error")
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/sandboxes/{sandbox_id}/files/list", response_model=FileListResponse)
async def list_files(sandbox_id: str, path: str = "/workspace"):
    """List files in a directory."""
    if sandbox_id not in sandbox_manager._active_sandboxes:
        raise HTTPException(status_code=404, detail="Sandbox not found")
    
    try:
        result = await sandbox_manager.list_files(sandbox_id=sandbox_id, path=path)
        if result.get("success"):
            entries = [
                FileListEntry(name=e["name"], is_dir=e["is_dir"], size=e["size"])
                for e in result.get("entries", [])
            ]
            return FileListResponse(success=True, entries=entries)
        else:
            return FileListResponse(success=False, error=result.get("error"))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/sandboxes/{sandbox_id}/files/upload")
async def upload_file(sandbox_id: str, path: str, file: UploadFile = File(...)):
    """Upload a file to the sandbox."""
    if sandbox_id not in sandbox_manager._active_sandboxes:
        raise HTTPException(status_code=404, detail="Sandbox not found")
    
    try:
        content = await file.read()
        content_b64 = base64.b64encode(content).decode()
        result = await sandbox_manager.write_file(
            sandbox_id=sandbox_id,
            path=path,
            content=content_b64,
            is_base64=True
        )
        if result.get("success"):
            return {"status": "uploaded", "path": path, "size": len(content)}
        else:
            raise HTTPException(status_code=500, detail=result.get("error", "Unknown error"))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
