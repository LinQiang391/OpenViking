"""HTMX Partials API - Full Version"""

from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from typing import Optional

from vikingbot.config.loader import load_config, save_config
from vikingbot.config.schema import Config, ProvidersConfig, AgentsConfig, ChannelsConfig, ToolsConfig, SandboxConfig
from vikingbot.session.manager import SessionManager
from vikingbot.utils.helpers import get_workspace_path

router = APIRouter()


ALL_PROVIDERS = [
    ('openrouter', 'OpenRouter'),
    ('anthropic', 'Anthropic'),
    ('openai', 'OpenAI'),
    ('deepseek', 'DeepSeek'),
    ('groq', 'Groq'),
    ('gemini', 'Gemini'),
    ('minimax', 'MiniMax'),
    ('aihubmix', 'AiHubMix'),
    ('dashscope', 'DashScope'),
    ('moonshot', 'Moonshot'),
    ('zhipu', 'Zhipu'),
    ('vllm', 'vLLM'),
]

ALL_CHANNELS = [
    ('telegram', 'Telegram'),
    ('discord', 'Discord'),
    ('whatsapp', 'WhatsApp'),
    ('feishu', 'Feishu (飞书)'),
    ('mochat', 'MoChat'),
    ('dingtalk', 'DingTalk (钉钉)'),
    ('slack', 'Slack'),
    ('email', 'Email'),
    ('qq', 'QQ'),
]


def render_dashboard():
    """Render dashboard partial"""
    config = load_config()
    session_manager = SessionManager(config.workspace_path)
    sessions = session_manager.list_sessions()
    
    html = f'''
        <div class="flex gap-5 p-4 bg-gray-50 border border-gray-200 rounded-lg mb-5">
            <div class="flex items-center gap-2">
                <span class="w-2.5 h-2.5 bg-green-500 rounded-full"></span>
                Running
            </div>
            <div class="flex items-center gap-2">
                Version: 0.1.0
            </div>
            <div class="flex items-center gap-2">
                Sessions: {len(sessions)}
            </div>
        </div>
        
        <div class="bg-white border border-gray-200 rounded-lg p-5 mb-5 shadow-sm">
            <h3 class="text-lg font-semibold text-gray-700 mb-4">Quick Stats</h3>
            <div class="grid grid-cols-3 gap-5">
                <div class="p-5 bg-gray-50 rounded-lg border border-gray-200">
                    <div class="text-4xl text-blue-600">{len(sessions)}</div>
                    <div class="text-gray-500">Sessions</div>
                </div>
                <div class="p-5 bg-gray-50 rounded-lg border border-gray-200">
                    <div class="text-4xl text-green-500">✓</div>
                    <div class="text-gray-500">Gateway</div>
                </div>
                <div class="p-5 bg-gray-50 rounded-lg border border-gray-200">
                    <div class="text-sm text-gray-500 break-all">{str(config.workspace_path)}</div>
                    <div class="text-gray-400 mt-1.5">Config Path</div>
                </div>
            </div>
        </div>
    '''
    return HTMLResponse(content=html)


def render_config_base(active_tab: str = 'providers'):
    """Render config base with tabs"""
    tabs_html = ''
    for tab_id, tab_label in [('providers', 'Providers'), ('agents', 'Agents'), ('channels', 'Channels'), ('tools', 'Tools'), ('sandbox', 'Sandbox')]:
        active_class = 'bg-blue-600 text-white border-blue-600' if tab_id == active_tab else 'bg-gray-100 text-gray-700 border-gray-300 hover:bg-gray-200'
        tabs_html += f'''
            <button class="px-5 py-2 border rounded-lg transition {active_class}"
                    hx-get="/api/v1/partials/config/{tab_id}"
                    hx-target="#config-content"
                    hx-swap="innerHTML">
                {tab_label}
            </button>
        '''
    
    html = f'''
        <div class="bg-white border border-gray-200 rounded-lg p-5 shadow-sm">
            <div class="flex justify-between items-center mb-5">
                <h3 class="text-lg font-semibold text-gray-700">Configuration</h3>
                <div class="flex gap-2.5">
                    <button class="px-4 py-2 bg-blue-600 text-white rounded hover:bg-blue-700 transition"
                            hx-get="/api/v1/partials/config/json"
                            hx-target="#config-content"
                            hx-swap="innerHTML">
                        JSON Mode
                    </button>
                </div>
            </div>
            
            <div class="flex gap-2 mb-4 border-b border-gray-200 pb-4">
                {tabs_html}
            </div>
            
            <div id="config-content">
                {render_config_providers()}
            </div>
        </div>
    '''
    return HTMLResponse(content=html)


def render_config_providers():
    """Render providers config form"""
    config = load_config()
    providers_dict = config.providers.model_dump() if config.providers else {}
    
    configured_html = ''
    for provider_id, provider_name in ALL_PROVIDERS:
        if provider_id in providers_dict and providers_dict[provider_id]:
            provider_config = providers_dict[provider_id]
            api_key = provider_config.get('api_key', '')
            api_base = provider_config.get('api_base', '')
            extra_headers = provider_config.get('extra_headers', {})
            extra_headers_str = str(extra_headers) if extra_headers else ''
            
            configured_html += f'''
                <div class="bg-gray-50 border border-gray-200 rounded-lg p-4 mb-4">
                    <div class="flex justify-between items-center mb-3">
                        <strong class="text-gray-800">{provider_name}</strong>
                    </div>
                    <div class="mb-3">
                        <label class="block mb-1.5 font-medium text-gray-700">API Key</label>
                        <input type="password" class="w-full px-3 py-2 border border-gray-300 rounded bg-white text-gray-800" placeholder="sk-..." value="{api_key}">
                    </div>
                    <div class="grid grid-cols-2 gap-4">
                        <div class="mb-0">
                            <label class="block mb-1.5 font-medium text-gray-700">API Base</label>
                            <input type="text" class="w-full px-3 py-2 border border-gray-300 rounded bg-white text-gray-800" placeholder="" value="{api_base}">
                            <p class="text-xs text-gray-500 mt-1">Optional custom API base URL</p>
                        </div>
                        <div class="mb-0">
                            <label class="block mb-1.5 font-medium text-gray-700">Extra Headers (JSON)</label>
                            <input type="text" class="w-full px-3 py-2 border border-gray-300 rounded bg-white text-gray-800" placeholder="" value="{extra_headers_str}">
                            <p class="text-xs text-gray-500 mt-1">Optional custom headers as JSON</p>
                        </div>
                    </div>
                </div>
            '''
    
    if not configured_html:
        configured_html = '<p class="text-gray-500">No providers configured yet.</p>'
    
    html = f'''
        <div class="form-section">
            <h4 class="mb-4 text-gray-800">Configured Providers</h4>
            {configured_html}
            <p class="text-gray-500 text-sm mt-4">Use JSON mode for full provider configuration.</p>
        </div>
    '''
    return html


def render_config_agents():
    """Render agents config form"""
    config = load_config()
    agents = config.agents.defaults if config.agents else None
    
    html = f'''
        <div class="form-section">
            <h4 class="mb-4 text-gray-800">Agent Defaults</h4>
            
            <div class="mb-3">
                <label class="block mb-1.5 font-medium text-gray-700">Workspace</label>
                <input type="text" class="w-full px-3 py-2 border border-gray-300 rounded bg-white text-gray-800" placeholder="~/.vikingbot/workspace/default" value="{agents.workspace if agents else ''}">
                <p class="text-xs text-gray-500 mt-1">Workspace directory path</p>
            </div>
            
            <div class="mb-3">
                <label class="block mb-1.5 font-medium text-gray-700">Model <span class="text-red-600">*</span></label>
                <input type="text" class="w-full px-3 py-2 border border-gray-300 rounded bg-white text-gray-800" placeholder="anthropic/claude-opus-4-5" value="{agents.model if agents else ''}">
                <p class="text-xs text-gray-500 mt-1">Default model to use for the agent (e.g., anthropic/claude-opus-4-5, openai/gpt-4)</p>
            </div>
            
            <div class="grid grid-cols-2 gap-4 mb-3">
                <div class="mb-0">
                    <label class="block mb-1.5 font-medium text-gray-700">Max Tokens</label>
                    <input type="number" class="w-full px-3 py-2 border border-gray-300 rounded bg-white text-gray-800" value="{agents.max_tokens if agents and agents.max_tokens else 8192}">
                </div>
                <div class="mb-0">
                    <label class="block mb-1.5 font-medium text-gray-700">Temperature</label>
                    <input type="number" step="0.1" class="w-full px-3 py-2 border border-gray-300 rounded bg-white text-gray-800" value="{agents.temperature if agents and agents.temperature else 0.7}">
                </div>
            </div>
            
            <div class="grid grid-cols-2 gap-4 mb-3">
                <div class="mb-0">
                    <label class="block mb-1.5 font-medium text-gray-700">Max Tool Iterations</label>
                    <input type="number" class="w-full px-3 py-2 border border-gray-300 rounded bg-white text-gray-800" value="{agents.max_tool_iterations if agents and agents.max_tool_iterations else 50}">
                    <p class="text-xs text-gray-500 mt-1">Maximum number of tool calls per agent run</p>
                </div>
                <div class="mb-0">
                    <label class="block mb-1.5 font-medium text-gray-700">Memory Window</label>
                    <input type="number" class="w-full px-3 py-2 border border-gray-300 rounded bg-white text-gray-800" value="{agents.memory_window if agents and agents.memory_window else 50}">
                    <p class="text-xs text-gray-500 mt-1">Number of recent messages to include in context</p>
                </div>
            </div>
            
            <div class="mb-0">
                <label class="block mb-1.5 font-medium text-gray-700">Image Generation Model</label>
                <input type="text" class="w-full px-3 py-2 border border-gray-300 rounded bg-white text-gray-800" placeholder="" value="{agents.gen_image_model if agents else ''}">
                <p class="text-xs text-gray-500 mt-1">Model to use for image generation (optional)</p>
            </div>
        </div>
    '''
    return html


def render_config_channels():
    """Render channels config form"""
    config = load_config()
    channels = config.channels if config.channels else []
    
    configured_html = ''
    for idx, ch in enumerate(channels):
        channel_type = ch.get('type', 'unknown')
        channel_name = dict(ALL_CHANNELS).get(channel_type, channel_type)
        
        configured_html += f'''
            <div class="bg-gray-50 border border-gray-200 rounded-lg p-4 mb-4">
                <div class="flex justify-between items-center mb-3">
                    <strong class="text-gray-800">{channel_name}</strong>
                    <button class="px-3 py-1.5 bg-red-600 text-white rounded text-sm hover:bg-red-700 transition"
                            hx-post="/api/v1/partials/channels/delete/{idx}"
                            hx-target="#config-content"
                            hx-swap="innerHTML">
                        Remove
                    </button>
                </div>
                <div class="mb-3">
                    <label class="block mb-1.5 font-medium text-gray-700">Enabled</label>
                    <input type="checkbox" class="w-4 h-4 text-blue-600" {'checked' if ch.get('enabled') else ''}>
                </div>
                <p class="text-xs text-gray-500">Use JSON mode for full channel configuration.</p>
            </div>
        '''
    
    if not configured_html:
        configured_html = '<p class="text-gray-500">No channels configured yet.</p>'
    
    options_html = ''.join([f'<option value="{pid}">{pname}</option>' for pid, pname in ALL_CHANNELS])
    
    html = f'''
        <div class="form-section">
            <h4 class="mb-4 text-gray-800">Configured Channels</h4>
            {configured_html}
            
            <div class="mt-5">
                <h4 class="mb-4 text-gray-800">Add Channel</h4>
                <div class="grid grid-cols-2 gap-4">
                    <div>
                        <select id="new-channel-type" class="w-full px-3 py-2 border border-gray-300 rounded bg-white">
                            <option value="">Select a channel type...</option>
                            {options_html}
                        </select>
                    </div>
                    <div>
                        <button class="w-full px-4 py-2 bg-green-600 text-white rounded hover:bg-green-700 transition"
                                hx-post="/api/v1/partials/channels/add"
                                hx-include="[id='new-channel-type']"
                                hx-target="#config-content"
                                hx-swap="innerHTML">
                            Add Channel
                        </button>
                    </div>
                </div>
            </div>
            
            <div class="mt-5 p-4 bg-yellow-50 border border-yellow-300 rounded-lg">
                <strong>💡 Tip:</strong> For detailed channel configuration, switch to JSON mode. You can have multiple channels of the same type.
            </div>
        </div>
    '''
    return html


def render_config_tools():
    """Render tools config form"""
    config = load_config()
    tools = config.tools if config.tools else None
    
    restrict_checked = 'checked' if (tools and tools.restrict_to_workspace) else ''
    web_api_key = tools.web.search.api_key if (tools and tools.web and tools.web.search) else ''
    web_max_results = tools.web.search.max_results if (tools and tools.web and tools.web.search) else 5
    exec_timeout = tools.exec.timeout if (tools and tools.exec) else 60
    
    html = f'''
        <div class="form-section">
            <h4 class="mb-4 text-gray-800">Tools</h4>
            
            <div class="mb-3">
                <label class="block mb-1.5 font-medium text-gray-700">
                    <input type="checkbox" class="w-4 h-4 text-blue-600 mr-2" {restrict_checked}>
                    Restrict to Workspace
                </label>
                <p class="text-xs text-gray-500">When enabled, restricts all agent tools (shell, file operations) to the workspace directory for security</p>
            </div>
            
            <div class="mb-3">
                <label class="block mb-1.5 font-medium text-gray-700">Web Search API Key</label>
                <input type="password" class="w-full px-3 py-2 border border-gray-300 rounded bg-white text-gray-800" placeholder="" value="{web_api_key}">
                <p class="text-xs text-gray-500 mt-1">Optional API key for web search capability. Get from https://brave.com/search/api/</p>
            </div>
            
            <div class="grid grid-cols-2 gap-4 mb-3">
                <div class="mb-0">
                    <label class="block mb-1.5 font-medium text-gray-700">Web Search Max Results</label>
                    <input type="number" class="w-full px-3 py-2 border border-gray-300 rounded bg-white text-gray-800" value="{web_max_results}">
                    <p class="text-xs text-gray-500 mt-1">Maximum number of search results to return</p>
                </div>
                <div class="mb-0">
                    <label class="block mb-1.5 font-medium text-gray-700">Exec Timeout (seconds)</label>
                    <input type="number" class="w-full px-3 py-2 border border-gray-300 rounded bg-white text-gray-800" value="{exec_timeout}">
                    <p class="text-xs text-gray-500 mt-1">Timeout for shell exec commands</p>
                </div>
            </div>
        </div>
    '''
    return html


def render_config_sandbox():
    """Render sandbox config form"""
    config = load_config()
    sandbox = config.sandbox if config.sandbox else None
    
    sandbox_checked = 'checked' if (sandbox and sandbox.enabled) else ''
    backend_srt_selected = 'selected' if (sandbox and sandbox.backend == 'srt') else ''
    backend_docker_selected = 'selected' if (sandbox and sandbox.backend == 'docker') else ''
    mode_per_session_selected = 'selected' if (sandbox and sandbox.mode == 'per-session') else ''
    mode_shared_selected = 'selected' if (sandbox and sandbox.mode == 'shared') else ''
    
    html = f'''
        <div class="form-section">
            <h4 class="mb-4 text-gray-800">Sandbox</h4>
            
            <div class="mb-3">
                <label class="block mb-1.5 font-medium text-gray-700">
                    <input type="checkbox" class="w-4 h-4 text-blue-600 mr-2" {sandbox_checked}>
                    Enable Sandbox
                </label>
                <p class="text-xs text-gray-500">Enable sandboxed execution for enhanced security. Requires Node.js for SRT backend.</p>
            </div>
            
            <div class="grid grid-cols-2 gap-4 mb-3">
                <div class="mb-0">
                    <label class="block mb-1.5 font-medium text-gray-700">Backend</label>
                    <select class="w-full px-3 py-2 border border-gray-300 rounded bg-white">
                        <option value="srt" {backend_srt_selected}>SRT (Anthropic Sandbox Runtime)</option>
                        <option value="docker" {backend_docker_selected}>Docker</option>
                    </select>
                    <p class="text-xs text-gray-500 mt-1">Sandbox backend to use</p>
                </div>
                <div class="mb-0">
                    <label class="block mb-1.5 font-medium text-gray-700">Mode</label>
                    <select class="w-full px-3 py-2 border border-gray-300 rounded bg-white">
                        <option value="per-session" {mode_per_session_selected}>Per Session (Isolated)</option>
                        <option value="shared" {mode_shared_selected}>Shared</option>
                    </select>
                    <p class="text-xs text-gray-500 mt-1">Per-session mode creates isolated workspace for each session</p>
                </div>
            </div>
        </div>
    '''
    return html


def render_config_json():
    """Render config in JSON mode"""
    config = load_config()
    config_json = config.model_dump_json(indent=2)
    
    html = f'''
        <div class="mt-4">
            <textarea id="config-editor" class="w-full p-3 bg-white border border-gray-300 rounded-lg text-gray-800 font-mono text-sm resize-y min-h-[400px] focus:outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-100">
{config_json}
            </textarea>
            <div class="mt-4 flex gap-2.5">
                <button class="px-4 py-2 bg-blue-600 text-white rounded hover:bg-blue-700 transition"
                        hx-post="/api/v1/partials/config/save"
                        hx-include="[id='config-editor']"
                        hx-target="#config-content"
                        hx-swap="innerHTML">
                    Save Config
                </button>
                <button class="px-4 py-2 bg-gray-500 text-white rounded hover:bg-gray-600 transition"
                        hx-get="/api/v1/partials/config"
                        hx-target="#config-content"
                        hx-swap="innerHTML">
                    Back to Form
                </button>
            </div>
        </div>
    '''
    return html


def render_sessions():
    """Render sessions partial"""
    config = load_config()
    session_manager = SessionManager(config.workspace_path)
    sessions = session_manager.list_sessions()
    
    rows_html = ''
    for s in sessions:
        created_at = s.get('created_at', '-')[:16] if s.get('created_at') else '-'
        updated_at = s.get('updated_at', '-')[:16] if s.get('updated_at') else '-'
        msg_count = s.get('message_count', 0)
        session_key = s['key']
        
        rows_html += f'''
            <tr class="hover:bg-gray-50">
                <td class="p-3 border-b border-gray-200">
                    <span class="text-blue-600 cursor-pointer"
                          hx-get="/api/v1/partials/sessions/{session_key}"
                          hx-target="#sessions-content"
                          hx-swap="innerHTML">
                        {session_key}
                    </span>
                </td>
                <td class="p-3 border-b border-gray-200">{created_at}</td>
                <td class="p-3 border-b border-gray-200">{updated_at}</td>
                <td class="p-3 border-b border-gray-200">{msg_count}</td>
                <td class="p-3 border-b border-gray-200">
                    <button class="px-3 py-1.5 bg-red-600 text-white rounded text-sm hover:bg-red-700 transition">
                        Delete
                    </button>
                </td>
            </tr>
        '''
    
    html = f'''
        <div class="bg-white border border-gray-200 rounded-lg p-5 shadow-sm">
            <h3 class="text-lg font-semibold text-gray-700 mb-4">Sessions</h3>
            {'<p class="text-gray-500">No sessions found</p>' if len(sessions) == 0 else f'''
            <div id="sessions-content">
                <table class="w-full border-collapse">
                    <thead>
                        <tr>
                            <th class="p-3 text-left text-gray-500 font-semibold border-b border-gray-200">Session</th>
                            <th class="p-3 text-left text-gray-500 font-semibold border-b border-gray-200">Created</th>
                            <th class="p-3 text-left text-gray-500 font-semibold border-b border-gray-200">Updated</th>
                            <th class="p-3 text-left text-gray-500 font-semibold border-b border-gray-200">Messages</th>
                            <th class="p-3 text-left text-gray-500 font-semibold border-b border-gray-200"></th>
                        </tr>
                    </thead>
                    <tbody>
                        {rows_html}
                    </tbody>
                </table>
            </div>
            '''}
        </div>
    '''
    return HTMLResponse(content=html)


def render_session_detail(session_key: str):
    """Render session detail"""
    from vikingbot.utils.helpers import safe_filename
    from pathlib import Path
    import json
    
    config = load_config()
    
    try:
        sessions_dir = Path.home() / ".vikingbot" / "sessions"
        safe_key = safe_filename(session_key.replace(":", "_"))
        session_path = sessions_dir / f"{safe_key}.jsonl"
        
        messages_html = ''
        
        if session_path.exists():
            with open(session_path, 'r') as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            msg = json.loads(line)
                            role = msg.get('role', 'unknown')
                            content = msg.get('content', '')
                            messages_html += f'''
                                <div class="p-3 rounded-lg mb-2 border border-gray-200 {'bg-blue-50' if role == 'user' else 'bg-gray-50'}">
                                    <div class="font-semibold mb-1 text-blue-600">{role}</div>
                                    <div>{content}</div>
                                </div>
                            '''
                        except Exception:
                            pass
        
        html = f'''
            <div class="mt-5">
                <div class="flex justify-between items-center mb-4">
                    <h4 class="text-lg font-semibold text-gray-700">Session: {session_key}</h4>
                    <button class="px-4 py-2 bg-gray-500 text-white rounded hover:bg-gray-600 transition"
                            hx-get="/api/v1/partials/sessions"
                            hx-target="#sessions-content"
                            hx-swap="innerHTML">
                        Back
                    </button>
                </div>
                {messages_html if messages_html else '<p class="text-gray-500">No messages found</p>'}
            </div>
        '''
    except Exception as e:
        html = f'<p class="text-red-600">Error loading session: {e}</p>'
    
    return HTMLResponse(content=html)


def render_workspace():
    """Render workspace partial"""
    config = load_config()
    workspace_path = get_workspace_path()
    
    # Get list of workspaces (sessions)
    session_manager = SessionManager(config.workspace_path)
    sessions = session_manager.list_sessions()
    
    workspace_options = '<option value="default">Default Workspace</option>'
    for s in sessions:
        session_key = s['key']
        workspace_options += f'<option value="session-{session_key}">Session: {session_key}</option>'
    
    html = f'''
        <div class="bg-white border border-gray-200 rounded-lg p-5 shadow-sm">
            <div class="flex justify-between items-center mb-5">
                <h3 class="text-lg font-semibold text-gray-700">Workspace</h3>
                <select id="workspace-select" class="px-3 py-2 border border-gray-300 rounded bg-white min-w-[200px]">
                    {workspace_options}
                </select>
            </div>
            
            <div class="flex gap-5">
                <div class="w-[300px] flex-shrink-0">
                    <div id="file-tree" class="text-gray-500">
                        File browser - use JSON config for full workspace access
                        <div class="mt-3 p-3 bg-gray-50 rounded border border-gray-200">
                            <div class="text-xs text-gray-500">Default Workspace:</div>
                            <div class="text-sm text-gray-700 break-all mt-1">{str(workspace_path)}</div>
                        </div>
                    </div>
                </div>
                <div class="flex-1">
                    <div class="mb-4 hidden" id="editor-toolbar">
                        <button class="px-4 py-2 bg-blue-600 text-white rounded hover:bg-blue-700 transition">Save</button>
                        <span class="ml-4 text-gray-500" id="current-file"></span>
                    </div>
                    <textarea id="file-editor" class="hidden w-full p-3 bg-white border border-gray-300 rounded-lg text-gray-800 font-mono text-sm resize-y min-h-[400px]" placeholder="Select a file to edit..."></textarea>
                </div>
            </div>
        </div>
    '''
    return HTMLResponse(content=html)


@router.get("/partials/dashboard")
async def get_dashboard_partial():
    return render_dashboard()


@router.get("/partials/config")
async def get_config_partial():
    return render_config_base()


@router.get("/partials/config/providers")
async def get_config_providers():
    return HTMLResponse(content=render_config_providers())


@router.get("/partials/config/agents")
async def get_config_agents():
    return HTMLResponse(content=render_config_agents())


@router.get("/partials/config/channels")
async def get_config_channels():
    return HTMLResponse(content=render_config_channels())


@router.get("/partials/config/tools")
async def get_config_tools():
    return HTMLResponse(content=render_config_tools())


@router.get("/partials/config/sandbox")
async def get_config_sandbox():
    return HTMLResponse(content=render_config_sandbox())


@router.get("/partials/config/json")
async def get_config_json():
    return HTMLResponse(content=render_config_json())


@router.post("/partials/config/save")
async def save_config_endpoint(request: Request):
    """Save config from JSON mode"""
    form_data = await request.form()
    config_json = form_data.get('config-editor', '')
    
    try:
        import json
        config_dict = json.loads(config_json)
        config = Config(**config_dict)
        save_config(config)
        return render_config_base()
    except Exception as e:
        return HTMLResponse(content=f'<p class="text-red-600">Error saving config: {e}</p>')


@router.get("/partials/channels/add/{channel_type}")
async def add_channel_endpoint(channel_type: str):
    """Add a new channel"""
    if channel_type:
        config = load_config()
        if not config.channels:
            config.channels = []
        config.channels.append({
            'type': channel_type,
            'enabled': False
        })
        save_config(config)
    
    return HTMLResponse(content=render_config_channels())


@router.get("/partials/channels/delete/{idx}")
async def delete_channel_endpoint(idx: int):
    """Delete a channel"""
    config = load_config()
    if config.channels and len(config.channels) > idx:
        config.channels.pop(idx)
        save_config(config)
    
    return HTMLResponse(content=render_config_channels())


@router.get("/partials/sessions")
async def get_sessions_partial():
    return render_sessions()


@router.get("/partials/sessions/{session_key}")
async def get_session_detail_partial(session_key: str):
    return render_session_detail(session_key)


@router.get("/partials/workspace")
async def get_workspace_partial():
    return render_workspace()
