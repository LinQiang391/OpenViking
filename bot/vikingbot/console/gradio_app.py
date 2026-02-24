"""
Vikingbot Console - Gradio Implementation

纯 Python 实现的控制台界面，基于 Gradio。
不需要任何前端框架知识！
"""

import json
from pathlib import Path
from typing import Any, Dict

import gradio as gr

from vikingbot.config.loader import load_config, save_config
from vikingbot.config.schema import Config


def load_config_data() -> Dict[str, Any]:
    """Load current config."""
    try:
        config = load_config()
        return config.model_dump()
    except Exception as e:
        return {"error": str(e)}


def save_config_data(config_json: str) -> str:
    """Save config from JSON string."""
    try:
        config_dict = json.loads(config_json)
        config = Config(**config_dict)
        save_config(config)
        return "✓ Config saved successfully! Please restart the gateway service for changes to take effect."
    except Exception as e:
        return f"✗ Error: {str(e)}"


def get_status() -> Dict[str, Any]:
    """Get system status."""
    from vikingbot import __version__
    return {
        "version": __version__,
        "sessions_count": 0,
        "config_path": str(load_config().workspace_path.parent / "config.json"),
    }


def create_dashboard():
    """Create dashboard tab."""
    with gr.Tab("Dashboard"):
        status = gr.Markdown("Loading...")
        
        @gr.on(triggers=[gr.Button("Refresh").click], outputs=status)
        def refresh():
            s = get_status()
            return f"""
            ## ⚓ Vikingbot Console
            
            | Status | Value |
            |--------|-------|
            | 🟢 Status | Running |
            | 📦 Version | {s['version']} |
            | 💬 Sessions | {s['sessions_count']} |
            | 📁 Config Path | {s['config_path']} |
            """
        
        # Initial load
        refresh()


def create_config_tab():
    """Create config tab with dynamic form."""
    with gr.Tab("Config"):
        config = load_config_data()
        
        gr.Markdown("## Configuration")
        
        # JSON editor (simple and reliable)
        config_json = gr.Code(
            value=json.dumps(config, indent=2),
            language="json",
            label="Config (JSON)",
            lines=30,
        )
        
        save_btn = gr.Button("Save Config", variant="primary")
        status_msg = gr.Markdown("")
        
        save_btn.click(
            fn=save_config_data,
            inputs=config_json,
            outputs=status_msg,
        )
        
        # Refresh button
        refresh_btn = gr.Button("Refresh")
        
        @refresh_btn.click(outputs=config_json)
        def refresh_config():
            return json.dumps(load_config_data(), indent=2)


def create_sessions_tab():
    """Create sessions tab."""
    with gr.Tab("Sessions"):
        gr.Markdown("## Sessions")
        gr.Markdown("Sessions management coming soon...")


def create_workspace_tab():
    """Create workspace tab."""
    with gr.Tab("Workspace"):
        gr.Markdown("## Workspace")
        gr.Markdown("Workspace file browser coming soon...")


def create_app():
    """Create the Gradio app."""
    with gr.Blocks(title="Vikingbot Console", theme=gr.themes.Soft()) as demo:
        gr.Markdown("# ⚓ Vikingbot Console")
        
        with gr.Tabs():
            create_dashboard()
            create_config_tab()
            create_sessions_tab()
            create_workspace_tab()
    
    return demo


if __name__ == "__main__":
    demo = create_app()
    demo.launch(server_name="0.0.0.0", server_port=8351, share=False)
