"""Sessions API"""

from fastapi import APIRouter, HTTPException, Query
from typing import Optional

from vikingbot.config.loader import load_config
from vikingbot.session.manager import SessionManager

router = APIRouter()


@router.get("/sessions")
async def list_sessions(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0)
):
    try:
        config = load_config()
        session_manager = SessionManager(config.workspace_path)
        sessions = session_manager.list_sessions()
        
        for s in sessions:
            session = session_manager._load(s["key"])
            if session:
                s["message_count"] = len(session.messages)
        
        total = len(sessions)
        paginated = sessions[offset:offset + limit]
        
        return {
            "success": True,
            "data": paginated,
            "total": total
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/sessions/{session_key:path}")
async def get_session(session_key: str):
    try:
        config = load_config()
        session_manager = SessionManager(config.workspace_path)
        session = session_manager._load(session_key)
        
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
        
        return {
            "success": True,
            "data": {
                "key": session.key,
                "created_at": session.created_at.isoformat() if session.created_at else None,
                "updated_at": session.updated_at.isoformat() if session.updated_at else None,
                "messages": session.messages,
                "metadata": session.metadata
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/sessions/{session_key:path}")
async def delete_session(session_key: str):
    try:
        config = load_config()
        session_manager = SessionManager(config.workspace_path)
        deleted = session_manager.delete(session_key)
        
        if not deleted:
            raise HTTPException(status_code=404, detail="Session not found")
        
        return {
            "success": True,
            "message": "Session deleted"
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
