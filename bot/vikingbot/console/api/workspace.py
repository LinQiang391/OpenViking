"""Workspace API"""

import os
from pathlib import Path
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from vikingbot.config.loader import load_config

router = APIRouter()


class FileWrite(BaseModel):
    content: str


def is_safe_path(base: Path, target: Path) -> bool:
    try:
        base.resolve()
        target.resolve()
        return base in target.resolve().parents or target.resolve() == base
    except Exception:
        return False


def get_workspace_base(workspace_id: Optional[str] = None) -> Path:
    config = load_config()
    workspace =  config.workspace_path
    if workspace_id:
        workspace = workspace / workspace_id
    return workspace



@router.get("/workspaces")
async def list_workspaces():
    try:
        config = load_config()
        
        workspaces = []

        workspace = get_workspace_base(None)

        for item in workspace.iterdir():
            workspaces.append({
                "id": item.name,
                "name": item.name,
                "path": str(item),
                "is_default": False
            })
        return {
            "success": True,
            "data": workspaces
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/workspace/files")
async def list_files(
    path: str = Query("/"),
    workspace_id: Optional[str] = Query(None)
):
    try:
        workspace_path = get_workspace_base(workspace_id)
        
        target_path = workspace_path / path.lstrip("/")
        target_path = target_path.resolve()
        
        if not is_safe_path(workspace_path, target_path):
            raise HTTPException(status_code=403, detail="Access denied")
        
        if not target_path.exists():
            raise HTTPException(status_code=404, detail="Path not found")
        
        if not target_path.is_dir():
            raise HTTPException(status_code=400, detail="Not a directory")
        
        files = []
        for item in target_path.iterdir():
            stat = item.stat()
            files.append({
                "name": item.name,
                "type": "directory" if item.is_dir() else "file",
                "size": stat.st_size,
                "modified_at": datetime.fromtimestamp(stat.st_mtime).isoformat()
            })
        
        files.sort(key=lambda x: (x["type"] != "directory", x["name"]))
        
        relative_path = str(target_path.relative_to(workspace_path))
        if relative_path == ".":
            relative_path = "/"
        else:
            relative_path = "/" + relative_path
        
        return {
            "success": True,
            "data": {
                "path": relative_path,
                "workspace_id": workspace_id or "shared",
                "files": files
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/workspace/files/{file_path:path}")
async def read_file(
    file_path: str,
    workspace_id: Optional[str] = Query(None)
):
    try:
        workspace_path = get_workspace_base(workspace_id)
        
        target_path = workspace_path / file_path.lstrip("/")
        target_path = target_path.resolve()
        
        if not is_safe_path(workspace_path, target_path):
            raise HTTPException(status_code=403, detail="Access denied")
        
        if not target_path.exists():
            raise HTTPException(status_code=404, detail="File not found")
        
        if target_path.is_dir():
            raise HTTPException(status_code=400, detail="Is a directory")
        
        stat = target_path.stat()
        
        try:
            content = target_path.read_text()
        except UnicodeDecodeError:
            raise HTTPException(status_code=400, detail="Binary file not supported")
        
        relative_path = str(target_path.relative_to(workspace_path))
        
        return {
            "success": True,
            "data": {
                "path": relative_path,
                "workspace_id": workspace_id or "shared",
                "content": content,
                "size": stat.st_size,
                "modified_at": datetime.fromtimestamp(stat.st_mtime).isoformat()
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/workspace/files/{file_path:path}")
async def write_file(
    file_path: str,
    data: FileWrite,
    workspace_id: Optional[str] = Query(None)
):
    try:
        workspace_path = get_workspace_base(workspace_id)
        
        target_path = workspace_path / file_path.lstrip("/")
        target_path = target_path.resolve()
        
        if not is_safe_path(workspace_path, target_path):
            raise HTTPException(status_code=403, detail="Access denied")
        
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text(data.content)
        
        return {
            "success": True,
            "message": "File written"
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/workspace/files/{file_path:path}")
async def delete_file(
    file_path: str,
    workspace_id: Optional[str] = Query(None)
):
    try:
        workspace_path = get_workspace_base(workspace_id)
        
        target_path = workspace_path / file_path.lstrip("/")
        target_path = target_path.resolve()
        
        if not is_safe_path(workspace_path, target_path):
            raise HTTPException(status_code=403, detail="Access denied")
        
        if not target_path.exists():
            raise HTTPException(status_code=404, detail="File not found")
        
        if target_path.is_dir():
            import shutil
            shutil.rmtree(target_path)
        else:
            target_path.unlink()
        
        return {
            "success": True,
            "message": "File deleted"
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
