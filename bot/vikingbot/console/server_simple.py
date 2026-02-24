"""Vikingbot Console Server - FastAPI Web Service (Simple)"""

import asyncio
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from starlette.middleware.cors import CORSMiddleware

from vikingbot import __version__
from vikingbot.config.loader import load_config, get_config_path, save_config
from vikingbot.session.manager import SessionManager
from vikingbot.utils.helpers import get_workspace_path

app = FastAPI(title="Vikingbot Console", version=__version__)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_start_time = time.time()


@app.get("/health")
@app.get("/healthz")
async def health_check():
    """Health check endpoint for Kubernetes probes"""
    return {
        "status": "healthy",
        "version": __version__,
        "uptime": int(time.time() - _start_time)
    }


@app.get("/api/v1/status")
async def get_status():
    config = load_config()
    session_manager = SessionManager(config.workspace_path)
    sessions = session_manager.list_sessions()
    
    return {
        "success": True,
        "data": {
            "version": __version__,
            "uptime": int(time.time() - _start_time),
            "config_path": str(get_config_path()),
            "workspace_path": str(config.workspace_path),
            "sessions_count": len(sessions),
            "gateway_running": True
        }
    }


from vikingbot.console.api import config, sessions, workspace, partials

app.include_router(config.router, prefix="/api/v1", tags=["config"])
app.include_router(sessions.router, prefix="/api/v1", tags=["sessions"])
app.include_router(workspace.router, prefix="/api/v1", tags=["workspace"])
app.include_router(partials.router, prefix="/api/v1", tags=["partials"])


@app.get("/{path:path}")
async def serve_frontend(path: str):
    static_dir = Path(__file__).parent / "static"
    
    if path == "" or path == "/":
        return FileResponse(static_dir / "index.html")
    
    file_path = static_dir / path
    if file_path.exists() and file_path.is_file():
        return FileResponse(file_path)
    
    return FileResponse(static_dir / "index.html")


try:
    import gradio as gr
    import json
    from typing import Any, Dict, List
    
    def create_gradio_app():
        def get_status_inner() -> Dict[str, Any]:
            from vikingbot import __version__
            config = load_config()
            return {
                "version": __version__,
                "sessions_count": 0,
                "config_path": str(config.workspace_path.parent / "config.json"),
                "workspace_path": str(config.workspace_path),
            }
        
        def get_available_skills() -> List[str]:
            return [
                "github-proxy",
                "github", 
                "memory",
                "cron",
                "weather",
                "tmux",
                "skill-creator",
                "summarize"
            ]
        
        def create_dashboard_tab():
            with gr.Tab("Dashboard"):
                status_md = gr.Markdown("Loading...")
                
                @gr.on(triggers=[gr.Button("Refresh").click], outputs=status_md)
                def refresh():
                    s = get_status_inner()
                    return f"""
                    # ⚓ Vikingbot Console
                    
                    | Status | Value |
                    |--------|-------|
                    | 🟢 Status | Running |
                    | 📦 Version | {s['version']} |
                    | 💬 Sessions | {s['sessions_count']} |
                    | 📁 Config Path | {s['config_path']} |
                    | 🖥️ Workspace Path | {s['workspace_path']} |
                    """
                
                refresh()
        
        def create_config_tab():
            with gr.Tab("Config"):
                config = load_config()
                config_dict = config.model_dump()
                
                gr.Markdown("## Configuration")
                
                with gr.Row():
                    with gr.Column():
                        gr.Markdown("### Skills")
                        skills_choices = get_available_skills()
                        selected_skills = config_dict.get("skills", [])
                        skills = gr.CheckboxGroup(
                            choices=skills_choices,
                            value=selected_skills,
                            label="Enabled Skills",
                            info="Select the skills you want to enable"
                        )
                    
                    with gr.Column():
                        gr.Markdown("### Hooks")
                        hooks_list = config_dict.get("hooks", [])
                        hooks_text = "\n".join(hooks_list) if hooks_list else ""
                        hooks = gr.Textbox(
                            value=hooks_text,
                            label="Hook Paths",
                            info="Enter hook paths, one per line",
                            lines=5
                        )
                
                gr.Markdown("---")
                gr.Markdown("### Full Configuration (JSON)")
                config_json = gr.Code(
                    value=json.dumps(config_dict, indent=2),
                    language="json",
                    label="Config",
                    lines=20
                )
                
                save_btn = gr.Button("Save Config", variant="primary")
                status_msg = gr.Markdown("")
                
                @save_btn.click(inputs=[skills, hooks, config_json], outputs=status_msg)
                def save(skills_val, hooks_val, json_val):
                    from vikingbot.config.schema import Config
                    try:
                        config_dict = json.loads(json_val)
                        config_dict["skills"] = skills_val or []
                        
                        if hooks_val:
                            config_dict["hooks"] = [h.strip() for h in hooks_val.split("\n") if h.strip()]
                        else:
                            config_dict["hooks"] = []
                        
                        config = Config(**config_dict)
                        save_config(config)
                        return "✓ Config saved successfully! Please restart the gateway service for changes to take effect."
                    except Exception as e:
                        return f"✗ Error: {str(e)}"
                
                refresh_btn = gr.Button("Refresh")
                
                @refresh_btn.click(outputs=[skills, hooks, config_json])
                def refresh():
                    config = load_config()
                    d = config.model_dump()
                    skills_val = d.get("skills", [])
                    hooks_val = "\n".join(d.get("hooks", []))
                    json_val = json.dumps(d, indent=2)
                    return skills_val, hooks_val, json_val
        
        def create_sessions_tab():
            with gr.Tab("Sessions"):
                gr.Markdown("## Sessions")
                gr.Markdown("Sessions management coming soon...")
        
        def create_workspace_tab():
            with gr.Tab("Workspace"):
                gr.Markdown("## Workspace")
                gr.Markdown("Workspace file browser coming soon...")
        
        with gr.Blocks(title="Vikingbot Console") as demo:
            gr.Markdown("# ⚓ Vikingbot Console")
            
            with gr.Tabs():
                create_dashboard_tab()
                create_config_tab()
                create_sessions_tab()
                create_workspace_tab()
        
        return demo
    
    gradio_demo = create_gradio_app()
    app = gr.mount_gradio_app(app, gradio_demo, path="/")
    
except ImportError:
    pass
except Exception as e:
    print(f"Warning: Gradio not available: {e}")


async def start_console_server(port: int = 18791, host: str = "0.0.0.0"):
    import uvicorn
    config = uvicorn.Config(
        app,
        host=host,
        port=port,
        log_level="info",
        access_log=False
    )
    server = uvicorn.Server(config)
    await server.serve()
