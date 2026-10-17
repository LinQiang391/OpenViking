import base64
import io
import types
from unittest.mock import AsyncMock, Mock

import pytest
from PIL import Image

from openviking.core.context import ResourceContentType
from openviking.parse.parsers.media.utils import (
    MPEG_TS_PACKET_SIZE,
    MPEG_TS_PROBE_BYTES,
)
from openviking.storage.index_action import FieldPatch
from openviking.utils import embedding_utils
from openviking.utils.embedding_input import EMBEDDING_TRUNCATION_SUFFIX
from openviking.utils.ingest_options import IngestOptions
from openviking_cli.utils.config.parser_config import ImageConfig


class DummyQueue:
    def __init__(self):
        self.items = []

    async def enqueue(self, msg):
        self.items.append(msg)
        return "queue-message-id"


class DummyQueueWithId(DummyQueue):
    async def enqueue(self, msg):
        self.items.append(msg)
        return "queue-message-id"


class FailingQueue(DummyQueue):
    async def enqueue(self, msg):
        self.items.append(msg)
        raise RuntimeError("queue unavailable")


class DummyQueueManager:
    EMBEDDING = "embedding"

    def __init__(self, queue):
        self._queue = queue

    def get_queue(self, _name):
        return self._queue


class DummyFS:
    def __init__(self, content, *, account_backends=None):
        self.content = content
        self.read_calls = []
        self.read_file_calls = 0
        self.read_file_bytes_calls = 0
        self.vector_store = self
        self._vector_config_resolver = self
        self.account_backends = (
            {"default": "local"} if account_backends is None else account_backends
        )
        self.resolve_calls = []
        self._get_vector_store = Mock(side_effect=AssertionError("unexpected vector store lookup"))
        self.get_content_policy = AsyncMock(
            side_effect=AssertionError("producer must not query content policy")
        )
        self.account_uses_content_field = AsyncMock(
            side_effect=AssertionError("producer must not query content readiness")
        )
        self.get_account_backend = AsyncMock(
            side_effect=AssertionError("producer must reuse resolved vector settings")
        )

    async def resolve(self, account_id):
        self.resolve_calls.append(account_id)
        return types.SimpleNamespace(
            embedding=embedding_utils.get_openviking_config().embedding,
            vectordb=types.SimpleNamespace(backend=self.account_backends[account_id]),
        )

    async def read(self, _path, offset=0, size=-1, ctx=None):
        self.read_calls.append((offset, size))
        raw = self.content if isinstance(self.content, bytes) else str(self.content).encode("utf-8")
        return raw[offset : offset + size]

    async def read_file(self, _path, ctx=None):
        self.read_file_calls += 1
        return self.content

    async def read_file_bytes(self, _path, ctx=None):
        self.read_file_bytes_calls += 1
        if isinstance(self.content, bytes):
            return self.content
        return str(self.content).encode("utf-8")

    async def exists(self, _path, ctx=None):
        return False

    async def ls(self, _uri, ctx=None):
        return []


class DummyUser:
    account_id = "default"
    user_id = "default"

    def user_space_name(self):
        return "default"

    def to_dict(self):
        return {"account_id": self.account_id, "user_id": self.user_id}


class DummyReq:
    def __init__(self):
        self.user = DummyUser()
        self.account_id = "default"


def _jpeg_bytes(width: int, height: int) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), "white").save(buf, format="JPEG")
    return buf.getvalue()


def _data_uri_image_size(data_uri: str) -> tuple[int, int]:
    _, encoded = data_uri.split(";base64,", 1)
    with Image.open(io.BytesIO(base64.b64decode(encoded))) as img:
        return img.size


def test_get_resource_content_type_recognizes_media_extensions():
    expected = {
        "recording.ogg": ResourceContentType.AUDIO,
        "RECORDING.OPUS": ResourceContentType.AUDIO,
        "recording.mkv": ResourceContentType.VIDEO,
        "RECORDING.WEBM": ResourceContentType.VIDEO,
        "source.ts": ResourceContentType.TEXT,
    }

    for filename, content_type in expected.items():
        assert embedding_utils.get_resource_content_type(filename) == content_type


def _mpeg_ts_bytes() -> bytes:
    content = bytearray(MPEG_TS_PROBE_BYTES)
    for offset in range(0, MPEG_TS_PROBE_BYTES, MPEG_TS_PACKET_SIZE):
        content[offset] = 0x47
    return bytes(content)


@pytest.mark.asyncio
async def test_vectorize_disambiguates_typescript_and_mpeg_ts(monkeypatch):
    monkeypatch.setattr(
        embedding_utils,
        "get_openviking_config",
        lambda: types.SimpleNamespace(
            embedding=types.SimpleNamespace(text_source="content_only", max_input_tokens=1000)
        ),
    )

    async def vectorize(filename, content, summary):
        queue = DummyQueue()
        fs = DummyFS(content)
        monkeypatch.setattr(
            embedding_utils,
            "get_queue_manager",
            lambda: DummyQueueManager(queue),
        )
        monkeypatch.setattr(embedding_utils, "get_viking_fs", lambda: fs)
        await embedding_utils.vectorize_file(
            file_path=f"viking://user/default/resources/{filename}",
            summary_dict={"name": filename, "summary": summary},
            parent_uri="viking://user/default/resources",
            ctx=DummyReq(),
        )
        return queue, fs

    text_queue, text_fs = await vectorize(
        "source.ts", "export const answer: number = 42;", "TypeScript source"
    )
    video_queue, video_fs = await vectorize("broadcast.ts", _mpeg_ts_bytes(), "")

    assert text_fs.read_file_calls == 1
    assert text_queue.items[0].message == "export const answer: number = 42;"
    assert "content" not in text_queue.items[0].context_data
    assert video_fs.read_file_calls == 0
    assert video_queue.items[0].message == "broadcast.ts"
    assert "content" not in video_queue.items[0].context_data


@pytest.mark.asyncio
@pytest.mark.parametrize("text_source", ["summary_first", "summary_only"])
@pytest.mark.parametrize("summary", ["short summary", ""])
async def test_vectorize_file_uses_summary_first(monkeypatch, text_source, summary):
    from openviking_cli.utils.config.embedding_config import EmbeddingConfig

    cfg = EmbeddingConfig(
        dense={
            "provider": "openai",
            "model": "text-embedding-3-small",
            "api_base": "http://localhost:8080/v1",
            "dimension": 1536,
        },
        text_source=text_source,
        max_input_tokens=1000,
    )
    queue = DummyQueue()
    monkeypatch.setattr(embedding_utils, "get_queue_manager", lambda: DummyQueueManager(queue))
    monkeypatch.setattr(embedding_utils, "get_viking_fs", lambda: DummyFS("raw content"))
    monkeypatch.setattr(
        embedding_utils,
        "get_openviking_config",
        lambda: types.SimpleNamespace(embedding=cfg),
    )
    await embedding_utils.vectorize_file(
        file_path="viking://user/default/resources/test.md",
        summary_dict={"name": "test.md", "summary": summary},
        parent_uri="viking://user/default/resources",
        ctx=DummyReq(),
    )

    assert len(queue.items) == 1
    assert queue.items[0].message == (summary or "raw content")
    assert "content" not in queue.items[0].context_data


@pytest.mark.asyncio
async def test_vectorize_file_threads_supplied_md5_without_reading_bytes(monkeypatch):
    # The upload site already holds the final bytes; vectorize_file must accept
    # the fingerprint via file_md5 and never read the file back to compute it.
    queue = DummyQueue()
    fs = DummyFS("deployment guide")
    monkeypatch.setattr(embedding_utils, "get_queue_manager", lambda: DummyQueueManager(queue))
    monkeypatch.setattr(embedding_utils, "get_viking_fs", lambda: fs)
    monkeypatch.setattr(
        embedding_utils,
        "get_openviking_config",
        lambda: types.SimpleNamespace(
            embedding=types.SimpleNamespace(text_source="summary_first", max_input_tokens=1000)
        ),
    )

    await embedding_utils.vectorize_file(
        file_path="viking://user/default/resources/demo.md",
        summary_dict={"name": "demo.md", "summary": "deployment summary"},
        parent_uri="viking://user/default/resources",
        ctx=DummyReq(),
        file_md5="a" * 32,
    )

    assert len(queue.items) == 1
    assert queue.items[0].context_data["md5"] == "a" * 32
    # No file read was issued just to fingerprint the content.
    assert fs.read_file_bytes_calls == 0


@pytest.mark.asyncio
async def test_vectorize_file_uses_supplied_content_without_reading_file(monkeypatch):
    queue = DummyQueue()
    fs = DummyFS("remote content")
    monkeypatch.setattr(embedding_utils, "get_queue_manager", lambda: DummyQueueManager(queue))
    monkeypatch.setattr(embedding_utils, "get_viking_fs", lambda: fs)
    monkeypatch.setattr(
        embedding_utils,
        "get_openviking_config",
        lambda: types.SimpleNamespace(
            embedding=types.SimpleNamespace(text_source="content_only", max_input_tokens=1000)
        ),
    )

    await embedding_utils.vectorize_file(
        file_path="viking://user/default/resources/a.py",
        summary_dict={"name": "a.py", "summary": ""},
        parent_uri="viking://user/default/resources",
        ctx=DummyReq(),
        file_content=b"print('local')",
    )

    assert queue.items[0].message == "print('local')"
    assert fs.read_file_calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text_source,use_summary", [("summary_first", False), ("content_only", True)]
)
@pytest.mark.parametrize("default_backend", ["vikingdb", "opengauss"])
@pytest.mark.parametrize("account_backend", ["vikingdb", "http", "volcengine", "opengauss"])
async def test_vectorize_file_preloads_resolved_account_content_without_policy_lookup(
    monkeypatch, text_source, use_summary, default_backend, account_backend
):
    from openviking.storage.viking_vector_index_backend import VIKINGDB_CONTENT_MAX_SIZE

    queue = DummyQueue()
    fs = DummyFS(
        "remote content must not be read", account_backends={"default": account_backend}
    )
    full_source = account_backend == "opengauss"
    tail_token = "TAIL_TOKEN_FULL_RESOURCE_20261016"
    content = "正文 retrieval\n" * (VIKINGDB_CONTENT_MAX_SIZE // 10 + 1) + tail_token
    summary = "short resource summary"
    assert content.index(tail_token) > VIKINGDB_CONTENT_MAX_SIZE
    monkeypatch.setattr(embedding_utils, "get_queue_manager", lambda: DummyQueueManager(queue))
    monkeypatch.setattr(embedding_utils, "get_viking_fs", lambda: fs)
    monkeypatch.setattr(
        embedding_utils,
        "get_openviking_config",
        lambda: types.SimpleNamespace(
            embedding=types.SimpleNamespace(text_source=text_source, max_input_tokens=1000),
            storage=types.SimpleNamespace(
                vectordb=types.SimpleNamespace(backend=default_backend)
            ),
        ),
    )

    enqueued = await embedding_utils.vectorize_file(
        file_path="viking://user/default/resources/long.md",
        summary_dict={"name": "long.md", "summary": summary},
        parent_uri="viking://user/default/resources",
        context_type="resource",
        ctx=DummyReq(),
        file_content=content.encode("utf-8"),
        materialize_content=True,
        use_summary=use_summary,
    )

    assert enqueued is True
    assert len(queue.items) == 1
    message = queue.items[0]
    preloaded = message.context_data["_materialized_content"]
    assert preloaded == (content if full_source else content[:VIKINGDB_CONTENT_MAX_SIZE])
    assert (tail_token in preloaded) is full_source
    assert fs.resolve_calls == ["default"]
    fs._get_vector_store.assert_not_called()
    fs.get_content_policy.assert_not_called()
    fs.account_uses_content_field.assert_not_called()
    fs.get_account_backend.assert_not_called()
    assert message.message == summary
    assert "content" not in message.context_data
    assert fs.read_calls == []
    assert fs.read_file_calls == 0
    assert fs.read_file_bytes_calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("default_backend", ["vikingdb", "opengauss"])
@pytest.mark.parametrize("enabled_at_consumption", [False, True])
async def test_preloaded_body_survives_migration_between_enqueue_and_consumption(
    monkeypatch, default_backend, enabled_at_consumption
):
    from openviking.models.embedder.base import EmbedResult
    from openviking.server.identity import RequestContext, Role
    from openviking.storage.collection_schemas import TextEmbeddingHandler
    from openviking.storage.queuefs.embedding_msg import EmbeddingMsg
    from openviking.storage.viking_vector_index_backend import VIKINGDB_CONTENT_MAX_SIZE
    from openviking_cli.session.user_id import UserIdentifier

    tail = "migrationtailkeywordunique"
    content = "x" * (VIKINGDB_CONTENT_MAX_SIZE + 11) + tail
    summary = "independent embedding summary"
    ctx = RequestContext(user=UserIdentifier("dedicated", "alice"), role=Role.USER)
    config = types.SimpleNamespace(
        storage=types.SimpleNamespace(
            vectordb=types.SimpleNamespace(name="context", backend=default_backend)
        ),
        embedding=types.SimpleNamespace(
            text_source="summary_first", max_input_tokens=1000, dimension=2,
            circuit_breaker=types.SimpleNamespace(
                failure_threshold=5, reset_timeout=60.0, max_reset_timeout=600.0,
            ),
        ),
    )
    policy = {"enabled": False, "source": "full_source", "max_size": None}
    backend = types.SimpleNamespace(
        _mode="opengauss",
        get_content_policy=AsyncMock(return_value=policy),
    )
    store = types.SimpleNamespace(
        is_closing=False,
        get_account_backend=AsyncMock(return_value=backend),
        get_content_policy=AsyncMock(side_effect=AssertionError("unexpected store policy lookup")),
        account_uses_content_field=AsyncMock(
            side_effect=AssertionError("unexpected legacy content readiness lookup")
        ),
        upsert=AsyncMock(return_value="record"),
    )
    fs = DummyFS(
        "must not replace the preloaded source", account_backends={"dedicated": "opengauss"}
    )
    queue = DummyQueue()
    monkeypatch.setattr(embedding_utils, "get_viking_fs", lambda: fs)
    monkeypatch.setattr("openviking.storage.viking_fs.get_viking_fs", lambda: fs)
    monkeypatch.setattr(embedding_utils, "get_queue_manager", lambda: DummyQueueManager(queue))
    monkeypatch.setattr(embedding_utils, "get_openviking_config", lambda: config)
    monkeypatch.setattr("openviking_cli.utils.config.get_openviking_config", lambda: config)
    assert await embedding_utils.vectorize_file(
        file_path="viking://user/alice/resources/long.md",
        summary_dict={"name": "long.md", "summary": summary},
        parent_uri="viking://user/alice/resources", ctx=ctx,
        file_content=content, materialize_content=True, use_summary=True, action="upsert",
    )
    payload = queue.items[0].to_json()
    queued = EmbeddingMsg.from_json(payload)
    assert queued.context_data["_materialized_content"] == content
    assert queued.context_data["_materialized_content"].endswith(tail)
    assert queued.message == summary
    assert fs.resolve_calls == ["dedicated"]
    fs._get_vector_store.assert_not_called()
    fs.get_content_policy.assert_not_called()
    fs.account_uses_content_field.assert_not_called()
    fs.get_account_backend.assert_not_called()
    store.get_account_backend.assert_not_called()
    backend.get_content_policy.assert_not_called()
    assert policy["enabled"] is False
    policy["enabled"] = enabled_at_consumption
    embedder = types.SimpleNamespace(
        prepare_embedding_input=lambda text: text,
        embed_async=AsyncMock(return_value=EmbedResult(dense_vector=[0.1, 0.2])),
    )
    handler = TextEmbeddingHandler(store, types.SimpleNamespace(bind=lambda account: embedder))
    outcome = await handler.on_dequeue({"data": payload})
    assert outcome.outcome.value == "success"
    written = store.upsert.call_args.args[0]
    if enabled_at_consumption:
        assert written["content"] == content
        assert written["content"].endswith(tail)
    else:
        assert "content" not in written
    assert "_materialized_content" not in written
    assert written["vector"] == [0.1, 0.2]
    embedder.embed_async.assert_awaited_once_with(summary, is_query=False)
    store.get_account_backend.assert_awaited_once_with("dedicated")
    backend.get_content_policy.assert_awaited_once_with()
    store.get_content_policy.assert_not_called()
    store.account_uses_content_field.assert_not_called()
    assert written["account_id"] == "dedicated"
    assert not fs.read_calls
    assert fs.read_file_calls == fs.read_file_bytes_calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "materialize,supplied,filename", [(False, True, "a.md"), (True, False, "a.md"), (True, True, "a.mp4")]
)
async def test_non_text_or_absent_preload_never_resolves_content_policy(
    monkeypatch, materialize, supplied, filename
):
    queue = DummyQueue()
    fs = DummyFS("remote source")
    fs._get_vector_store = Mock(side_effect=AssertionError("unexpected content policy IO"))
    monkeypatch.setattr(embedding_utils, "get_queue_manager", lambda: DummyQueueManager(queue))
    monkeypatch.setattr(embedding_utils, "get_viking_fs", lambda: fs)
    monkeypatch.setattr(
        embedding_utils,
        "get_openviking_config",
        lambda: types.SimpleNamespace(
            embedding=types.SimpleNamespace(text_source="summary_first", max_input_tokens=1000)
        ),
    )
    assert await embedding_utils.vectorize_file(
        "viking://resources/" + filename,
        {"name": filename, "summary": "summary"},
        "viking://resources",
        ctx=DummyReq(),
        file_content=b"source" if supplied else None,
        materialize_content=materialize,
    )
    fs._get_vector_store.assert_not_called()
    assert "_materialized_content" not in queue.items[0].context_data
    assert fs.read_file_calls == 0


@pytest.mark.asyncio
async def test_vectorize_file_omits_md5_when_not_supplied(monkeypatch):
    queue = DummyQueue()
    fs = DummyFS("deployment guide")
    monkeypatch.setattr(embedding_utils, "get_queue_manager", lambda: DummyQueueManager(queue))
    monkeypatch.setattr(embedding_utils, "get_viking_fs", lambda: fs)
    monkeypatch.setattr(
        embedding_utils,
        "get_openviking_config",
        lambda: types.SimpleNamespace(
            embedding=types.SimpleNamespace(text_source="summary_first", max_input_tokens=1000)
        ),
    )

    await embedding_utils.vectorize_file(
        file_path="viking://user/default/resources/demo.md",
        summary_dict={"name": "demo.md", "summary": "deployment summary"},
        parent_uri="viking://user/default/resources",
        ctx=DummyReq(),
    )

    assert len(queue.items) == 1
    # Unknown fingerprint must not be emitted, so a partial update never clears it.
    assert "md5" not in queue.items[0].context_data
    assert fs.read_file_bytes_calls == 0


@pytest.mark.asyncio
async def test_vectorize_image_downsamples_large_embedding_input(monkeypatch):
    queue = DummyQueue()
    original = _jpeg_bytes(80, 220)
    fs = DummyFS(original)
    monkeypatch.setattr(embedding_utils, "get_queue_manager", lambda: DummyQueueManager(queue))
    monkeypatch.setattr(embedding_utils, "get_viking_fs", lambda: fs)
    monkeypatch.setattr(
        embedding_utils,
        "get_openviking_config",
        lambda: types.SimpleNamespace(
            embedding=types.SimpleNamespace(text_source="content_only", max_input_tokens=1000),
            image=ImageConfig(
                preview_max_dimension=64,
                max_file_size_mb=100.0,
                large_image_threshold_dimension=100,
            ),
        ),
    )

    await embedding_utils.vectorize_file(
        file_path="viking://resources/docs/large.jpg",
        summary_dict={"name": "large.jpg", "summary": "large image"},
        parent_uri="viking://resources/docs",
        ctx=DummyReq(),
    )

    assert len(queue.items) == 1
    message = queue.items[0].message
    image_part = next(part for part in message if part["type"] == "image_url")
    width, height = _data_uri_image_size(image_part["image_url"]["url"])
    assert max(width, height) <= 64
    assert fs.content == original
    assert fs.read_file_bytes_calls == 1


@pytest.mark.asyncio
async def test_vectorize_image_uses_supplied_bytes_without_second_read(monkeypatch):
    queue = DummyQueue()
    original = _jpeg_bytes(20, 20)
    fs = DummyFS(b"must not be read")
    monkeypatch.setattr(embedding_utils, "get_queue_manager", lambda: DummyQueueManager(queue))
    monkeypatch.setattr(embedding_utils, "get_viking_fs", lambda: fs)
    monkeypatch.setattr(
        embedding_utils,
        "get_openviking_config",
        lambda: types.SimpleNamespace(
            embedding=types.SimpleNamespace(text_source="content_only", max_input_tokens=1000),
            image=ImageConfig(preview_max_dimension=64, max_file_size_mb=100.0),
        ),
    )

    await embedding_utils.vectorize_file(
        file_path="viking://resources/docs/image.jpg",
        summary_dict={"name": "image.jpg", "summary": "image"},
        parent_uri="viking://resources/docs",
        ctx=DummyReq(),
        file_content=original,
    )

    assert fs.read_file_bytes_calls == 0


@pytest.mark.asyncio
async def test_vectorize_file_propagates_enqueue_failure(monkeypatch):
    monkeypatch.setattr(
        embedding_utils, "get_queue_manager", lambda: DummyQueueManager(FailingQueue())
    )
    monkeypatch.setattr(embedding_utils, "get_viking_fs", lambda: DummyFS("content"))
    monkeypatch.setattr(
        embedding_utils,
        "get_openviking_config",
        lambda: types.SimpleNamespace(
            embedding=types.SimpleNamespace(text_source="summary_first", max_input_tokens=1000)
        ),
    )
    with pytest.raises(RuntimeError, match="queue unavailable"):
        await embedding_utils.vectorize_file(
            "viking://user/default/resources/test.md",
            {"name": "test.md", "summary": "summary"},
            "viking://user/default/resources",
            ctx=DummyReq(),
        )


@pytest.mark.asyncio
async def test_vectorize_directory_propagates_enqueue_failures(monkeypatch):
    monkeypatch.setattr(
        embedding_utils, "get_queue_manager", lambda: DummyQueueManager(FailingQueue())
    )

    with pytest.raises(RuntimeError, match="queue unavailable"):
        await embedding_utils.vectorize_directory_meta(
            "viking://user/default/resources",
            abstract="abstract",
            overview="overview",
            ctx=DummyReq(),
        )


@pytest.mark.asyncio
async def test_vectorize_unknown_text_file_embeds_summary_without_reading_content(monkeypatch):
    queue = DummyQueue()
    raw_makefile = "build:\n\tcargo build --locked\n"
    fs = DummyFS(raw_makefile)
    monkeypatch.setattr(embedding_utils, "get_queue_manager", lambda: DummyQueueManager(queue))
    monkeypatch.setattr(embedding_utils, "get_viking_fs", lambda: fs)
    monkeypatch.setattr(
        embedding_utils,
        "get_openviking_config",
        lambda: types.SimpleNamespace(
            embedding=types.SimpleNamespace(text_source="summary_first", max_input_tokens=1000)
        ),
    )

    await embedding_utils.vectorize_file(
        file_path="viking://user/default/resources/Makefile",
        summary_dict={"name": "Makefile", "summary": "VLM generated build file summary"},
        parent_uri="viking://user/default/resources",
        ctx=DummyReq(),
    )

    assert len(queue.items) == 1
    msg = queue.items[0]
    assert msg.message == "VLM generated build file summary"
    assert "content" not in msg.context_data
    assert fs.read_file_bytes_calls == 0


@pytest.mark.asyncio
async def test_vectorize_unknown_text_file_without_summary_uses_bounded_content(monkeypatch):
    queue = DummyQueue()
    fs = DummyFS("build:\n\tcargo build --release\n")
    monkeypatch.setattr(embedding_utils, "get_queue_manager", lambda: DummyQueueManager(queue))
    monkeypatch.setattr(embedding_utils, "get_viking_fs", lambda: fs)
    monkeypatch.setattr(
        embedding_utils,
        "get_openviking_config",
        lambda: types.SimpleNamespace(
            embedding=types.SimpleNamespace(text_source="content_only", max_input_tokens=1000)
        ),
    )

    enqueued = await embedding_utils.vectorize_file(
        file_path="viking://user/default/resources/Makefile",
        summary_dict={"name": "Makefile", "summary": ""},
        parent_uri="viking://user/default/resources",
        ctx=DummyReq(),
    )

    assert enqueued is True
    assert len(queue.items) == 1
    msg = queue.items[0]
    assert msg.message == "build:\n\tcargo build --release\n"
    assert "content" not in msg.context_data
    assert fs.read_file_calls == 1


@pytest.mark.asyncio
async def test_vectorize_empty_content_skips_enqueue(monkeypatch):
    queue = DummyQueue()
    monkeypatch.setattr(embedding_utils, "get_queue_manager", lambda: DummyQueueManager(queue))
    monkeypatch.setattr(embedding_utils, "get_viking_fs", lambda: DummyFS(""))
    monkeypatch.setattr(
        embedding_utils,
        "get_openviking_config",
        lambda: types.SimpleNamespace(
            embedding=types.SimpleNamespace(text_source="content_only", max_input_tokens=1000)
        ),
    )

    enqueued = await embedding_utils.vectorize_file(
        file_path="viking://user/default/resources/obsolete.md",
        summary_dict={"name": "obsolete.md", "summary": ""},
        parent_uri="viking://user/default/resources",
        ctx=DummyReq(),
    )

    assert enqueued is False
    assert queue.items == []


@pytest.mark.asyncio
async def test_vectorize_file_writes_search_tags_into_embedding_context(monkeypatch):
    queue = DummyQueue()
    monkeypatch.setattr(embedding_utils, "get_queue_manager", lambda: DummyQueueManager(queue))
    monkeypatch.setattr(embedding_utils, "get_viking_fs", lambda: DummyFS("deployment guide"))
    monkeypatch.setattr(
        embedding_utils,
        "get_openviking_config",
        lambda: types.SimpleNamespace(
            embedding=types.SimpleNamespace(text_source="content_only", max_input_tokens=1000)
        ),
    )

    await embedding_utils.vectorize_file(
        file_path="viking://user/default/resources/demo.md",
        summary_dict={"name": "demo.md", "summary": "deployment summary"},
        parent_uri="viking://user/default/resources",
        ctx=DummyReq(),
        ingest_options=IngestOptions.from_search_tags(["team=search", "env=test"]),
    )

    assert len(queue.items) == 1
    msg = queue.items[0]
    assert msg.context_data["search_tags"] == ["team=search", "env=test"]


@pytest.mark.asyncio
async def test_vectorize_file_appends_search_tags_to_existing_record_tags(monkeypatch):
    queue = DummyQueue()
    monkeypatch.setattr(embedding_utils, "get_queue_manager", lambda: DummyQueueManager(queue))
    monkeypatch.setattr(embedding_utils, "get_viking_fs", lambda: DummyFS("deployment guide"))
    monkeypatch.setattr(
        embedding_utils,
        "get_openviking_config",
        lambda: types.SimpleNamespace(
            embedding=types.SimpleNamespace(text_source="content_only", max_input_tokens=1000)
        ),
    )

    await embedding_utils.vectorize_file(
        file_path="viking://user/default/resources/demo.md",
        summary_dict={"name": "demo.md", "summary": "deployment summary"},
        parent_uri="viking://user/default/resources",
        ctx=DummyReq(),
        ingest_options=IngestOptions.from_search_tags(
            ["env=prod", "team=search"],
            mode="append",
        ),
    )

    assert len(queue.items) == 1
    msg = queue.items[0]
    assert msg.context_data["search_tags"] == ["env=prod", "team=search"]
    assert msg.context_data["_upsert_options"] == {"search_tag_mode": "append"}


@pytest.mark.asyncio
async def test_vectorize_file_append_does_not_read_existing_tags(monkeypatch):
    queue = DummyQueue()
    monkeypatch.setattr(embedding_utils, "get_queue_manager", lambda: DummyQueueManager(queue))
    monkeypatch.setattr(embedding_utils, "get_viking_fs", lambda: DummyFS("deployment guide"))
    monkeypatch.setattr(
        embedding_utils,
        "get_openviking_config",
        lambda: types.SimpleNamespace(
            embedding=types.SimpleNamespace(text_source="content_only", max_input_tokens=1000)
        ),
    )

    class DummyVikingDB:
        async def filter(self, **_kwargs):
            raise AssertionError("vectorize must not read existing tags")

    monkeypatch.setattr(
        "openviking.server.dependencies.get_service",
        lambda: types.SimpleNamespace(vikingdb_manager=DummyVikingDB()),
    )

    await embedding_utils.vectorize_file(
        file_path="viking://user/default/resources/demo.md",
        summary_dict={"name": "demo.md", "summary": "deployment summary"},
        parent_uri="viking://user/default/resources",
        ctx=DummyReq(),
        ingest_options=IngestOptions.from_search_tags(
            ["env=prod", "team=search"],
            mode="append",
        ),
    )

    assert queue.items[0].context_data["search_tags"] == ["env=prod", "team=search"]
    assert queue.items[0].context_data["_upsert_options"] == {"search_tag_mode": "append"}


@pytest.mark.asyncio
async def test_vectorize_directory_meta_writes_search_tags_into_embedding_context(monkeypatch):
    queue = DummyQueue()
    monkeypatch.setattr(embedding_utils, "get_queue_manager", lambda: DummyQueueManager(queue))
    monkeypatch.setattr(embedding_utils, "get_viking_fs", lambda: DummyFS("ignored"))

    await embedding_utils.vectorize_directory_meta(
        uri="viking://user/default/resources/demo",
        abstract="demo abstract",
        overview="demo overview",
        ctx=DummyReq(),
        ingest_options=IngestOptions.from_search_tags(["team=search", "env=test"]),
    )

    assert len(queue.items) == 2
    for msg in queue.items:
        assert msg.context_data["search_tags"] == ["team=search", "env=test"]


@pytest.mark.asyncio
async def test_vectorize_directory_meta_appends_search_tags_by_level(monkeypatch):
    queue = DummyQueue()
    monkeypatch.setattr(embedding_utils, "get_queue_manager", lambda: DummyQueueManager(queue))
    monkeypatch.setattr(embedding_utils, "get_viking_fs", lambda: DummyFS("ignored"))

    await embedding_utils.vectorize_directory_meta(
        uri="viking://user/default/resources/demo",
        abstract="demo abstract",
        overview="demo overview",
        ctx=DummyReq(),
        ingest_options=IngestOptions.from_search_tags(
            ["env=prod", "team=search"],
            mode="append",
        ),
    )

    assert len(queue.items) == 2
    assert queue.items[0].context_data["level"] == 0
    assert queue.items[0].context_data["search_tags"] == ["env=prod", "team=search"]
    assert queue.items[0].context_data["_upsert_options"] == {"search_tag_mode": "append"}
    assert queue.items[1].context_data["level"] == 1
    assert queue.items[1].context_data["search_tags"] == ["env=prod", "team=search"]
    assert queue.items[1].context_data["_upsert_options"] == {"search_tag_mode": "append"}


@pytest.mark.asyncio
async def test_vectorize_directory_meta_applies_merge_per_level(monkeypatch):
    queue = DummyQueue()
    monkeypatch.setattr(embedding_utils, "get_queue_manager", lambda: DummyQueueManager(queue))
    monkeypatch.setattr(embedding_utils, "get_viking_fs", lambda: DummyFS("ignored"))

    await embedding_utils.vectorize_directory_meta(
        uri="viking://user/default/resources/demo",
        abstract="demo abstract",
        overview="demo overview",
        ctx=DummyReq(),
        actions={0: "merge", 1: "upsert"},
    )

    assert len(queue.items) == 2
    assert "_upsert_options" not in queue.items[0].context_data
    assert queue.items[0].action.value == "merge"
    assert queue.items[1].action.value == "upsert"


@pytest.mark.asyncio
async def test_vectorize_directory_meta_writes_per_level_source_md5(monkeypatch):
    queue = DummyQueue()
    monkeypatch.setattr(embedding_utils, "get_queue_manager", lambda: DummyQueueManager(queue))
    monkeypatch.setattr(embedding_utils, "get_viking_fs", lambda: DummyFS("ignored"))

    await embedding_utils.vectorize_directory_meta(
        uri="viking://user/default/resources/demo",
        abstract="demo abstract",
        overview="demo overview",
        ctx=DummyReq(),
        md5s={0: "abstract-md5", 1: "overview-md5"},
    )

    assert [item.context_data["md5"] for item in queue.items] == [
        "abstract-md5",
        "overview-md5",
    ]


@pytest.mark.asyncio
async def test_vectorize_directory_meta_l1_abstract_is_overview(monkeypatch):
    """L1 records must carry the overview in the abstract scalar so Rerank
    sees L1 text instead of the L0 abstract."""
    from openviking.core.context import ContextLevel
    from openviking.storage.abstract_overview import render_abstract_overview

    queue = DummyQueue()
    monkeypatch.setattr(embedding_utils, "get_queue_manager", lambda: DummyQueueManager(queue))
    monkeypatch.setattr(embedding_utils, "get_viking_fs", lambda: DummyFS("ignored"))

    uri = "viking://user/default/resources/demo"
    overview = "# Overview\n\n" + ("section detail. " * 50)
    metadata = {
        "source": {"kind": "http", "uri": "https://example.com/private.pdf"},
        "generated_by": {"component": "SemanticProcessor", "trigger": "ingest"},
        "freshness": {
            "total_entries": 4,
            "sampled_entries": 2,
            "unsampled_entries": 2,
            "pending_child_changes": 0,
        },
    }
    await embedding_utils.vectorize_directory_meta(
        uri=uri,
        abstract=render_abstract_overview(
            ContextLevel.ABSTRACT, uri, "Visible abstract.", metadata
        ),
        overview=render_abstract_overview(ContextLevel.OVERVIEW, uri, overview, metadata),
        ctx=DummyReq(),
    )

    assert len(queue.items) == 2
    l0, l1 = queue.items
    assert l0.context_data["level"] == 0
    assert l0.context_data["abstract"] == "Visible abstract."
    assert l1.context_data["level"] == 1
    assert l1.context_data["abstract"] == overview.rstrip()
    assert l1.message == (f"---\ndirectory: {uri}/\n---\n\n{overview.rstrip()}")
    for item in (l0, l1):
        assert f"directory: {uri}/" in item.message
        assert "source:" not in item.message
        assert "generated_by:" not in item.message
        assert "freshness:" not in item.message


@pytest.mark.asyncio
async def test_vectorize_directory_meta_l1_abstract_truncated(monkeypatch):
    """Oversized overviews are capped below the scalar byte limit."""
    queue = DummyQueue()
    monkeypatch.setattr(embedding_utils, "get_queue_manager", lambda: DummyQueueManager(queue))
    monkeypatch.setattr(embedding_utils, "get_viking_fs", lambda: DummyFS("ignored"))

    oversized = "长" * 80_000
    await embedding_utils.vectorize_directory_meta(
        uri="viking://user/default/resources/demo",
        abstract="demo abstract",
        overview=oversized,
        ctx=DummyReq(),
    )

    l1 = queue.items[1]
    assert l1.context_data["level"] == 1
    assert len(l1.context_data["abstract"].encode("utf-8")) <= 50_000


@pytest.mark.asyncio
async def test_vectorize_unknown_text_content_only_reads_embedding_input(monkeypatch):
    queue = DummyQueue()
    fs = DummyFS("build:\n\tcargo build --locked\n".encode("gb18030"))
    monkeypatch.setattr(embedding_utils, "get_queue_manager", lambda: DummyQueueManager(queue))
    monkeypatch.setattr(embedding_utils, "get_viking_fs", lambda: fs)
    monkeypatch.setattr(
        embedding_utils,
        "get_openviking_config",
        lambda: types.SimpleNamespace(
            embedding=types.SimpleNamespace(text_source="content_only", max_input_tokens=1000)
        ),
    )

    await embedding_utils.vectorize_file(
        file_path="viking://user/default/resources/Makefile",
        summary_dict={"name": "Makefile", "summary": ""},
        parent_uri="viking://user/default/resources",
        ctx=DummyReq(),
    )

    assert len(queue.items) == 1
    msg = queue.items[0]
    assert msg.message == "build:\n\tcargo build --locked\n"
    assert "content" not in msg.context_data
    assert fs.read_file_bytes_calls == 0
    assert fs.read_file_calls == 1


@pytest.mark.asyncio
async def test_vectorize_unknown_file_ignores_summary_content_without_reread(monkeypatch):
    queue = DummyQueue()
    raw_content = "build:\n\tcargo build --locked\n"
    fs = DummyFS("should not be read")
    monkeypatch.setattr(embedding_utils, "get_queue_manager", lambda: DummyQueueManager(queue))
    monkeypatch.setattr(embedding_utils, "get_viking_fs", lambda: fs)
    monkeypatch.setattr(
        embedding_utils,
        "get_openviking_config",
        lambda: types.SimpleNamespace(
            embedding=types.SimpleNamespace(text_source="summary_first", max_input_tokens=1000)
        ),
    )

    await embedding_utils.vectorize_file(
        file_path="viking://user/default/resources/Makefile",
        summary_dict={
            "name": "Makefile",
            "summary": "VLM generated build file summary",
            "content": raw_content,
        },
        parent_uri="viking://user/default/resources",
        ctx=DummyReq(),
    )

    assert len(queue.items) == 1
    msg = queue.items[0]
    assert msg.message == "VLM generated build file summary"
    assert "content" not in msg.context_data
    assert fs.read_file_bytes_calls == 0
    assert fs.read_file_calls == 0


@pytest.mark.asyncio
async def test_vectorize_unknown_binary_file_falls_back_to_summary(monkeypatch):
    queue = DummyQueue()
    summary = "VLM generated binary file summary"
    binary_content = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
    fs = DummyFS(binary_content)
    monkeypatch.setattr(embedding_utils, "get_queue_manager", lambda: DummyQueueManager(queue))
    monkeypatch.setattr(embedding_utils, "get_viking_fs", lambda: fs)
    monkeypatch.setattr(
        embedding_utils,
        "get_openviking_config",
        lambda: types.SimpleNamespace(
            embedding=types.SimpleNamespace(text_source="summary_first", max_input_tokens=1000)
        ),
    )

    await embedding_utils.vectorize_file(
        file_path="viking://user/default/resources/model.weights",
        summary_dict={"name": "model.weights", "summary": summary},
        parent_uri="viking://user/default/resources",
        ctx=DummyReq(),
    )

    assert len(queue.items) == 1
    msg = queue.items[0]
    assert msg.message == summary
    assert "content" not in msg.context_data
    assert fs.read_file_bytes_calls == 0
    assert fs.read_file_calls == 0


@pytest.mark.asyncio
async def test_vectorize_text_summary_first_defers_full_content_read(monkeypatch):
    queue = DummyQueue()
    fs = DummyFS("# README\nraw text for bm25\n")
    monkeypatch.setattr(embedding_utils, "get_queue_manager", lambda: DummyQueueManager(queue))
    monkeypatch.setattr(embedding_utils, "get_viking_fs", lambda: fs)
    monkeypatch.setattr(
        embedding_utils,
        "get_openviking_config",
        lambda: types.SimpleNamespace(
            embedding=types.SimpleNamespace(text_source="summary_first", max_input_tokens=1000)
        ),
    )

    await embedding_utils.vectorize_file(
        file_path="viking://user/default/resources/README.md",
        summary_dict={"name": "README.md", "summary": "summary for embedding"},
        parent_uri="viking://user/default/resources",
        ctx=DummyReq(),
    )

    assert len(queue.items) == 1
    msg = queue.items[0]
    assert msg.message == "summary for embedding"
    assert "content" not in msg.context_data
    assert fs.read_file_calls == 0
    assert fs.read_file_bytes_calls == 0


@pytest.mark.asyncio
async def test_vectorize_image_file_enqueues_summary_and_image(monkeypatch):
    queue = DummyQueue()
    fs = DummyFS(b"\x89PNG\r\n\x1a\nimage")
    monkeypatch.setattr(embedding_utils, "get_queue_manager", lambda: DummyQueueManager(queue))
    monkeypatch.setattr(embedding_utils, "get_viking_fs", lambda: fs)
    monkeypatch.setattr(
        embedding_utils,
        "get_openviking_config",
        lambda: types.SimpleNamespace(
            embedding=types.SimpleNamespace(text_source="summary_first", max_input_tokens=1000)
        ),
    )

    await embedding_utils.vectorize_file(
        file_path="viking://user/default/resources/photo.png",
        summary_dict={"name": "photo.png", "summary": "a cat on a sofa"},
        parent_uri="viking://user/default/resources",
        ctx=DummyReq(),
    )

    assert len(queue.items) == 1
    msg = queue.items[0]
    assert msg.message[0] == {"type": "text", "text": "a cat on a sofa"}
    assert msg.message[1]["type"] == "image_url"
    assert msg.message[1]["image_url"]["url"].startswith("data:image/png;base64,")
    assert "content" not in msg.context_data
    assert fs.read_file_bytes_calls == 1


@pytest.mark.asyncio
async def test_vectorize_svg_file_uses_summary_and_indexes_markup(monkeypatch):
    queue = DummyQueue()
    svg_content = '<svg xmlns="http://www.w3.org/2000/svg"><text>queue flow</text></svg>'
    fs = DummyFS(svg_content)
    monkeypatch.setattr(embedding_utils, "get_queue_manager", lambda: DummyQueueManager(queue))
    monkeypatch.setattr(embedding_utils, "get_viking_fs", lambda: fs)
    monkeypatch.setattr(
        embedding_utils,
        "get_openviking_config",
        lambda: types.SimpleNamespace(
            embedding=types.SimpleNamespace(text_source="summary_first", max_input_tokens=1000)
        ),
    )

    await embedding_utils.vectorize_file(
        file_path="viking://user/default/resources/diagram.svg",
        summary_dict={"name": "diagram.svg", "summary": "queue processing diagram"},
        parent_uri="viking://user/default/resources",
        ctx=DummyReq(),
    )

    assert len(queue.items) == 1
    msg = queue.items[0]
    assert msg.message == "queue processing diagram"
    assert "content" not in msg.context_data
    assert fs.read_file_calls == 0
    assert fs.read_file_bytes_calls == 0


@pytest.mark.asyncio
async def test_vectorize_image_file_falls_back_to_summary_when_image_unreadable(monkeypatch):
    class UnreadableImageFS(DummyFS):
        async def read_file_bytes(self, _path, ctx=None):
            self.read_file_bytes_calls += 1
            raise OSError("cannot read")

    queue = DummyQueue()
    fs = UnreadableImageFS("")
    monkeypatch.setattr(embedding_utils, "get_queue_manager", lambda: DummyQueueManager(queue))
    monkeypatch.setattr(embedding_utils, "get_viking_fs", lambda: fs)
    monkeypatch.setattr(
        embedding_utils,
        "get_openviking_config",
        lambda: types.SimpleNamespace(
            embedding=types.SimpleNamespace(text_source="summary_first", max_input_tokens=1000)
        ),
    )

    await embedding_utils.vectorize_file(
        file_path="viking://user/default/resources/photo.png",
        summary_dict={"name": "photo.png", "summary": "fallback summary"},
        parent_uri="viking://user/default/resources",
        ctx=DummyReq(),
    )

    assert len(queue.items) == 1
    assert queue.items[0].message == "fallback summary"
    assert fs.read_file_bytes_calls == 1


@pytest.mark.asyncio
async def test_vectorize_text_file_ignores_summary_content_without_reread(monkeypatch):
    queue = DummyQueue()
    raw_content = "# README\nraw text already read during summary\n"
    fs = DummyFS("should not be read")
    monkeypatch.setattr(embedding_utils, "get_queue_manager", lambda: DummyQueueManager(queue))
    monkeypatch.setattr(embedding_utils, "get_viking_fs", lambda: fs)
    monkeypatch.setattr(
        embedding_utils,
        "get_openviking_config",
        lambda: types.SimpleNamespace(
            embedding=types.SimpleNamespace(text_source="summary_first", max_input_tokens=1000)
        ),
    )

    await embedding_utils.vectorize_file(
        file_path="viking://user/default/resources/README.md",
        summary_dict={
            "name": "README.md",
            "summary": "summary for embedding",
            "content": raw_content,
        },
        parent_uri="viking://user/default/resources",
        ctx=DummyReq(),
    )

    assert len(queue.items) == 1
    msg = queue.items[0]
    assert msg.message == "summary for embedding"
    assert "content" not in msg.context_data
    assert fs.read_file_calls == 0
    assert fs.read_file_bytes_calls == 0


@pytest.mark.asyncio
async def test_vectorize_content_only_reads_embedding_input(monkeypatch):
    queue = DummyQueue()
    fs = DummyFS("README content".encode("gb18030"))
    monkeypatch.setattr(embedding_utils, "get_queue_manager", lambda: DummyQueueManager(queue))
    monkeypatch.setattr(embedding_utils, "get_viking_fs", lambda: fs)
    monkeypatch.setattr(
        embedding_utils,
        "get_openviking_config",
        lambda: types.SimpleNamespace(
            embedding=types.SimpleNamespace(text_source="content_only", max_input_tokens=1000)
        ),
    )

    await embedding_utils.vectorize_file(
        file_path="viking://user/default/resources/README.md",
        summary_dict={"name": "README.md", "summary": "summary for embedding"},
        parent_uri="viking://user/default/resources",
        ctx=DummyReq(),
    )

    assert len(queue.items) == 1
    msg = queue.items[0]
    assert msg.message == "README content"
    assert "content" not in msg.context_data
    assert fs.read_file_calls == 1
    assert fs.read_file_bytes_calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "content, expected",
    [
        ("a1👍" * 100, "a1👍" * 26 + "a1" + EMBEDDING_TRUNCATION_SUFFIX),
        ("中文あ가Ｌ、" * 100, "中文あ가Ｌ、" * 4 + "中文" + EMBEDDING_TRUNCATION_SUFFIX),
        ("a1中👍" * 5, "a1中👍" * 5),
    ],
    ids=["ordinary-characters", "wide-text", "within-budget"],
)
async def test_vectorize_file_bounds_content_before_enqueue(monkeypatch, content, expected):
    queue = DummyQueue()
    fs = DummyFS(content)
    monkeypatch.setattr(embedding_utils, "get_queue_manager", lambda: DummyQueueManager(queue))
    monkeypatch.setattr(embedding_utils, "get_viking_fs", lambda: fs)
    monkeypatch.setattr(
        embedding_utils,
        "get_openviking_config",
        lambda: types.SimpleNamespace(
            embedding=types.SimpleNamespace(text_source="content_only", max_input_tokens=20)
        ),
    )
    await embedding_utils.vectorize_file(
        file_path="viking://user/default/resources/test.md",
        summary_dict={"name": "test.md", "summary": "short summary"},
        parent_uri="viking://user/default/resources",
        ctx=DummyReq(),
    )

    assert len(queue.items) == 1
    message = queue.items[0].message
    assert message == expected
    assert fs.read_file_calls == 1


@pytest.mark.asyncio
async def test_index_resource_skips_session_namespace(monkeypatch):
    queue = DummyQueue()
    monkeypatch.setattr(embedding_utils, "get_queue_manager", lambda: DummyQueueManager(queue))
    monkeypatch.setattr(embedding_utils, "get_viking_fs", lambda: DummyFS("ignored"))
    monkeypatch.setattr(
        embedding_utils,
        "get_openviking_config",
        lambda: types.SimpleNamespace(
            embedding=types.SimpleNamespace(text_source="summary_first", max_input_tokens=1000)
        ),
    )
    await embedding_utils.index_resource(
        uri="viking://session/default/sess_001/history/archive_001",
        ctx=DummyReq(),
    )

    assert queue.items == []


def test_truncate_abstract_bytes_caps_below_byte_limit():
    # small values pass through unchanged
    assert embedding_utils._truncate_abstract_bytes("small") == "small"
    assert embedding_utils._truncate_abstract_bytes("") == ""
    # oversized value is capped AND stays valid UTF-8 (no split multibyte char)
    big = "你" * 30_000  # 90,000 UTF-8 bytes, over the 65535 bytes_row cap
    capped = embedding_utils._truncate_abstract_bytes(big)
    encoded = capped.encode("utf-8")
    assert len(encoded) <= embedding_utils._ABSTRACT_MAX_BYTES
    assert encoded.decode("utf-8") == capped


@pytest.mark.asyncio
async def test_vectorize_file_truncates_oversized_abstract(monkeypatch):
    """An oversized file summary must be capped before it becomes the `abstract`
    scalar, otherwise the vector-store bytes_row write fails (string field >
    65535 bytes) and the resource is silently never vectorized."""
    queue = DummyQueue()
    monkeypatch.setattr(embedding_utils, "get_queue_manager", lambda: DummyQueueManager(queue))
    monkeypatch.setattr(embedding_utils, "get_viking_fs", lambda: DummyFS("ignored"))
    monkeypatch.setattr(
        embedding_utils,
        "get_openviking_config",
        lambda: types.SimpleNamespace(
            embedding=types.SimpleNamespace(text_source="summary_first", max_input_tokens=1000)
        ),
    )
    oversized = "你" * 30_000  # 90,000 UTF-8 bytes
    await embedding_utils.vectorize_file(
        file_path="viking://user/default/resources/big.md",
        summary_dict={"name": "big.md", "summary": oversized},
        parent_uri="viking://user/default/resources",
        ctx=DummyReq(),
    )

    assert len(queue.items) == 1
    abstract = queue.items[0].context_data["abstract"]
    assert len(abstract.encode("utf-8")) <= embedding_utils._ABSTRACT_MAX_BYTES
    assert abstract.encode("utf-8").decode("utf-8") == abstract  # valid UTF-8


@pytest.mark.asyncio
async def test_empty_media_uses_filename_but_unknown_binary_skips(monkeypatch):
    monkeypatch.setattr(
        embedding_utils,
        "get_openviking_config",
        lambda: types.SimpleNamespace(
            embedding=types.SimpleNamespace(
                text_source="content_only",
                max_input_tokens=1000,
            )
        ),
    )

    queue = DummyQueue()
    monkeypatch.setattr(
        embedding_utils,
        "get_queue_manager",
        lambda: DummyQueueManager(queue),
    )
    monkeypatch.setattr(
        embedding_utils,
        "get_viking_fs",
        lambda: DummyFS(b"media"),
    )
    await embedding_utils.vectorize_file(
        file_path="viking://resources/media/meeting.mp3",
        summary_dict={"name": "meeting.mp3", "summary": ""},
        parent_uri="viking://resources/media",
        ctx=DummyReq(),
    )

    assert queue.items[0].message == "meeting.mp3"
    assert "content" not in queue.items[0].context_data

    queue = DummyQueue()
    monkeypatch.setattr(
        embedding_utils,
        "get_queue_manager",
        lambda: DummyQueueManager(queue),
    )
    monkeypatch.setattr(
        embedding_utils,
        "get_viking_fs",
        lambda: DummyFS(b"binary"),
    )
    await embedding_utils.vectorize_file(
        file_path="viking://resources/media/archive.bin",
        summary_dict={"name": "archive.bin", "summary": ""},
        parent_uri="viking://resources/media",
        ctx=DummyReq(),
    )

    assert queue.items == []


@pytest.mark.asyncio
async def test_vectorize_directory_meta_truncates_oversized_abstract(monkeypatch):
    """The directory-meta path (fed by index_resource reading .abstract.md) must
    cap the abstract scalar on every enqueued Context (abstract + overview)."""
    queue = DummyQueue()
    monkeypatch.setattr(embedding_utils, "get_queue_manager", lambda: DummyQueueManager(queue))
    monkeypatch.setattr(embedding_utils, "get_viking_fs", lambda: DummyFS("ignored"))
    oversized = "你" * 30_000  # 90,000 UTF-8 bytes
    await embedding_utils.vectorize_directory_meta(
        uri="viking://user/default/resources/dir",
        abstract=oversized,
        overview="overview text",
        ctx=DummyReq(),
    )

    assert queue.items  # at least the abstract-level Context was enqueued
    for item in queue.items:
        abstract = item.context_data["abstract"]
        assert len(abstract.encode("utf-8")) <= embedding_utils._ABSTRACT_MAX_BYTES
        assert abstract.encode("utf-8").decode("utf-8") == abstract


@pytest.mark.asyncio
async def test_skill_directory_body_frontmatter_is_not_parsed_twice(monkeypatch):
    from openviking.storage.abstract_overview import (
        AbstractOverviewFormatError,
        render_abstract_overview,
    )

    queue = DummyQueue()
    monkeypatch.setattr(embedding_utils, "get_queue_manager", lambda: DummyQueueManager(queue))
    monkeypatch.setattr(embedding_utils, "get_viking_fs", lambda: DummyFS("ignored"))
    uri = "viking://agent/skills/demo"
    body = "---\nname: demo\ndescription: Tool instructions\n---\n\n# Parameters\nKeep this body."
    await embedding_utils.vectorize_directory_meta(
        uri, "name: demo", body, context_type="skill", ctx=DummyReq(), content_is_body=True
    )
    overview_msg = next(item for item in queue.items if item.context_data["level"] == 1)
    assert overview_msg.context_data["abstract"] == body
    assert body in overview_msg.message

    # Existing callers still validate serialized sidecars, and strip only the
    # outer system metadata from a valid document containing frontmatter.
    with pytest.raises(AbstractOverviewFormatError, match="must contain directory"):
        await embedding_utils.vectorize_directory_meta(uri, "abstract", body, ctx=DummyReq())
    queue.items.clear()
    await embedding_utils.vectorize_directory_meta(
        uri, "abstract", render_abstract_overview(1, uri, body), ctx=DummyReq()
    )
    overview_msg = next(item for item in queue.items if item.context_data["level"] == 1)
    assert overview_msg.context_data["abstract"] == body
    assert body in overview_msg.message


@pytest.mark.asyncio
async def test_full_upsert_uses_resolved_search_tags_from_plan(monkeypatch):
    queue = DummyQueue()
    monkeypatch.setattr(embedding_utils, "get_queue_manager", lambda: DummyQueueManager(queue))
    monkeypatch.setattr(embedding_utils, "get_viking_fs", lambda: DummyFS("body"))
    monkeypatch.setattr(
        embedding_utils,
        "get_openviking_config",
        lambda: types.SimpleNamespace(
            embedding=types.SimpleNamespace(text_source="summary_only", max_input_tokens=1000)
        ),
    )

    await embedding_utils.vectorize_file(
        file_path="viking://resources/repo/a.py",
        summary_dict={"name": "a.py", "summary": "summary"},
        parent_uri="viking://resources/repo",
        ctx=DummyReq(),
        scalar_override={"search_tags": ["team=old", "lang=python", "team=new", "owner=alice"]},
        ingest_options=IngestOptions.from_search_tags(["team=new", "owner=alice"], mode="append"),
        action="upsert",
    )

    msg = queue.items[0]
    assert msg.context_data["search_tags"] == [
        "team=old",
        "lang=python",
        "team=new",
        "owner=alice",
    ]
    assert msg.action.value == "upsert"
    assert msg.context_data["_upsert_options"] == {"search_tag_mode": "append"}


@pytest.mark.asyncio
async def test_context_vectorization_defaults_to_merge_for_legacy_producers(monkeypatch):
    queue = DummyQueue()
    monkeypatch.setattr(embedding_utils, "get_queue_manager", lambda: DummyQueueManager(queue))
    monkeypatch.setattr(embedding_utils, "get_viking_fs", lambda: DummyFS("body"))
    monkeypatch.setattr(
        embedding_utils,
        "get_openviking_config",
        lambda: types.SimpleNamespace(
            embedding=types.SimpleNamespace(text_source="summary_only", max_input_tokens=1000)
        ),
    )

    await embedding_utils.vectorize_file(
        file_path="viking://resources/repo/a.py",
        summary_dict={"name": "a.py", "summary": "summary"},
        parent_uri="viking://resources/repo",
        ctx=DummyReq(),
    )
    await embedding_utils.vectorize_directory_meta(
        uri="viking://resources/repo",
        abstract="abstract",
        overview="overview",
        ctx=DummyReq(),
    )

    assert [message.action.value for message in queue.items] == ["merge", "merge", "merge"]
    assert all(message.field_patch is None for message in queue.items)


@pytest.mark.asyncio
async def test_planned_merge_does_not_copy_context_into_patch_seed(monkeypatch):
    queue = DummyQueue()
    monkeypatch.setattr(embedding_utils, "get_queue_manager", lambda: DummyQueueManager(queue))
    monkeypatch.setattr(embedding_utils, "get_viking_fs", lambda: DummyFS("body"))
    monkeypatch.setattr(
        embedding_utils,
        "get_openviking_config",
        lambda: types.SimpleNamespace(
            embedding=types.SimpleNamespace(text_source="summary_only", max_input_tokens=1000)
        ),
    )

    await embedding_utils.vectorize_file(
        file_path="viking://resources/repo/a.py",
        summary_dict={"name": "a.py", "summary": "large summary"},
        parent_uri="viking://resources/repo",
        ctx=DummyReq(),
        action="merge",
        field_patch=FieldPatch(
            {"search_tags": ["scope=new"]},
            {"search_tags": "append"},
        ),
    )

    msg = queue.items[0]
    assert msg.context_data["abstract"] == "large summary"
    assert msg.field_patch == FieldPatch(
        {"search_tags": ["scope=new"]},
        {"search_tags": "append"},
    )


def test_semantic_processor_uses_merge_for_unplanned_vectorization():
    import inspect

    from openviking.storage.queuefs.semantic_processor import SemanticProcessor

    assert (
        inspect.signature(SemanticProcessor._vectorize_single_file).parameters["action"].default
        == "merge"
    )


@pytest.mark.asyncio
async def test_full_upsert_carries_existing_record_id_as_internal_override(monkeypatch):
    queue = DummyQueue()
    monkeypatch.setattr(embedding_utils, "get_queue_manager", lambda: DummyQueueManager(queue))
    monkeypatch.setattr(embedding_utils, "get_viking_fs", lambda: DummyFS("body"))
    monkeypatch.setattr(
        embedding_utils,
        "get_openviking_config",
        lambda: types.SimpleNamespace(
            embedding=types.SimpleNamespace(text_source="summary_only", max_input_tokens=1000)
        ),
    )

    await embedding_utils.vectorize_file(
        file_path="viking://resources/repo/a.py",
        summary_dict={"name": "a.py", "summary": "summary"},
        parent_uri="viking://resources/repo",
        ctx=DummyReq(),
        scalar_override={"_record_id": "id-from-vector-db"},
        action="upsert",
    )

    msg = queue.items[0]
    assert msg.context_data["_upsert_record_id"] == "id-from-vector-db"
    assert "_record_id" not in msg.context_data
