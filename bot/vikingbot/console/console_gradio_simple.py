import json
import sys
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import gradio as gr

from vikingbot.config.loader import load_config, save_config
from vikingbot.config.schema import Config


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
        from vikingbot import __version__
        config = load_config()
        gr.Markdown("# ⚓ Vikingbot Console")
        gr.Markdown(f"""
        | Status | Value |
        |--------|-------|
        | 🟢 Status | Running |
        | 📦 Version | {__version__} |
        | 📁 Config Path | {str(config.workspace_path.parent / "config.json")} |
        | 🖥️ Workspace Path | {str(config.workspace_path)} |
        """)


def create_config_field(field_name: str, field_info: Dict[str, Any], current_value: Any, parent_path: str = "") -> List:
    field_path = f"{parent_path}.{field_name}" if parent_path else field_name
    
    description = field_info.get("description", "")
    title = field_info.get("title", field_name.replace("_", " ").title())
    label = f"{title}" + (f" ({description})" if description else "")
    
    components = []
    
    field_type = field_info.get("type", "string")
    if field_type == "object" and "properties" in field_info:
        with gr.Group():
            gr.Markdown(f"### {title}")
            if description:
                gr.Markdown(f"*{description}*")
            for nested_field_name, nested_field_info in field_info["properties"].items():
                nested_value = current_value.get(nested_field_name, None) if current_value else None
                components.extend(create_config_field(nested_field_name, nested_field_info, nested_value, field_path))
    elif field_type == "array":
        items_info = field_info.get("items", {})
        items_type = items_info.get("type", "string")
        # If items type isn't a simple type (has properties or is an object), use JSON editor
        use_textbox = False
        if items_type == "string" and not ("properties" in items_info or items_type == "object"):
            current_list = current_value or []
            # Check if all items are strings before joining
            if all(isinstance(item, str) for item in current_list):
                use_textbox = True
        
        if use_textbox:
            current_list = current_value or []
            value = "\n".join(current_list) if current_list else ""
            components.append(gr.Textbox(
                value=value,
                label=f"{label} (one per line)",
                lines=3,
                elem_id=f"field_{field_path.replace('.', '_')}"
            ))
        else:
            value = json.dumps(current_value, indent=2) if current_value else ""
            components.append(gr.Code(
                value=value,
                label=label,
                language="json",
                elem_id=f"field_{field_path.replace('.', '_')}"
            ))
    elif field_type == "integer":
        components.append(gr.Number(
            value=current_value,
            label=label,
            elem_id=f"field_{field_path.replace('.', '_')}"
        ))
    elif field_type == "number":
        components.append(gr.Number(
            value=current_value,
            label=label,
            elem_id=f"field_{field_path.replace('.', '_')}"
        ))
    elif field_type == "boolean":
        components.append(gr.Checkbox(
            value=current_value or False,
            label=label,
            elem_id=f"field_{field_path.replace('.', '_')}"
        ))
    else:
        components.append(gr.Textbox(
            value=current_value or "",
            label=label,
            elem_id=f"field_{field_path.replace('.', '_')}"
        ))
    
    return components


def collect_field_values(components, schema, parent_path: str = ""):
    result = {}
    comp_idx = 0
    
    for field_name, field_info in schema.get("properties", {}).items():
        field_path = f"{parent_path}.{field_name}" if parent_path else field_name
        field_type = field_info.get("type", "string")
        
        if field_type == "object" and "properties" in field_info:
            nested_result, num_consumed = collect_field_values(components[comp_idx:], field_info, field_path)
            result[field_name] = nested_result
            comp_idx += num_consumed
        else:
            component = components[comp_idx]
            comp_idx += 1
            
            if hasattr(component, "value"):
                value = component.value
                
                if field_type == "array":
                    items_type = field_info.get("items", {}).get("type", "string")
                    if items_type == "string" and isinstance(value, str):
                        value = [line.strip() for line in value.split("\n") if line.strip()]
                    elif isinstance(value, str):
                        try:
                            value = json.loads(value)
                        except:
                            value = []
                
                result[field_name] = value
    
    return result, comp_idx


def create_config_tab():
    with gr.Tab("Config"):
        config = load_config()
        config_dict = config.model_dump()
        schema = Config.model_json_schema()
        
        gr.Markdown("## Configuration")
        
        # Skills & Hooks section
        gr.Markdown("### Skills & Hooks")
        
        with gr.Row():
            with gr.Column():
                skills_choices = get_available_skills()
                selected_skills = config_dict.get("skills", [])
                skills = gr.CheckboxGroup(
                    choices=skills_choices,
                    value=selected_skills,
                    label="Enabled Skills",
                    info="Select the skills you want to enable"
                )
            
            with gr.Column():
                hooks_list = config_dict.get("hooks", [])
                hooks_text = "\n".join(hooks_list) if hooks_list else ""
                hooks = gr.Textbox(
                    value=hooks_text,
                    label="Hook Paths",
                    info="Enter hook paths, one per line",
                    lines=5
                )
        
        gr.Markdown("---")
        
        # Dynamic config fields from schema
        all_components = [skills, hooks]
        
        # Create sections for each top-level config field except skills and hooks
        sections = [
            ("agents", "Agents Config"),
            ("providers", "Providers Config"),
            ("channels", "Channels Config"),
            ("gateway", "Gateway Config"),
            ("tools", "Tools Config"),
            ("sandbox", "Sandbox Config"),
            ("heartbeat", "Heartbeat Config")
        ]
        
        for field_name, section_title in sections:
            if field_name in schema["properties"]:
                gr.Markdown(f"### {section_title}")
                field_info = schema["properties"][field_name]
                current_value = config_dict.get(field_name, None)
                new_components = create_config_field(field_name, field_info, current_value)
                all_components.extend(new_components)
                gr.Markdown("---")
        
        save_btn = gr.Button("Save Config")
        status_msg = gr.Markdown("")
        
        def save_config_fn(*args):
            try:
                config_dict = load_config().model_dump()
                
                # First two args are skills and hooks
                skills_val = args[0]
                hooks_val = args[1]
                
                config_dict["skills"] = skills_val or []
                if hooks_val:
                    config_dict["hooks"] = [h.strip() for h in hooks_val.split("\n") if h.strip()]
                else:
                    config_dict["hooks"] = []
                
                # Collect values from dynamic components
                remaining_args = args[2:]
                comp_idx = 0
                
                for field_name, _ in sections:
                    if field_name in schema["properties"]:
                        field_info = schema["properties"][field_name]
                        field_result, num_consumed = collect_field_values(remaining_args[comp_idx:], field_info)
                        config_dict[field_name] = field_result
                        comp_idx += num_consumed
                
                config = Config(**config_dict)
                save_config(config)
                return "✓ Config saved successfully! Please restart the gateway service for changes to take effect."
            except Exception as e:
                return f"✗ Error: {str(e)}"
        
        save_btn.click(
            fn=save_config_fn,
            inputs=all_components,
            outputs=status_msg
        )


def list_sessions() -> List[Dict[str, Any]]:
    """List all available sessions."""
    config = load_config()
    sessions_dir = config.workspace_path.parent / "sessions"
    
    if not sessions_dir.exists():
        return []
    
    sessions = []
    for session_file in sessions_dir.glob("*.json"):
        try:
            with open(session_file, "r") as f:
                session_data = json.load(f)
                sessions.append({
                    "name": session_file.stem,
                    "path": str(session_file),
                    "data": session_data
                })
        except:
            pass
    
    return sorted(sessions, key=lambda x: x["name"])


def get_session_content(session_name: str) -> str:
    """Get the content of a specific session."""
    config = load_config()
    sessions_dir = config.workspace_path.parent / "sessions"
    session_file = sessions_dir / f"{session_name}.json"
    
    if session_file.exists():
        with open(session_file, "r") as f:
            return json.dumps(json.load(f), indent=2)
    return "Session not found"


def delete_session(session_name: str) -> str:
    """Delete a specific session."""
    config = load_config()
    sessions_dir = config.workspace_path.parent / "sessions"
    session_file = sessions_dir / f"{session_name}.json"
    
    if session_file.exists():
        session_file.unlink()
        return f"Session '{session_name}' deleted successfully"
    return "Session not found"


def create_sessions_tab():
    with gr.Tab("Sessions"):
        gr.Markdown("## Sessions")
        
        with gr.Row():
            with gr.Column(scale=1):
                session_list = gr.Dropdown(
                    choices=[],
                    label="Select Session",
                    info="Choose a session to view or delete"
                )
                refresh_btn = gr.Button("Refresh Sessions")
                delete_btn = gr.Button("Delete Session", variant="stop")
            
            with gr.Column(scale=2):
                session_content = gr.Code(
                    value="",
                    label="Session Content",
                    language="json",
                    interactive=False
                )
                status_msg = gr.Markdown("")
        
        def refresh_sessions():
            sessions = list_sessions()
            session_names = [s["name"] for s in sessions]
            return gr.Dropdown(choices=session_names, value=None), ""
        
        def load_session(session_name):
            if session_name:
                return get_session_content(session_name), ""
            return "", "Please select a session"
        
        def remove_session(session_name):
            if session_name:
                result = delete_session(session_name)
                sessions = list_sessions()
                session_names = [s["name"] for s in sessions]
                return gr.Dropdown(choices=session_names, value=None), "", result
            return gr.Dropdown(), "", "Please select a session"
        
        refresh_btn.click(
            fn=refresh_sessions,
            outputs=[session_list, status_msg]
        )
        
        session_list.change(
            fn=load_session,
            inputs=session_list,
            outputs=[session_content, status_msg]
        )
        
        delete_btn.click(
            fn=remove_session,
            inputs=session_list,
            outputs=[session_list, session_content, status_msg]
        )
        
        # Initial load
        refresh_btn.click(fn=refresh_sessions, outputs=[session_list, status_msg])


def list_workspace_files(path: str = "") -> List[Dict[str, Any]]:
    """List files and directories in the workspace."""
    config = load_config()
    workspace_path = config.workspace_path
    
    if path:
        current_path = workspace_path / path
    else:
        current_path = workspace_path
    
    if not current_path.exists() or not current_path.is_dir():
        return []
    
    files = []
    for item in current_path.iterdir():
        files.append({
            "name": item.name,
            "path": str(item.relative_to(workspace_path)),
            "is_dir": item.is_dir()
        })
    
    return sorted(files, key=lambda x: (not x["is_dir"], x["name"]))


def read_workspace_file(file_path: str) -> str:
    """Read a file from the workspace."""
    config = load_config()
    full_path = config.workspace_path / file_path
    
    if full_path.exists() and full_path.is_file():
        try:
            with open(full_path, "r") as f:
                return f.read()
        except:
            return "Cannot read file (binary or encoding error)"
    return "File not found"


def write_workspace_file(file_path: str, content: str) -> str:
    """Write content to a file in the workspace."""
    config = load_config()
    full_path = config.workspace_path / file_path
    
    try:
        # Ensure parent directory exists
        full_path.parent.mkdir(parents=True, exist_ok=True)
        
        with open(full_path, "w") as f:
            f.write(content)
        
        return f"File '{file_path}' saved successfully"
    except Exception as e:
        return f"Error saving file: {str(e)}"


def delete_workspace_file(file_path: str) -> str:
    """Delete a file from the workspace."""
    config = load_config()
    full_path = config.workspace_path / file_path
    
    if full_path.exists():
        if full_path.is_file():
            full_path.unlink()
            return f"File '{file_path}' deleted successfully"
        elif full_path.is_dir():
            try:
                full_path.rmdir()
                return f"Directory '{file_path}' deleted successfully"
            except:
                return f"Cannot delete directory '{file_path}' (not empty)"
    return "Path not found"


def create_workspace_tab():
    with gr.Tab("Workspace"):
        gr.Markdown("## Workspace")
        
        current_path = gr.State("")
        
        with gr.Row():
            with gr.Column(scale=1):
                path_display = gr.Textbox(
                    value="",
                    label="Current Path",
                    interactive=False
                )
                file_list = gr.Dropdown(
                    choices=[],
                    label="Files & Directories",
                    info="Select a file to view/edit or a directory to navigate"
                )
                refresh_btn = gr.Button("Refresh")
                navigate_up_btn = gr.Button("⬆️ Go Up")
                delete_btn = gr.Button("Delete", variant="stop")
            
            with gr.Column(scale=2):
                file_content = gr.Code(
                    value="",
                    label="File Content",
                    language="python"
                )
                save_btn = gr.Button("Save File")
                status_msg = gr.Markdown("")
        
        def update_file_list(path):
            config = load_config()
            files = list_workspace_files(path)
            choices = []
            for f in files:
                icon = "📁" if f["is_dir"] else "📄"
                choices.append(f"{icon} {f['name']}")
            
            return gr.Dropdown(choices=choices, value=None), path or "/"
        
        def handle_selection(selection, current_path_val):
            if not selection:
                return "", current_path_val, "", ""
            
            # Extract filename without icon
            filename = selection[2:] if len(selection) > 2 else selection
            
            config = load_config()
            full_path = config.workspace_path / current_path_val / filename
            
            if full_path.is_dir():
                # Navigate into directory
                new_path = str(Path(current_path_val) / filename) if current_path_val else filename
                files = list_workspace_files(new_path)
                choices = []
                for f in files:
                    icon = "📁" if f["is_dir"] else "📄"
                    choices.append(f"{icon} {f['name']}")
                
                return new_path, gr.Dropdown(choices=choices, value=None), "", f"Navigated to {new_path}"
            else:
                # Read file
                file_path = str(Path(current_path_val) / filename) if current_path_val else filename
                content = read_workspace_file(file_path)
                return current_path_val, gr.Dropdown(), content, f"Loaded {file_path}"
        
        def go_up(current_path_val):
            if current_path_val:
                parent = str(Path(current_path_val).parent)
                if parent == ".":
                    parent = ""
                return update_file_list(parent) + ("",)
            return "", gr.Dropdown(), "", "", "Already at root"
        
        def save_file(current_path_val, selection, content):
            if not selection:
                return "Please select a file first"
            
            filename = selection[2:] if len(selection) > 2 else selection
            file_path = str(Path(current_path_val) / filename) if current_path_val else filename
            
            return write_workspace_file(file_path, content)
        
        def delete_item(current_path_val, selection):
            if not selection:
                return "Please select a file or directory first"
            
            filename = selection[2:] if len(selection) > 2 else selection
            file_path = str(Path(current_path_val) / filename) if current_path_val else filename
            
            result = delete_workspace_file(file_path)
            # Refresh list
            files = list_workspace_files(current_path_val)
            choices = []
            for f in files:
                icon = "📁" if f["is_dir"] else "📄"
                choices.append(f"{icon} {f['name']}")
            
            return gr.Dropdown(choices=choices, value=None), "", result
        
        # Initial load
        refresh_btn.click(
            fn=update_file_list,
            inputs=current_path,
            outputs=[file_list, path_display]
        )
        
        file_list.change(
            fn=handle_selection,
            inputs=[file_list, current_path],
            outputs=[current_path, file_list, file_content, status_msg]
        )
        
        navigate_up_btn.click(
            fn=go_up,
            inputs=current_path,
            outputs=[current_path, file_list, file_content, path_display, status_msg]
        )
        
        save_btn.click(
            fn=save_file,
            inputs=[current_path, file_list, file_content],
            outputs=status_msg
        )
        
        delete_btn.click(
            fn=delete_item,
            inputs=[current_path, file_list],
            outputs=[file_list, file_content, status_msg]
        )
        
        # Initial load on tab open
        refresh_btn.click(fn=update_file_list, inputs=current_path, outputs=[file_list, path_display])


with gr.Blocks(title="Vikingbot Console") as demo:
    with gr.Tabs():
        create_dashboard_tab()
        create_config_tab()
        create_sessions_tab()
        create_workspace_tab()


if __name__ == "__main__":
    port = 18791
    if len(sys.argv) > 1:
        try:
            port = int(sys.argv[1])
        except ValueError:
            pass
    demo.launch(server_name="0.0.0.0", server_port=port, share=False)
