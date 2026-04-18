"""
端到端功能测试: 服务健康检查 → 记忆注入 → compact → 记忆召回 QA → openGauss 存储验证。

测试部署环境中 OpenClaw + OpenViking + openGauss 三个服务协同工作是否正常。
对应 testcase 分支 _pipeline.py 的完整流程，但直接操作已部署的 Docker 容器，
无需本地安装 openclaw 或 openviking。

运行:
  cd examples/openclaw-plugin/docker/tests
  pytest test_e2e_functional.py -v --tb=short

环境变量:
  OG_PASSWORD      — openGauss 密码（用于数据库验证，可选）
  JUDGE_API_KEY    — LLM Judge 的 API Key（可选，不设则跳过 Judge）
"""

import json
import logging
import os
import time
import uuid

import pytest
import requests

from conftest import CFG, ENDPOINTS

logger = logging.getLogger("e2e.functional")

SESSION_ID = f"e2e-docker-{uuid.uuid4().hex[:8]}"
AGENT_ID = "main"
USER_ID = "e2e-user"


# ── Helpers ───────────────────────────────────────────────────────


def ov_search(query: str, limit: int = 10) -> dict:
    """Call OpenViking /api/v1/search/find."""
    resp = requests.post(
        f"{ENDPOINTS.ov_base_url}/api/v1/search/find",
        json={"query": query, "limit": limit},
        headers=ENDPOINTS.ov_headers(),
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


def oc_send_message(message: str, session_key: str, timeout: int = 120) -> dict:
    """Send message via OpenClaw /v1/responses API."""
    headers = {
        "Content-Type": "application/json",
        "X-OpenClaw-Agent-ID": AGENT_ID,
        "X-OpenClaw-Session-Key": session_key,
    }
    if ENDPOINTS.oc_token:
        headers["Authorization"] = f"Bearer {ENDPOINTS.oc_token}"

    payload = {
        "model": "openclaw",
        "input": message,
        "stream": False,
        "user": USER_ID,
    }
    resp = requests.post(
        f"{ENDPOINTS.oc_base_url}/v1/responses",
        json=payload,
        headers=headers,
        timeout=timeout,
    )
    resp.raise_for_status()
    body = resp.json()

    text = ""
    for item in body.get("output", []):
        if item.get("type") == "message":
            for content in item.get("content", []):
                if content.get("type") == "output_text":
                    text = content.get("text", "")
                    break
        if text:
            break
    if not text:
        for item in body.get("output", []):
            if "text" in item:
                text = item["text"]
                break

    return {"success": True, "text": text, "raw": body}


def oc_send_with_retry(message: str, session_key: str,
                       max_retries: int = 3, delay: int = 15,
                       timeout: int = 120) -> dict:
    """Send with retry on rate-limit or timeout."""
    last_result = None
    for attempt in range(1, max_retries + 1):
        try:
            result = oc_send_message(message, session_key, timeout=timeout)
            text = result.get("text", "")
            if "rate limit" in text.lower() or "try again" in text.lower():
                logger.warning("rate limited (attempt %d/%d)", attempt, max_retries)
                time.sleep(delay)
                continue
            return result
        except requests.Timeout:
            logger.warning("timeout (attempt %d/%d)", attempt, max_retries)
            if attempt < max_retries:
                time.sleep(delay)
            last_result = {"success": False, "error": "timeout", "text": ""}
        except Exception as exc:
            last_result = {"success": False, "error": str(exc), "text": ""}
            break
    return last_result or {"success": False, "error": "max retries exceeded", "text": ""}


def oc_compact(session_key: str, timeout: int = 60) -> dict:
    """Trigger sessions.compact via Gateway WebSocket RPC."""
    try:
        import websocket
    except ImportError:
        return {"success": False, "error": "websocket-client not installed"}

    ws_url = ENDPOINTS.oc_base_url.replace("http://", "ws://")

    try:
        ws = websocket.create_connection(ws_url, timeout=timeout)
    except Exception as exc:
        return {"success": False, "error": f"ws connect: {exc}"}

    try:
        challenge = json.loads(ws.recv())
        if challenge.get("event") != "connect.challenge":
            return {"success": False, "error": f"unexpected: {challenge}"}

        connect_id = str(uuid.uuid4())
        ws.send(json.dumps({
            "type": "req", "id": connect_id, "method": "connect",
            "params": {
                "minProtocol": 3, "maxProtocol": 3,
                "client": {
                    "id": "openclaw-control-ui",
                    "version": "1.0.0",
                    "platform": "linux",
                    "mode": "webchat",
                },
                "scopes": ["operator.admin", "operator.read", "operator.write"],
                "auth": {"token": ENDPOINTS.oc_token},
            },
        }))

        while True:
            msg = json.loads(ws.recv())
            if msg.get("type") == "res" and msg.get("id") == connect_id:
                if not msg.get("ok"):
                    return {"success": False, "error": f"handshake rejected: {msg}"}
                break

        compact_id = str(uuid.uuid4())
        ws.send(json.dumps({
            "type": "req", "id": compact_id,
            "method": "sessions.compact",
            "params": {"key": session_key},
        }))

        while True:
            msg = json.loads(ws.recv())
            if msg.get("type") == "res" and msg.get("id") == compact_id:
                if msg.get("ok"):
                    return {"success": True, "payload": msg.get("payload", {})}
                return {"success": False, "error": msg.get("error", {})}

    except Exception as exc:
        return {"success": False, "error": str(exc)}
    finally:
        try:
            ws.close()
        except Exception:
            pass


def judge_answer(question: str, gold: str, response: str) -> dict:
    """LLM judge: compare response against gold answer."""
    judge_cfg = CFG.get("judge", {})
    api_key = os.environ.get("JUDGE_API_KEY", "")
    if not api_key and judge_cfg.get("api_key_env"):
        api_key = os.environ.get(judge_cfg["api_key_env"], "")
    if not api_key:
        return {"is_correct": None, "error": "no judge API key"}

    base_url = os.environ.get(
        "JUDGE_BASE_URL",
        CFG.get("models", {}).get("provider", {}).get("baseUrl", ""),
    )
    model = CFG.get("models", {}).get("judge", "")

    prompt = (
        f"Your task is to label an answer as 'CORRECT' or 'WRONG'.\n\n"
        f"Question: {question}\nGold answer: {gold}\nGenerated answer: {response}\n\n"
        f"Be generous: if the answer touches on the same topic, count as CORRECT.\n"
        f'Respond with JSON only: {{"is_correct": "CORRECT" or "WRONG", "reasoning": "..."}}'
    )

    try:
        resp = requests.post(
            f"{base_url}/chat/completions",
            json={
                "model": model.split("/")[-1] if "/" in model else model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0,
            },
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
            timeout=60,
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"].strip()
        start, end = content.find("{"), content.rfind("}")
        if start != -1 and end != -1:
            parsed = json.loads(content[start:end + 1])
            return {
                "is_correct": parsed.get("is_correct", "WRONG").upper() == "CORRECT",
                "reasoning": parsed.get("reasoning", ""),
            }
        return {"is_correct": None, "error": f"parse failed: {content[:200]}"}
    except Exception as exc:
        return {"is_correct": None, "error": str(exc)}


# ── Tests ─────────────────────────────────────────────────────────


class TestServiceHealth:
    """Phase 0: 服务健康检查 — 确认三个服务均已启动且可达。"""

    def test_openviking_health(self, endpoints):
        """OpenViking HTTP 端口可达。"""
        resp = requests.get(
            f"{endpoints.ov_base_url}/api/v1/health",
            headers=endpoints.ov_headers(),
            timeout=10,
        )
        assert resp.status_code in (200, 404), (
            f"OpenViking not reachable: {resp.status_code}"
        )
        logger.info("OpenViking healthy at %s", endpoints.ov_base_url)

    def test_openclaw_health(self, endpoints):
        """OpenClaw Gateway HTTP 端口可达。"""
        resp = requests.get(f"{endpoints.oc_base_url}/health", timeout=10)
        assert resp.status_code == 200, (
            f"OpenClaw not reachable: {resp.status_code}"
        )
        logger.info("OpenClaw healthy at %s", endpoints.oc_base_url)

    def test_opengauss_connection(self, endpoints):
        """openGauss 数据库可连接（需要 OG_PASSWORD 环境变量）。"""
        password = endpoints.og_password
        if not password:
            pytest.skip("OG_PASSWORD not set, skip DB connection test")

        import psycopg2
        conn = psycopg2.connect(
            host=endpoints.og_host,
            port=endpoints.og_port,
            user=endpoints.og_user,
            password=password,
            dbname=endpoints.og_db,
            connect_timeout=10,
        )
        cur = conn.cursor()
        cur.execute("SELECT 1")
        assert cur.fetchone()[0] == 1
        cur.close()
        conn.close()
        logger.info("openGauss connected at %s:%d/%s",
                     endpoints.og_host, endpoints.og_port, endpoints.og_db)


class TestMemoryIngestAndRecall:
    """Phase 1–2: 通过 OpenClaw 注入记忆 → compact → QA 召回验证。"""

    _ingest_responses: list = []
    _qa_results: list = []

    def test_01_ingest_messages(self, test_data, timeouts):
        """Phase 1: 发送对话消息注入记忆。"""
        messages = test_data["session_messages"]
        ingest_key = f"agent:{AGENT_ID}:openresponses-user:{USER_ID}"

        for i, msg in enumerate(messages, 1):
            logger.info("[ingest %d/%d] %s", i, len(messages), msg[:80])
            result = oc_send_with_retry(
                msg, ingest_key,
                max_retries=timeouts["max_retries"],
                delay=timeouts["retry_delay"],
                timeout=timeouts["message"],
            )
            assert result["success"], f"ingest {i} failed: {result.get('error')}"
            logger.info("[ingest %d] reply: %s", i, result["text"][:200])
            self.__class__._ingest_responses.append(result)

            if i < len(messages):
                time.sleep(timeouts.get("message_gap", 10))

        assert len(self.__class__._ingest_responses) == len(messages)

    def test_02_trigger_compact(self, timeouts):
        """Phase 1.5: 触发 sessions.compact 强制生成记忆。"""
        session_key = f"agent:{AGENT_ID}:openresponses-user:{USER_ID}"
        result = oc_compact(session_key, timeout=timeouts.get("compact", 300))
        logger.info("compact result: %s", result)
        assert result["success"], f"compact failed: {result.get('error')}"

    def test_03_wait_memory_indexed(self, test_data, timeouts, endpoints):
        """Phase 1.6: 等待 OpenViking 索引记忆，通过搜索 API 验证。"""
        expected = test_data.get("expected_entities", ["小明", "咪咪"])
        query = " ".join(expected)
        max_wait = timeouts.get("memory_wait", 180)
        poll = timeouts.get("memory_poll_interval", 10)

        for elapsed in range(0, max_wait, poll):
            try:
                data = ov_search(query, limit=10)
                result = data.get("result", {})
                memories = (
                    result.get("memories", [])
                    if isinstance(result, dict) else result
                )
                if isinstance(memories, list):
                    all_text = json.dumps(memories, ensure_ascii=False)
                    hits = [e for e in expected if e in all_text]
                    if set(hits) >= set(expected):
                        logger.info("memory ready after %ds, hits=%s", elapsed, hits)
                        return
                    logger.info("memory poll %ds: hits=%s (need %s)", elapsed, hits, expected)
            except Exception as exc:
                logger.info("memory poll %ds: error=%s", elapsed, exc)
            time.sleep(poll)

        pytest.fail(f"memory entities {expected} not found within {max_wait}s")

    def test_04_qa_recall(self, test_data, timeouts):
        """Phase 2: QA — 在独立 session 中提问，验证关键词召回。"""
        qa_pairs = test_data["qa_pairs"]

        for qi, qa in enumerate(qa_pairs, 1):
            question = qa["question"]
            expected_kws = qa.get("expected_keywords", [])
            qa_session_key = f"qa-{SESSION_ID}-q{qi}"

            logger.info("[QA %d] Q: %s", qi, question)
            result = oc_send_with_retry(
                f"请直接回答问题：{question}",
                qa_session_key,
                max_retries=timeouts["max_retries"],
                delay=timeouts["retry_delay"],
                timeout=timeouts["message"],
            )
            assert result["success"], f"QA {qi} failed: {result.get('error')}"

            text = result["text"]
            logger.info("[QA %d] A: %s", qi, text[:200])

            found = [kw for kw in expected_kws if kw.lower() in text.lower()]
            missing = [kw for kw in expected_kws if kw.lower() not in text.lower()]
            logger.info("[QA %d] found=%s missing=%s", qi, found, missing)

            self.__class__._qa_results.append({
                "qi": qi, "question": question,
                "gold_answer": qa.get("gold_answer", ""),
                "response": text,
                "keywords_found": found,
                "keywords_missing": missing,
            })

            assert len(found) > 0, (
                f"QA {qi}: 回复中未找到期望关键词 {expected_kws}\n回复: {text[:300]}"
            )

            if qi < len(qa_pairs):
                time.sleep(timeouts.get("message_gap", 10))

    def test_05_judge(self):
        """Phase 3: LLM Judge 评分（可选，需要 JUDGE_API_KEY）。"""
        qa_results = self.__class__._qa_results
        if not qa_results:
            pytest.skip("no QA results to judge")

        api_key = os.environ.get("JUDGE_API_KEY", "")
        if not api_key:
            judge_cfg = CFG.get("judge", {})
            if judge_cfg.get("api_key_env"):
                api_key = os.environ.get(judge_cfg["api_key_env"], "")
        if not api_key:
            pytest.skip("JUDGE_API_KEY not set")

        correct = 0
        for qa in qa_results:
            j = judge_answer(qa["question"], qa["gold_answer"], qa["response"])
            is_correct = j.get("is_correct", False)
            logger.info("[Judge Q%d] %s — %s",
                        qa["qi"],
                        "CORRECT" if is_correct else "WRONG",
                        j.get("reasoning", "")[:100])
            if is_correct:
                correct += 1
            qa["judge"] = j

        accuracy = correct / len(qa_results) if qa_results else 0
        logger.info("Judge accuracy: %d/%d = %.0f%%", correct, len(qa_results), accuracy * 100)
        assert accuracy >= 0.5, f"judge accuracy too low: {accuracy:.1%}"


class TestOpenGaussStorage:
    """Phase 4: openGauss 数据存储验证。"""

    def test_opengauss_has_tables(self, endpoints):
        """验证 openGauss 中已创建 OpenViking 的表。"""
        password = endpoints.og_password
        if not password:
            pytest.skip("OG_PASSWORD not set")

        import psycopg2
        conn = psycopg2.connect(
            host=endpoints.og_host,
            port=endpoints.og_port,
            user=endpoints.og_user,
            password=password,
            dbname=endpoints.og_db,
            connect_timeout=10,
        )
        cur = conn.cursor()
        cur.execute("""
            SELECT tablename FROM pg_tables
            WHERE schemaname = 'public'
            ORDER BY tablename
        """)
        tables = [row[0] for row in cur.fetchall()]
        cur.close()
        conn.close()

        logger.info("openGauss tables: %s", tables)
        assert len(tables) > 0, "openGauss has no tables in public schema"

    def test_opengauss_has_memory_data(self, endpoints, test_data):
        """验证 openGauss 中存储了记忆数据。"""
        password = endpoints.og_password
        if not password:
            pytest.skip("OG_PASSWORD not set")

        import psycopg2
        conn = psycopg2.connect(
            host=endpoints.og_host,
            port=endpoints.og_port,
            user=endpoints.og_user,
            password=password,
            dbname=endpoints.og_db,
            connect_timeout=10,
        )
        cur = conn.cursor()

        cur.execute("""
            SELECT tablename FROM pg_tables
            WHERE schemaname = 'public'
            ORDER BY tablename
        """)
        tables = [row[0] for row in cur.fetchall()]

        total_rows = 0
        for table in tables:
            try:
                cur.execute(f'SELECT COUNT(*) FROM "{table}"')
                count = cur.fetchone()[0]
                logger.info("table %s: %d rows", table, count)
                total_rows += count
            except Exception as exc:
                logger.warning("table %s: query error: %s", table, exc)
                conn.rollback()

        cur.close()
        conn.close()

        logger.info("total rows across all tables: %d", total_rows)
        assert total_rows > 0, "openGauss has no data rows"

    def test_ov_search_api(self, endpoints, test_data):
        """验证 OpenViking Search API 能检索到注入的记忆。"""
        expected = test_data.get("expected_entities", ["小明", "咪咪"])
        query = " ".join(expected)
        data = ov_search(query, limit=10)

        result = data.get("result", {})
        memories = result.get("memories", []) if isinstance(result, dict) else result
        logger.info("OV search returned %d results", len(memories) if isinstance(memories, list) else 0)

        if isinstance(memories, list) and len(memories) > 0:
            all_text = json.dumps(memories, ensure_ascii=False)
            hits = [e for e in expected if e in all_text]
            logger.info("entity hits in search: %s", hits)
            assert len(hits) > 0, f"search results contain no expected entities {expected}"
        else:
            pytest.fail("OpenViking search returned no memories")
