from typing import Any, Dict
from pathlib import Path

from ..base import Hook, HookContext
from ...config.loader import get_data_dir
from openviking.message import TextPart
from loguru import logger

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

    async def _get_client(self, session_key: str) -> ov.AsyncOpenViking:
        if not self._client:
            ov_data_path = get_data_dir() / "ov_data"
            ov_data_path.mkdir(parents=True, exist_ok=True)
            client = ov.AsyncOpenViking(path=str(ov_data_path))
            await client.initialize()
            self._client = client
        return self._client

    async def execute(self, context: HookContext, **kwargs) -> Any:
        vikingbot_session: Session = kwargs.get("session", {})
        session_id = context.session_id

        try:
            client = await self._get_client(session_id)
            session = client.session(session_id)
            #await session.delete()
            for message in vikingbot_session.messages:
                session.add_message(
                    message.get('role'),
                    [TextPart(text=message.get("content"))]
                )
            session.commit()
            logger.debug(f"Message added to OpenViking session {session_id}")
            return {"success": True}
        except Exception as e:
            logger.exception(f"Failed to add message to OpenViking: {e}")
            return {"success": False, "error": str(e)}


hooks = {
    'message.compact':[
        OpenVikingCompactHook()
    ]
}