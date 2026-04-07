"""
OpenViking server-side data cleanup script.

Removes sessions, agent-scoped memories, and optionally user-scoped memories
associated with specific OpenClaw gateway profiles (agent IDs) from the
OpenViking server.

Data layout on the server:
  Sessions (per user):     viking://session/{user_id}/{session_id}
  User memories (shared):  viking://user/{user_id}/memories/...
  Agent memories (isolated): viking://agent/{agent_space_hash}/memories/...
    where agent_space_hash = MD5("{user_id}:{agent_id}")[:12]

Usage:
  # List what data exists for given agent prefixes (dry-run, default)
  python cleanup_openviking.py eval-ov eval-mc

  # Delete agent-scoped data
  python cleanup_openviking.py eval-ov eval-mc --force

  # Delete including user-scoped memories (careful: shared across agents)
  python cleanup_openviking.py eval-ov eval-mc --force --include-user-memories

  # Use custom server URL and API key
  python cleanup_openviking.py eval-ov --url http://localhost:1933 --api-key <key>

  # Specify account/user explicitly
  python cleanup_openviking.py eval-ov --account default --user default --force
"""

import argparse
import hashlib
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Optional


DEFAULT_OV_URL = "http://127.0.0.1:1933"
DEFAULT_ACCOUNT = "default"
DEFAULT_USER = "default"
DEFAULT_OPENCLAW_AGENT = "main"


class OpenVikingClient:
    """Minimal HTTP client for OpenViking cleanup operations."""

    def __init__(
        self,
        base_url: str,
        account_id: str,
        user_id: str,
        api_key: Optional[str] = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.account_id = account_id
        self.user_id = user_id
        self.api_key = api_key

    def _headers(self, agent_id: str = "default") -> dict[str, str]:
        h: dict[str, str] = {
            "Content-Type": "application/json",
            "X-OpenViking-Account": self.account_id,
            "X-OpenViking-User": self.user_id,
            "X-OpenViking-Agent": agent_id,
        }
        if self.api_key:
            h["X-API-Key"] = self.api_key
        return h

    def _request(
        self,
        method: str,
        path: str,
        *,
        agent_id: str = "default",
        params: Optional[dict[str, str]] = None,
    ) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, method=method, headers=self._headers(agent_id))
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            return {"status": "error", "http_code": e.code, "detail": body}
        except urllib.error.URLError as e:
            return {"status": "error", "detail": str(e.reason)}

    def check_connection(self) -> bool:
        result = self._request("GET", "/api/v1/system/status")
        return result.get("status") == "ok"

    def list_sessions(self, agent_id: str = "default") -> list[dict]:
        result = self._request("GET", "/api/v1/sessions", agent_id=agent_id)
        if result.get("status") != "ok":
            return []
        sessions = result.get("result", [])
        return sessions if isinstance(sessions, list) else []

    def delete_session(self, session_id: str, agent_id: str = "default") -> dict:
        return self._request(
            "DELETE",
            f"/api/v1/sessions/{urllib.parse.quote(session_id, safe='')}",
            agent_id=agent_id,
        )

    def list_fs(
        self,
        uri: str,
        agent_id: str = "default",
        recursive: bool = False,
    ) -> list[dict]:
        params: dict[str, str] = {"uri": uri, "simple": "false", "output": "original"}
        if recursive:
            params["recursive"] = "true"
        result = self._request("GET", "/api/v1/fs/ls", agent_id=agent_id, params=params)
        if result.get("status") != "ok":
            return []
        items = result.get("result", [])
        return items if isinstance(items, list) else []

    def delete_fs(self, uri: str, recursive: bool = True, agent_id: str = "default") -> dict:
        params: dict[str, str] = {"uri": uri}
        if recursive:
            params["recursive"] = "true"
        return self._request("DELETE", "/api/v1/fs", agent_id=agent_id, params=params)

    def tree_fs(self, uri: str, agent_id: str = "default") -> dict:
        params = {"uri": uri, "output": "original", "level_limit": "2", "show_all_hidden": "true"}
        return self._request("GET", "/api/v1/fs/tree", agent_id=agent_id, params=params)


def compute_agent_space(user_id: str, agent_id: str) -> str:
    """Compute agent_space_name the same way the server does."""
    source = f"{user_id}:{agent_id}"
    return hashlib.md5(source.encode()).hexdigest()[:12]


def resolve_wire_agent_ids(profile: str, openclaw_agents: list[str]) -> list[str]:
    """Resolve the agent IDs that would appear on the wire.

    When deploy_gateway sets agentId to the profile name (non-default),
    the plugin sends: {profile}_{openclaw_agent} (sanitized).
    """
    if profile == "default":
        return list(openclaw_agents)
    return [f"{profile}_{a}" for a in openclaw_agents]


def print_section(title: str) -> None:
    print(f"\n  --- {title} ---")


def main():
    parser = argparse.ArgumentParser(
        description="Clean up OpenViking server-side data for gateway profiles",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Preview data for eval-ov and eval-mc profiles
  python cleanup_openviking.py eval-ov eval-mc

  # Delete agent-scoped data
  python cleanup_openviking.py eval-ov eval-mc --force

  # Also delete user-scoped memories (shared across agents!)
  python cleanup_openviking.py eval-ov eval-mc --force --include-user-memories

  # Custom OpenViking server
  python cleanup_openviking.py eval-ov --url http://localhost:1933 --api-key YOUR_KEY
""",
    )

    parser.add_argument("profiles", nargs="+", help="Gateway profile names (agent ID prefixes)")
    parser.add_argument("--url", default=DEFAULT_OV_URL, help=f"OpenViking server URL (default: {DEFAULT_OV_URL})")
    parser.add_argument("--api-key", default=None, help="OpenViking API key (omit for dev mode)")
    parser.add_argument("--account", default=DEFAULT_ACCOUNT, help=f"Account ID (default: {DEFAULT_ACCOUNT})")
    parser.add_argument("--user", default=DEFAULT_USER, help=f"User ID (default: {DEFAULT_USER})")
    parser.add_argument("--openclaw-agents", default="main", help="Comma-separated OpenClaw agent names (default: main)")
    parser.add_argument("--force", action="store_true", help="Actually delete (without this, only previews)")
    parser.add_argument("--include-user-memories", action="store_true", help="Also delete user-scoped memories (WARNING: shared across agents)")
    parser.add_argument("--delete-sessions", action="store_true", default=True, dest="delete_sessions", help="Delete sessions (default: yes)")
    parser.add_argument("--no-sessions", action="store_false", dest="delete_sessions", help="Skip session deletion")

    args = parser.parse_args()
    openclaw_agents = [a.strip() for a in args.openclaw_agents.split(",") if a.strip()]

    client = OpenVikingClient(
        base_url=args.url,
        account_id=args.account,
        user_id=args.user,
        api_key=args.api_key,
    )

    print(f"\n  OpenViking: {args.url}")
    print(f"  Account: {args.account}  User: {args.user}")

    if not client.check_connection():
        print(f"\n  ERROR: Cannot connect to OpenViking at {args.url}")
        print("  Make sure the server is running.")
        sys.exit(1)
    print("  Connection: OK")

    mode_label = "CLEANUP" if args.force else "DRY-RUN"
    print(f"\n{'=' * 62}")
    print(f"  [{mode_label}] Profiles: {', '.join(args.profiles)}")
    print(f"{'=' * 62}")

    total_sessions_deleted = 0
    total_agent_trees_deleted = 0

    for profile in args.profiles:
        wire_agent_ids = resolve_wire_agent_ids(profile, openclaw_agents)
        print(f"\n  Profile: {profile}")
        print(f"  Wire agent IDs: {wire_agent_ids}")

        # --- Sessions ---
        if args.delete_sessions:
            print_section("Sessions")
            sessions = client.list_sessions(agent_id=wire_agent_ids[0] if wire_agent_ids else "default")
            if not sessions:
                print("    No sessions found")
            else:
                print(f"    Found {len(sessions)} session(s)")
                for s in sessions:
                    sid = s.get("session_id", s) if isinstance(s, dict) else str(s)
                    label = ""
                    if isinstance(s, dict):
                        created = s.get("created_at", "")
                        msgs = s.get("message_count", "?")
                        label = f"  (created: {created}, messages: {msgs})"
                    if args.force:
                        result = client.delete_session(sid, agent_id=wire_agent_ids[0])
                        status = "DELETED" if result.get("status") == "ok" else f"ERROR: {result}"
                        print(f"    [{status}] {sid}{label}")
                        if result.get("status") == "ok":
                            total_sessions_deleted += 1
                    else:
                        print(f"    [WOULD DELETE] {sid}{label}")

        # --- Agent-scoped memories ---
        print_section("Agent-scoped memories")
        for wire_id in wire_agent_ids:
            agent_space = compute_agent_space(args.user, wire_id)
            agent_uri = f"viking://agent/{agent_space}"
            print(f"    Agent: {wire_id}")
            print(f"    Space: {agent_space} (MD5 of '{args.user}:{wire_id}')")
            print(f"    URI:   {agent_uri}")

            tree_result = client.tree_fs(agent_uri, agent_id=wire_id)
            if tree_result.get("status") == "ok":
                tree_data = tree_result.get("result", {})
                if isinstance(tree_data, dict):
                    children = tree_data.get("children", [])
                    total_nodes = _count_tree_nodes(tree_data)
                    print(f"    Tree:  {total_nodes} node(s), {len(children)} top-level entries")
                elif isinstance(tree_data, list):
                    print(f"    Items: {len(tree_data)} entries")
                else:
                    print(f"    Data:  {type(tree_data)}")
            else:
                print(f"    Tree:  (empty or not found)")

            if args.force:
                result = client.delete_fs(agent_uri, recursive=True, agent_id=wire_id)
                if result.get("status") == "ok":
                    print(f"    [DELETED] {agent_uri}")
                    total_agent_trees_deleted += 1
                else:
                    print(f"    [SKIP/ERROR] {result.get('detail', result)}")
            else:
                print(f"    [WOULD DELETE] {agent_uri} (recursive)")

    # --- User-scoped memories (optional, shared!) ---
    if args.include_user_memories:
        print_section("User-scoped memories (SHARED across all agents)")
        user_uri = f"viking://user/{args.user}"
        print(f"    URI: {user_uri}")
        print(f"    WARNING: This data is shared by ALL agents under user '{args.user}'")

        tree_result = client.tree_fs(user_uri)
        if tree_result.get("status") == "ok":
            tree_data = tree_result.get("result", {})
            total_nodes = _count_tree_nodes(tree_data) if isinstance(tree_data, dict) else 0
            print(f"    Tree: {total_nodes} node(s)")
        else:
            print(f"    Tree: (empty or not found)")

        if args.force:
            result = client.delete_fs(user_uri, recursive=True)
            if result.get("status") == "ok":
                print(f"    [DELETED] {user_uri}")
            else:
                print(f"    [SKIP/ERROR] {result.get('detail', result)}")
        else:
            print(f"    [WOULD DELETE] {user_uri} (recursive)")

    # --- Summary ---
    print(f"\n{'=' * 62}")
    if args.force:
        print(f"  Sessions deleted: {total_sessions_deleted}")
        print(f"  Agent trees deleted: {total_agent_trees_deleted}")
    else:
        print("  This was a dry-run. Add --force to actually delete.")
    print(f"{'=' * 62}\n")


def _count_tree_nodes(node: dict) -> int:
    count = 1
    for child in node.get("children", []):
        if isinstance(child, dict):
            count += _count_tree_nodes(child)
        else:
            count += 1
    return count


if __name__ == "__main__":
    main()
