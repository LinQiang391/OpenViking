"""
Local 模式 E2E 测试

每个测试场景 = 一个测试类:
  - setup: 安装配置（变量：不同安装场景）
  - test_e2e_flow: ingest → QA → judge → 存储验证（固定管线）
  - teardown: 清理

变量: 安装过程的不同交互场景
固定: 会话注入 → QA → judge 验证管线
"""

import json
import logging
import os
import time
import uuid
from typing import Any, Dict, List, Optional

import pytest
import requests

from config.settings import (
    JUDGE_API_KEY,
    JUDGE_BASE_URL,
    JUDGE_ENABLED,
    JUDGE_MODEL,
    LOG_DIR,
    MODEL_PRIMARY,
    MODEL_PROVIDER,
    OPENVIKING_PORT,
    PLUGIN_VERSION,
    PROFILE_GATEWAY_PORT,
    PROFILE_NAME,
    TEST_QA_PAIRS,
    TEST_SESSION_MESSAGES,
    get_effective_config,
)
from utils.process_manager import ProcessManager
from utils.profile_manager import ProfileManager

logger = logging.getLogger(__name__)


# ── Judge 工具 ────────────────────────────────────────────────

JUDGE_PROMPT_TEMPLATE = """
Your task is to label an answer to a question as 'CORRECT' or 'WRONG'.

Question: {question}
Gold answer: {gold_answer}
Generated answer: {response}

Be generous: as long as the generated answer touches on the same topic as the gold answer, count it as CORRECT.

Respond with JSON only: {{"is_correct": "CORRECT" or "WRONG", "reasoning": "your explanation"}}
"""


def judge_answer(question: str, gold_answer: str, response: str) -> Dict[str, Any]:
    """用 LLM 判断 response 是否匹配 gold_answer。"""
    result: Dict[str, Any] = {"is_correct": None, "reasoning": "", "error": None}

    api_key = JUDGE_API_KEY
    if not api_key:
        result["error"] = "no judge API key"
        return result

    base_url = JUDGE_BASE_URL or (MODEL_PROVIDER or {}).get("baseUrl", "")
    model = JUDGE_MODEL

    prompt = JUDGE_PROMPT_TEMPLATE.format(
        question=question, gold_answer=gold_answer, response=response,
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
        start = content.find("{")
        end = content.rfind("}")
        if start != -1 and end != -1:
            parsed = json.loads(content[start:end + 1])
            result["is_correct"] = parsed.get("is_correct", "WRONG").upper() == "CORRECT"
            result["reasoning"] = parsed.get("reasoning", "")
        else:
            result["error"] = f"cannot parse judge response: {content[:200]}"
    except Exception as exc:
        result["error"] = str(exc)
        logger.error("judge failed: %s", exc)

    return result


# ── 可复用的验证管线 ──────────────────────────────────────────

def _ov_auth_headers(profile: ProfileManager) -> Dict[str, str]:
    """从隔离 ov.conf 中读取 API key，构造 Authorization 头。"""
    headers: Dict[str, str] = {}
    ov_conf = getattr(profile, "_ov_conf", None)
    if ov_conf and os.path.isfile(ov_conf):
        try:
            with open(ov_conf) as f:
                conf = json.load(f)
            api_key = conf.get("vlm", {}).get("api_key", "") or conf.get("api_key", "")
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"
        except Exception:
            pass
    return headers


def _send_with_retry(profile, message, session_key, max_retries=3, delay=15, **kwargs):
    """发送消息，遇到 rate limit 或超时自动重试。"""
    result = None
    for attempt in range(1, max_retries + 1):
        result = profile.send_message_via_api(
            message=message, session_key=session_key, **kwargs
        )
        if result["success"]:
            text = result.get("text", "")
            if "rate limit" in text.lower() or "try again" in text.lower():
                logger.warning("rate limited (attempt %d/%d), waiting %ds...", attempt, max_retries, delay)
                time.sleep(delay)
                continue
            return result
        error = result.get("error", "")
        if "timed out" in str(error).lower() and attempt < max_retries:
            logger.warning("timeout (attempt %d/%d), retrying in %ds...", attempt, max_retries, delay)
            time.sleep(delay)
            continue
        return result
    return result


def run_ingest(profile: ProfileManager, session_id: str,
               user: str = "e2e-user",
               agent_id: str = "e2e-local") -> List[Dict[str, Any]]:
    """Phase 1: 会话注入 — 发送消息建立记忆。

    使用固定的 ingest session key，所有 ingest 消息共享同一个对话上下文。
    """
    ingest_session_key = f"agent:{agent_id}:openresponses-user:{user}"
    logger.info("ingest session_key: %s", ingest_session_key)

    responses = []
    for i, msg in enumerate(TEST_SESSION_MESSAGES, 1):
        logger.info("[ingest %d/%d] %s", i, len(TEST_SESSION_MESSAGES), msg[:80])
        result = _send_with_retry(
            profile, message=msg,
            session_key=ingest_session_key,
            user=user, agent_id=agent_id,
        )
        assert result["success"], f"ingest message {i} failed: {result.get('error', result)}"
        text = result.get("text", "")
        assert "rate limit" not in text.lower(), f"ingest {i} still rate limited after retries"
        logger.info("[ingest %d] response: %s", i, text[:200])
        responses.append(result)

        if i < len(TEST_SESSION_MESSAGES):
            time.sleep(10)

    return responses


def _get_ov_data_dir(profile: ProfileManager) -> str:
    """获取隔离 OV 的数据根目录。"""
    return os.path.join(profile.home, "openviking-data")


def _scan_memory_files(data_dir: str) -> List[str]:
    """扫描 OV 数据目录下的记忆文件（排除 .overview.md / .abstract.md 等元文件）。"""
    memories_root = os.path.join(data_dir, "viking", "default", "user", "default", "memories")
    found = []
    if not os.path.isdir(memories_root):
        return found
    for root, _dirs, files in os.walk(memories_root):
        for f in files:
            if f.startswith(".") or not f.endswith(".md"):
                continue
            found.append(os.path.join(root, f))
    return found


def _wait_for_memory_files(profile: ProfileManager, max_wait: int = 180,
                           poll_interval: int = 10,
                           expected_entities: Optional[List[str]] = None):
    """等待 OV 在文件系统上生成记忆文件。

    同时轮询 OV API，提供更完整的诊断信息。
    如果指定了 expected_entities（如 ["小明", "咪咪"]），会检查文件名或内容是否包含。
    """
    data_dir = _get_ov_data_dir(profile)
    ov_port = getattr(profile, "_ov_port", OPENVIKING_PORT)
    ov_url = f"http://127.0.0.1:{ov_port}"
    logger.info("waiting for OV memory files in %s (max %ds)...", data_dir, max_wait)

    if expected_entities is None:
        expected_entities = ["小明"]

    headers = _ov_auth_headers(profile)

    rounds = max_wait // poll_interval
    for wait_round in range(1, rounds + 1):
        time.sleep(poll_interval)

        mem_files = _scan_memory_files(data_dir)
        entity_hits = []
        for ent in expected_entities:
            for mf in mem_files:
                if ent in os.path.basename(mf):
                    entity_hits.append(ent)
                    break
                try:
                    with open(mf) as fh:
                        if ent in fh.read():
                            entity_hits.append(ent)
                            break
                except Exception:
                    pass

        api_count = 0
        try:
            resp = requests.post(
                f"{ov_url}/api/v1/search/find",
                json={"query": " ".join(expected_entities), "limit": 5},
                headers=headers, timeout=10,
            )
            if resp.status_code == 200:
                data = resp.json()
                result = data.get("result", {})
                memories = result.get("memories", []) if isinstance(result, dict) else result
                api_count = len(memories) if memories else 0
        except Exception:
            pass

        logger.info("[sync %d/%d] mem_files=%d entity_hits=%s api_results=%d",
                     wait_round, rounds, len(mem_files), entity_hits, api_count)

        if set(entity_hits) >= set(expected_entities):
            logger.info("memory files ready (%ds): %s",
                         wait_round * poll_interval,
                         [os.path.basename(f) for f in mem_files])
            return True

    logger.warning("memory files NOT complete after %ds: found %d files, entities=%s",
                    max_wait, len(mem_files), entity_hits)
    return False


def run_qa(profile: ProfileManager, session_id: str,
           user: str = "e2e-user",
           agent_id: str = "e2e-local") -> List[Dict[str, Any]]:
    """Phase 2: QA 验证 — 在全新的独立 session 中发问。

    每个问题使用不同的 session key（qa-{session_id}-q{n}），
    与 ingest session 完全隔离，仅靠 OV 记忆系统召回信息。
    """
    results = []
    for qi, qa in enumerate(TEST_QA_PAIRS, 1):
        question = qa["question"]
        expected_kws = qa.get("expected_keywords", [])

        qa_session_key = f"qa-{session_id}-q{qi}"
        logger.info("[QA %d] Q: %s (session: %s)", qi, question, qa_session_key)

        result = _send_with_retry(
            profile,
            message=f"请直接回答问题：{question}",
            session_key=qa_session_key,
            user=user, agent_id=agent_id,
        )
        assert result["success"], f"QA {qi} failed: {result.get('error', result)}"

        response_text = result["text"]
        logger.info("[QA %d] A: %s", qi, response_text[:200])

        found = [kw for kw in expected_kws if kw.lower() in response_text.lower()]
        missing = [kw for kw in expected_kws if kw.lower() not in response_text.lower()]
        logger.info("[QA %d] keywords found=%s missing=%s", qi, found, missing)

        results.append({
            "qi": qi, "question": question,
            "gold_answer": qa.get("gold_answer", ""),
            "response": response_text,
            "keywords_found": found, "keywords_missing": missing,
            "expected_keywords": expected_kws,
        })

        if qi < len(TEST_QA_PAIRS):
            time.sleep(10)

        assert len(found) > 0, (
            f"QA {qi}: 回复中未找到任何期望关键词 {expected_kws}\n"
            f"回复: {response_text[:300]}"
        )

    return results


def run_judge(qa_results: List[Dict[str, Any]]) -> float:
    """Phase 3: Judge 评分 — LLM 对比 response 与 gold answer。"""
    if not JUDGE_ENABLED or not JUDGE_API_KEY:
        logger.info("judge skipped (enabled=%s, has_key=%s)", JUDGE_ENABLED, bool(JUDGE_API_KEY))
        return -1.0

    correct = 0
    for qa in qa_results:
        judge_result = judge_answer(
            question=qa["question"],
            gold_answer=qa["gold_answer"],
            response=qa["response"],
        )
        is_correct = judge_result.get("is_correct", False)
        logger.info("[Judge Q%d] %s - %s", qa["qi"],
                     "CORRECT" if is_correct else "WRONG",
                     judge_result.get("reasoning", "")[:100])
        if is_correct:
            correct += 1
        qa["judge"] = judge_result

    accuracy = correct / len(qa_results) if qa_results else 0
    logger.info("Judge accuracy: %d/%d = %.1f%%", correct, len(qa_results), accuracy * 100)
    return accuracy


def verify_ov_storage(profile: ProfileManager,
                      expected_entities: Optional[List[str]] = None):
    """Phase 4: 存储验证 — 记忆文件 + OV API + session JSONL。

    验证三个层面:
      4a: 文件系统上的记忆文件（entities 目录下有 .md 文件，且包含预期实体）
      4b: OV API 搜索结果包含预期实体内容
      4c: OpenClaw session JSONL 文件存在且包含消息
    """
    if expected_entities is None:
        expected_entities = ["小明", "咪咪"]

    data_dir = _get_ov_data_dir(profile)
    ov_port = getattr(profile, "_ov_port", OPENVIKING_PORT)
    ov_url = f"http://127.0.0.1:{ov_port}"
    headers = _ov_auth_headers(profile)

    # 4a: 文件系统记忆文件验证
    mem_files = _scan_memory_files(data_dir)
    logger.info("[verify 4a] memory files found: %d", len(mem_files))
    for mf in mem_files:
        logger.info("  - %s", os.path.relpath(mf, data_dir))
    assert len(mem_files) > 0, (
        f"OV 数据目录下没有任何记忆文件: {data_dir}/viking/default/user/default/memories/\n"
        f"这意味着 OpenViking 的记忆提取功能没有正常工作。"
    )

    missing_entities = []
    for ent in expected_entities:
        found_in_file = False
        for mf in mem_files:
            if ent in os.path.basename(mf):
                found_in_file = True
                break
            try:
                with open(mf) as fh:
                    if ent in fh.read():
                        found_in_file = True
                        break
            except Exception:
                pass
        if not found_in_file:
            missing_entities.append(ent)

    if missing_entities:
        logger.warning("[verify 4a] entities NOT found in memory files: %s", missing_entities)
    else:
        logger.info("[verify 4a] all expected entities found: %s", expected_entities)
    assert not missing_entities, (
        f"记忆文件中未找到以下实体: {missing_entities}\n"
        f"已有文件: {[os.path.basename(f) for f in mem_files]}"
    )

    # 4b: OV API 搜索验证
    if ProcessManager.is_port_listening(ov_port):
        try:
            resp = requests.post(
                f"{ov_url}/api/v1/search/find",
                json={"query": "小明 工程师 Python 咪咪", "limit": 10},
                headers=headers, timeout=15,
            )
            logger.info("[verify 4b] OV API: status=%d body=%s", resp.status_code, resp.text[:400])
            if resp.status_code == 200:
                data = resp.json()
                result = data.get("result", {})
                memories = result.get("memories", []) if isinstance(result, dict) else result
                if isinstance(memories, list):
                    all_text = json.dumps(memories, ensure_ascii=False)
                    api_entity_hits = [e for e in expected_entities if e in all_text]
                    logger.info("[verify 4b] API entity hits: %s (total results: %d)",
                                 api_entity_hits, len(memories))
                    assert len(api_entity_hits) > 0, (
                        f"OV API 搜索结果中未包含任何预期实体 {expected_entities}\n"
                        f"返回内容: {all_text[:500]}"
                    )
        except requests.ConnectionError:
            logger.warning("[verify 4b] OV API unreachable at %s", ov_url)
    else:
        logger.warning("[verify 4b] OV not listening on port %d, skip API check", ov_port)

    # 4c: Session JSONL 文件
    agents_dir = os.path.join(profile.home, "agents")
    if os.path.isdir(agents_dir):
        session_files = []
        for root, _dirs, files in os.walk(agents_dir):
            for f in files:
                if f.endswith(".jsonl"):
                    session_files.append(os.path.join(root, f))

        logger.info("[verify 4c] session .jsonl files: %d", len(session_files))
        assert session_files, "no session JSONL files found"

        latest = max(session_files, key=os.path.getmtime)
        with open(latest) as fh:
            lines = fh.readlines()
        has_message = any(
            json.loads(l).get("type") == "message"
            for l in lines
            if l.strip()
        )
        logger.info("[verify 4c] latest session: %s (%d lines, has_message=%s)",
                     latest, len(lines), has_message)
        assert has_message, f"session file {latest} has no message events"
    else:
        logger.warning("[verify 4c] agents dir not found: %s", agents_dir)


def log_summary(qa_results: List[Dict[str, Any]]):
    """汇总报告。"""
    logger.info("=" * 60)
    logger.info("E2E SUMMARY | model=%s | plugin=%s", MODEL_PRIMARY, PLUGIN_VERSION)
    logger.info("=" * 60)
    for qa in qa_results:
        judge = qa.get("judge", {})
        j_status = "CORRECT" if judge.get("is_correct") else (
            "WRONG" if judge.get("is_correct") is False else "N/A"
        )
        logger.info("  Q%d: %s | kw=%s | judge=%s",
                     qa["qi"], qa["question"][:40], qa["keywords_found"], j_status)
    logger.info("=" * 60)


# ── 测试场景 ──────────────────────────────────────────────────

@pytest.mark.clawhub
@pytest.mark.local
class TestLocalE2ESingle:
    """Local 模式 E2E: clawhub 安装 → ingest → QA → judge → verify。"""

    SESSION_ID = f"e2e-local-{uuid.uuid4().hex[:8]}"
    profile: ProfileManager = None

    @classmethod
    def setup_class(cls):
        logger.info("=" * 60)
        logger.info("SETUP: local mode profile '%s'", PROFILE_NAME)
        cfg = get_effective_config()
        logger.info("config: %s", json.dumps(cfg, indent=2, ensure_ascii=False))
        logger.info("=" * 60)

        cls.profile = ProfileManager()
        steps = cls.profile.full_setup(mode="local")
        logger.info("setup steps: %s", steps)

        for k, v in steps.items():
            if k.endswith(("_error", "mode")) or k == "interactive_setup":
                continue
            assert v, f"setup '{k}' failed: {steps}"
        assert steps.get("interactive_setup", False), \
            f"interactive setup failed: {steps.get('setup_error', 'unknown')}"

        assert cls.profile.gateway_healthy(), f"gateway unhealthy on {PROFILE_GATEWAY_PORT}"
        ov_port = getattr(cls.profile, "_ov_port", OPENVIKING_PORT)
        logger.info("setup OK (OV port=%d, data=%s/openviking-data/)", ov_port, cls.profile.home)

    @classmethod
    def teardown_class(cls):
        logger.info("TEARDOWN: destroying profile '%s'", PROFILE_NAME)
        if cls.profile:
            result = cls.profile.full_teardown()
            logger.info("teardown: %s", result)

    def test_e2e_flow(self):
        """完整 E2E 管线: ingest → sessions.compact → 等待记忆文件 → QA(独立session) → judge → 验证。

        关键设计：
          - ingest 和 QA 使用完全不同的 session key，隔绝上下文
          - ingest 后通过 Gateway WebSocket RPC 端到端调用 sessions.compact
            （WebSocket → Gateway → OV Plugin compact → OV commit → 记忆提取）
          - QA 阶段仅靠 OV 记忆系统召回信息
        """
        profile = self.__class__.profile
        sid = self.SESSION_ID
        user = "e2e-user"
        agent_id = "e2e-local"

        # Phase 1: Ingest（所有消息在同一个 ingest session 中）
        ingest_responses = run_ingest(profile, sid, user=user, agent_id=agent_id)
        assert len(ingest_responses) == len(TEST_SESSION_MESSAGES), \
            f"ingest incomplete: {len(ingest_responses)}/{len(TEST_SESSION_MESSAGES)}"

        # Phase 1.5: 通过 Gateway WebSocket RPC 端到端触发 sessions.compact
        # 走完整链路: WebSocket → Gateway → sessions.compact → OV Plugin compact → OV commit
        ingest_session_key = f"agent:{agent_id}:openresponses-user:{user}"
        logger.info("triggering sessions.compact via Gateway RPC (session_key: %s)", ingest_session_key)
        compact_result = profile.trigger_compact(ingest_session_key)
        logger.info("compact result: %s", compact_result)
        assert compact_result.get("success"), (
            f"sessions.compact 失败: {compact_result.get('error', compact_result)}"
        )

        # Phase 1.6: 等待 OV 记忆文件生成
        mem_ready = _wait_for_memory_files(
            profile, max_wait=180, poll_interval=10,
            expected_entities=["小明", "咪咪"],
        )
        assert mem_ready, (
            "OpenViking 未在 180s 内生成预期的记忆文件（小明、咪咪），"
            "记忆提取功能可能未正常工作"
        )

        # Phase 2: QA（每个问题用独立 session，隔绝 ingest 上下文）
        qa_results = run_qa(profile, sid, user=user, agent_id=agent_id)

        # Phase 3: Judge
        accuracy = run_judge(qa_results)
        if accuracy >= 0:
            assert accuracy >= 0.5, f"judge accuracy too low: {accuracy:.1%}"

        # Phase 4: 存储验证（记忆文件 + API + session JSONL）
        verify_ov_storage(profile, expected_entities=["小明", "咪咪"])

        # 汇总
        log_summary(qa_results)
