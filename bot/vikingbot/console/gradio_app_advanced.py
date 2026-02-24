import json
from typing import Any, Dict

import gradio as gr

from vikingbot.config.loader import load_config, save_config
from vikingbot.config.schema import Config


def get_config_schema():
    schema = Config.model_json_schema()
    
    def process_schema(obj):
        if isinstance(obj, dict):
            result = {}
            for k, v in obj.items():
                if k in ["title", "description", "type", "properties", "required", "default", "anyOf", "allOf", "oneOf", "items"]:
                    result[k] = process_schema(v)
            return result
        elif isinstance(obj, list):
            return [process_schema(item) for item in obj]
        return obj
    
    return process_schema(schema)


def load_config_data() -> Dict[str, Any]:
    try:
        config = load_config()
        return config.model_dump()
    except Exception as e:
        return {"error": str(e)}


def save_config_data(config_dict: Dict[str, Any]) -> str:
    try:
        config = Config(**config_dict)
        save_config(config)
        return "✓ Config saved successfully! Please restart the gateway service for changes to take effect."
    except Exception as e:
        return f"✗ Error: {str(e)}"


def get_status() -> Dict[str, Any]:
    from vikingbot import __version__
    config = load_config()
    return {
        "version": __version__,
        "sessions_count": 0,
        "config_path": str(config.workspace_path.parent / "config.json"),
    }


def render_schema_form(schema: Dict, config: Dict, prefix: str = "") -> list:
    components = []
    
    if "properties" not in schema:
        return components
    
    for key, field_schema in schema["properties"].items():
        field_type = field_schema.get("type", "string")
        label = field_schema.get("title", key)
        description = field_schema.get("description", "")
        default = field_schema.get("default")
        current_value = config.get(key, default)
        is_required = schema.get("required", []).count(key) > 0
        
        field_id = f"{prefix}.{key}" if prefix else key
        
        if field_type == "boolean":
            comp = gr.Checkbox(
                label=label,
                value=bool(current_value),
                info=description,
            )
            components.append((field_id, comp))
        
        elif field_type in ["number", "integer"]:
            comp = gr.Number(
                label=label,
                value=current_value,
                info=description,
            )
            components.append((field_id, comp))
        
        elif field_type == "array":
            items = field_schema.get("items", {})
            items_type = items.get("type", "string")
            
            if items_type == "string" and current_value:
                default_text = "\n".join(current_value) if isinstance(current_value, list) else ""
            else:
                default_text = ""
            
            comp = gr.Textbox(
                label=label,
                value=default_text,
                info=description + " (one item per line)",
                lines=5,
            )
            components.append((field_id + "__array", comp))
        
        elif field_type == "object":
            if "properties" in field_schema:
                gr.Markdown(f"### {label}")
                nested = render_schema_form(field_schema, current_value or {}, field_id)
                components.extend(nested)
            else:
                default_json = json.dumps(current_value, indent=2) if current_value else "{}"
                comp = gr.Code(
                    label=label,
                    value=default_json,
                    language="json",
                    info=description,
                    lines=10,
                )
                components.append((field_id + "__json", comp))
        
        else:
            comp = gr.Textbox(
                label=label,
                value=str(current_value) if current_value is not None else "",
                info=description,
            )
            components.append((field_id, comp))
    
    return components


def create_dashboard():
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
        
        refresh()


def create_config_tab():
    with gr.Tab("Config"):
        config = load_config_data()
        schema = get_config_schema()
        
        gr.Markdown("## Configuration (Schema-based)")
        
        form_components = render_schema_form(schema, config)
        
        save_btn = gr.Button("Save Config", variant="primary")
        status_msg = gr.Markdown("")
        
        refresh_btn = gr.Button("Refresh")
        
        gr.Markdown("---")
        gr.Markdown("### Raw JSON")
        config_json = gr.Code(
            value=json.dumps(config, indent=2),
            language="json",
            label="Config (JSON)",
            lines=20,
        )
        
        save_json_btn = gr.Button("Save JSON", variant="secondary")
        json_status_msg = gr.Markdown("")
        
        @save_json_btn.click(inputs=config_json, outputs=json_status_msg)
        def save_from_json(json_str):
            try:
                config_dict = json.loads(json_str)
                return save_config_data(config_dict)
            except Exception as e:
                return f"✗ Error: {str(e)}"
        
        @refresh_btn.click(outputs=config_json)
        def refresh_config():
            return json.dumps(load_config_data(), indent=2)


def create_sessions_tab():
    with gr.Tab("Sessions"):
        gr.Markdown("## Sessions")
        gr.Markdown("Sessions management coming soon...")


def create_workspace_tab():
    with gr.Tab("Workspace"):
        gr.Markdown("## Workspace")
        gr.Markdown("Workspace file browser coming soon...")


def create_app():
    with gr.Blocks(title="Vikingbot Console", theme=gr.themes.Soft()) as demo:
        gr.Markdown("# ⚓ Vikingbot Console (Gradio)")
        
        with gr.Tabs():
            create_dashboard()
            create_config_tab()
            create_sessions_tab()
            create_workspace_tab()
    
    return demo


if __name__ == "__main__":
    demo = create_app()
    demo.launch(server_name="0.0.0.0", server_port=8351, share=False)
