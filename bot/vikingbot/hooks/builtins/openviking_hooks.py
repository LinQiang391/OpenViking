from typing import Any

from loguru import logger

from vikingbot.openviking_mount.ov_server import VikingClient
from ..base import Hook, HookContext
from ...session import Session

try:
    import openviking as ov

    HAS_OPENVIKING = True
except ImportError:
    HAS_OPENVIKING = False


class OpenVikingCompactHook(Hook):
    name = "openviking_compact"

    def __init__(self):
        self._client = None

    def _get_client(self, session_key: str) -> VikingClient:
        if not self._client:
            client = VikingClient()
            self._client = client
        return self._client

    async def execute(self, context: HookContext, **kwargs) -> Any:
        vikingbot_session: Session = kwargs.get("session", {})
        session_id = context.session_id

        try:
            client = self._get_client(session_id)
            result = await client.commit(session_id, vikingbot_session.messages)
            return result
        except Exception as e:
            logger.exception(f"Failed to add message to OpenViking: {e}")
            return {"success": False, "error": str(e)}


class OpenVikingPostCallHook(Hook):
    name = "openviking_post_call"
    is_sync = True

    def __init__(self):
        self._client = None

    async def _get_client(self, session_key: str) -> ov.AsyncOpenViking:
        if not self._client:
            ov_data_path = get_data_dir() / "ov_data"
            ov_data_path.mkdir(parents=True, exist_ok=True)
            client = ov.AsyncOpenViking(path=str(ov_data_path))
            await client.initialize()
            self._client = client
        return self._client

    async def execute(self, context: HookContext, **kwargs) -> Any:
        if kwargs.get('result'):
            if not isinstance(kwargs.get('result'), Exception):
                kwargs['result'] = f'hahahahahaha:\n{kwargs.get('result')}'
        return kwargs


hooks = {
    'message.compact':[
        OpenVikingCompactHook()
    ],
    'tool.post_call':[
        OpenVikingPostCallHook()
    ]
}