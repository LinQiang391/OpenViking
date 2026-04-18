"""
LocomoSmall 一键性能评测: 基于 locomo10_small.json (1 sample, 50 QA) 评估记忆召回质量。

流程:
  1. 加载 locomo10_small.json (来自 memcore 分支 benchmark/locomo/data/)
  2. 将 4 个 session 的对话通过 OpenClaw /v1/responses 注入
  3. 每个 session 注入后触发 compact 强制持久化
  4. 执行 50 个 QA (跳过 category=5 adversarial)，记录回答
  5. 使用 LLM Judge 评估 CORRECT/WRONG
  6. 按 category 统计准确率，输出 CSV 报告

运行:
  pytest test_locomo_benchmark.py -v -s --tb=short

  # 自定义数据路径
  LOCOMO_DATA=/path/to/locomo10_small.json pytest test_locomo_benchmark.py -v

需要:
  - JUDGE_API_KEY 或 VOLCENGINE_API_KEY 环境变量 (LLM Judge)
  - locomo10_small.json 数据文件（默认从 memcore 分支 git show 获取）

输出:
  - tests/reports/locomo_results.csv
  - tests/reports/locomo_summary.json
"""

import csv
import json
import logging
import os
import subprocess
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock

import pytest
import requests

from conftest import CFG, ENDPOINTS

logger = logging.getLogger("e2e.locomo")

REPORTS_DIR = Path(__file__).parent / "reports"
REPORTS_DIR.mkdir(exist_ok=True)

AGENT_ID = "locomo-eval"
csv_lock = Lock()

CATEGORY_NAMES = {
    1: "single-hop",
    2: "multi-hop",
    3: "temporal",
    4: "world-knowledge",
    5: "adversarial",
}


# ── Data Loading ──────────────────────────────────────────────────


def load_locomo_data(path: str | None = None) -> list:
    """Load locomo JSON. Falls back to git show from memcore branch."""
    if path and os.path.isfile(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    try:
        result = subprocess.run(
            ["git", "show", "memcore:benchmark/locomo/data/locomo10_small.json"],
            capture_output=True, text=True, check=True,
            cwd=os.environ.get("PROJECT_ROOT", str(Path(__file__).parents[4])),
        )
        return json.loads(result.stdout)
    except (subprocess.CalledProcessError, FileNotFoundError, json.JSONDecodeError) as exc:
        pytest.skip(f"Cannot load locomo data: {exc}")


def format_message(msg: dict) -> str:
    """Format a LoCoMo message into chat-style text."""
    speaker = msg.get("speaker", "unknown")
    text = msg.get("text", "")
    line = f"{speaker}: {text}"
    img_urls = msg.get("img_url", [])
    if isinstance(img_urls, str):
        img_urls = [img_urls]
    blip = msg.get("blip_caption", "")
    if img_urls:
        for url in img_urls:
            caption = f": {blip}" if blip else ""
            line += f"\n{url}{caption}"
    elif blip:
        line += f"\n({blip})"
    return line


def build_session_messages(sample: dict) -> list[dict]:
    """Build bundled messages per session for one LoCoMo sample."""
    conv = sample["conversation"]
    speakers = f"{conv['speaker_a']} & {conv['speaker_b']}"

    session_keys = sorted(
        [k for k in conv if k.startswith("session_") and not k.endswith("_date_time")],
        key=lambda k: int(k.split("_")[1]),
    )

    sessions = []
    for sk in session_keys:
        dt_key = f"{sk}_date_time"
        date_time = conv.get(dt_key, "")
        parts = [f"[group chat conversation: {date_time}]"]
        for msg in conv[sk]:
            parts.append(format_message(msg))
        combined = "\n\n".join(parts)
        sessions.append({
            "message": combined,
            "meta": {
                "sample_id": sample["sample_id"],
                "session_key": sk,
                "date_time": date_time,
                "speakers": speakers,
            },
        })
    return sessions


def get_question_time(sample: dict) -> str | None:
    """Extract the last session's date as ISO for question context."""
    conv = sample.get("conversation", {})
    session_keys = [k for k in conv if k.startswith("session_") and "date_time" not in k]
    if not session_keys:
        return None
    session_keys.sort(key=lambda k: int(k.split("_")[1]), reverse=True)
    for sk in session_keys:
        if conv.get(sk):
            dt_key = f"session_{sk.split('_')[1]}_date_time"
            date_str = conv.get(dt_key, "")
            if " on " in date_str:
                try:
                    date_part = date_str.split(" on ")[-1]
                    dt = datetime.strptime(date_part.strip(), "%d %B, %Y")
                    return dt.strftime("%Y-%m-%d")
                except ValueError:
                    pass
    return None


# ── API Helpers ───────────────────────────────────────────────────


def send_message(message: str, user: str, session_key: str | None = None,
                 timeout: int = 600) -> tuple[str, dict]:
    """Send via OpenClaw /v1/responses."""
    headers = {
        "Content-Type": "application/json",
        "X-OpenClaw-Agent-ID": AGENT_ID,
    }
    if ENDPOINTS.oc_token:
        headers["Authorization"] = f"Bearer {ENDPOINTS.oc_token}"
    if session_key:
        headers["X-OpenClaw-Session-Key"] = session_key

    payload = {
        "model": "openclaw",
        "input": message,
        "stream": False,
    }
    if user:
        payload["user"] = user

    resp = requests.post(
        f"{ENDPOINTS.oc_base_url}/v1/responses",
        json=payload, headers=headers, timeout=timeout,
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

    usage = body.get("usage", {})
    return text, usage


def trigger_compact(session_key: str, timeout: int = 300) -> bool:
    """Trigger sessions.compact via WebSocket RPC."""
    try:
        import websocket
    except ImportError:
        logger.warning("websocket-client not installed, skip compact")
        return False

    ws_url = ENDPOINTS.oc_base_url.replace("http://", "ws://")
    try:
        ws = websocket.create_connection(ws_url, timeout=timeout)
        challenge = json.loads(ws.recv())
        if challenge.get("event") != "connect.challenge":
            return False

        cid = str(uuid.uuid4())
        ws.send(json.dumps({
            "type": "req", "id": cid, "method": "connect",
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
            if msg.get("type") == "res" and msg.get("id") == cid:
                if not msg.get("ok"):
                    return False
                break

        rid = str(uuid.uuid4())
        ws.send(json.dumps({
            "type": "req", "id": rid,
            "method": "sessions.compact",
            "params": {"key": session_key},
        }))
        while True:
            msg = json.loads(ws.recv())
            if msg.get("type") == "res" and msg.get("id") == rid:
                ws.close()
                return msg.get("ok", False)
    except Exception as exc:
        logger.warning("compact failed: %s", exc)
        return False


def judge_single(question: str, gold: str, response: str) -> dict:
    """LLM judge for a single QA pair."""
    api_key = os.environ.get("JUDGE_API_KEY", "")
    if not api_key:
        for env_name in ("VOLCENGINE_API_KEY", "ARK_API_KEY", "OPENAI_API_KEY"):
            api_key = os.environ.get(env_name, "")
            if api_key:
                break
    if not api_key:
        return {"is_correct": None, "error": "no key"}

    base_url = CFG.get("models", {}).get("provider", {}).get("baseUrl", "")
    model = CFG.get("models", {}).get("judge", "")

    prompt = (
        f"Label the answer CORRECT or WRONG.\n\n"
        f"Question: {question}\nGold answer: {gold}\nGenerated answer: {response}\n\n"
        f"Be generous — if the answer touches the same topic, count CORRECT.\n"
        f"For time questions, any format referring to the same date is CORRECT.\n"
        f'Respond JSON only: {{"is_correct": "CORRECT" or "WRONG", "reasoning": "..."}}'
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
        s, e = content.find("{"), content.rfind("}")
        if s != -1 and e != -1:
            parsed = json.loads(content[s:e + 1])
            return {
                "is_correct": parsed.get("is_correct", "WRONG").upper() == "CORRECT",
                "reasoning": parsed.get("reasoning", ""),
            }
        return {"is_correct": None, "error": f"parse: {content[:100]}"}
    except Exception as exc:
        return {"is_correct": None, "error": str(exc)}


# ── CSV helpers ───────────────────────────────────────────────────


def init_csv(path: str) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            "sample_id", "qi", "category", "category_name",
            "question", "expected", "response",
            "is_correct", "reasoning",
            "input_tokens", "output_tokens", "total_tokens",
        ])


def append_csv(path: str, row: dict) -> None:
    with csv_lock:
        with open(path, "a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            usage = row.get("usage", {})
            writer.writerow([
                row.get("sample_id", ""),
                row.get("qi", ""),
                row.get("category", ""),
                CATEGORY_NAMES.get(int(row.get("category", 0)), "unknown"),
                row.get("question", ""),
                row.get("expected", ""),
                row.get("response", ""),
                row.get("is_correct", ""),
                row.get("reasoning", ""),
                usage.get("input_tokens", 0),
                usage.get("output_tokens", 0),
                usage.get("total_tokens", 0),
            ])


# ── Tests ─────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def locomo_data():
    data_path = os.environ.get("LOCOMO_DATA")
    return load_locomo_data(data_path)


class TestLocomoBenchmark:
    """LocomoSmall 一键召回评测。"""

    _records: list = []

    @pytest.fixture(autouse=True, scope="class")
    def _setup(self, locomo_data):
        self.__class__._records = []
        self.__class__._data = locomo_data
        yield

    def test_01_ingest_conversations(self, locomo_data):
        """注入 LoCoMo 对话到 OpenClaw (所有 sessions)。"""
        for sample_idx, sample in enumerate(locomo_data):
            sample_id = sample["sample_id"]
            user_key = f"eval-{sample_idx}"
            sessions = build_session_messages(sample)

            logger.info("=== Sample %s: %d sessions ===", sample_id, len(sessions))

            for sess in sessions:
                meta = sess["meta"]
                msg = sess["message"]
                label = f"{meta['session_key']} ({meta['date_time']})"
                logger.info("[ingest] %s: %s...", label, msg[:60].replace("\n", " "))

                try:
                    reply, usage = send_message(msg, user_key)
                    logger.info("[ingest] reply: %s", reply[:80])
                except Exception as exc:
                    logger.error("[ingest] %s failed: %s", label, exc)
                    pytest.fail(f"ingest failed for {label}: {exc}")

                session_key = f"agent:{AGENT_ID}:openresponses-user:{user_key}"
                trigger_compact(session_key)

        logger.info("=== Ingest complete ===")

    def test_02_run_qa(self, locomo_data):
        """执行 QA 评测 (跳过 category=5 adversarial)。"""
        csv_path = str(REPORTS_DIR / "locomo_results.csv")
        init_csv(csv_path)

        all_records = []

        for sample_idx, sample in enumerate(locomo_data):
            sample_id = sample["sample_id"]
            user_key = f"eval-{sample_idx}"
            question_time = get_question_time(sample)
            qas = [q for q in sample.get("qa", []) if str(q.get("category", "")) != "5"]

            logger.info("=== QA for %s: %d questions (excl. adversarial) ===",
                        sample_id, len(qas))

            for qi, qa in enumerate(qas, 1):
                question = qa["question"]
                expected = str(qa["answer"])
                category = qa.get("category", "")
                session_key = f"qa-{sample_id}-q{qi}"

                if question_time:
                    input_msg = f"Current date: {question_time}. Answer the question directly: {question}"
                else:
                    input_msg = f"Answer the question directly: {question}"

                logger.info("[QA %d/%d] %s", qi, len(qas), question[:60])

                try:
                    response, usage = send_message(input_msg, sample_id, session_key)
                    logger.info("[QA %d] A: %s", qi, response[:80])
                except Exception as exc:
                    response = f"[ERROR] {exc}"
                    usage = {}
                    logger.error("[QA %d] failed: %s", qi, exc)

                record = {
                    "sample_id": sample_id,
                    "qi": qi,
                    "category": category,
                    "question": question,
                    "expected": expected,
                    "response": response,
                    "usage": usage,
                }
                all_records.append(record)
                append_csv(csv_path, record)

        self.__class__._records = all_records
        logger.info("QA complete: %d records → %s", len(all_records), csv_path)
        assert len(all_records) > 0, "no QA records generated"

    def test_03_judge_and_report(self, locomo_data):
        """LLM Judge 评分 + 生成汇总报告。"""
        records = self.__class__._records
        if not records:
            pytest.skip("no QA records")

        api_key = os.environ.get("JUDGE_API_KEY", "")
        for env_name in ("VOLCENGINE_API_KEY", "ARK_API_KEY", "OPENAI_API_KEY"):
            if not api_key:
                api_key = os.environ.get(env_name, "")
        if not api_key:
            pytest.skip("no judge API key available")

        logger.info("=== Judging %d answers ===", len(records))
        correct_by_cat: dict[str, list[bool]] = {}

        for rec in records:
            j = judge_single(rec["question"], rec["expected"], rec["response"])
            is_correct = j.get("is_correct", False) or False
            rec["is_correct"] = is_correct
            rec["reasoning"] = j.get("reasoning", "")

            cat = str(rec.get("category", "?"))
            correct_by_cat.setdefault(cat, []).append(is_correct)

            status = "CORRECT" if is_correct else "WRONG"
            logger.info("[Judge Q%s] %s — %s", rec["qi"], status, rec["reasoning"][:60])

        total_correct = sum(1 for r in records if r.get("is_correct"))
        total = len(records)
        overall_acc = total_correct / total if total else 0

        summary = {
            "timestamp": datetime.now().isoformat(),
            "total_questions": total,
            "total_correct": total_correct,
            "overall_accuracy": round(overall_acc, 4),
            "by_category": {},
        }
        for cat, results in sorted(correct_by_cat.items()):
            cat_correct = sum(results)
            cat_total = len(results)
            cat_name = CATEGORY_NAMES.get(int(cat), f"cat-{cat}")
            summary["by_category"][cat_name] = {
                "correct": cat_correct,
                "total": cat_total,
                "accuracy": round(cat_correct / cat_total, 4) if cat_total else 0,
            }

        summary_path = REPORTS_DIR / "locomo_summary.json"
        with open(summary_path, "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2, ensure_ascii=False)

        csv_path = str(REPORTS_DIR / "locomo_results.csv")
        init_csv(csv_path)
        for rec in records:
            append_csv(csv_path, rec)

        logger.info("=" * 60)
        logger.info("LoCoMo Benchmark Results")
        logger.info("=" * 60)
        logger.info("Overall: %d/%d = %.1f%%", total_correct, total, overall_acc * 100)
        for cat_name, stats in summary["by_category"].items():
            logger.info("  %s: %d/%d = %.1f%%",
                        cat_name, stats["correct"], stats["total"], stats["accuracy"] * 100)
        logger.info("Reports: %s", REPORTS_DIR)
        logger.info("=" * 60)
