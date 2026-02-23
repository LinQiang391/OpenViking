#!/usr/bin/env python3
"""Patch a local MemoryBench checkout to add the OpenViking provider."""

from __future__ import annotations

import argparse
import re
from pathlib import Path


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def write_text(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def insert_after_once(text: str, needle: str, insertion: str, label: str) -> str:
    if insertion.strip() in text:
        return text

    idx = text.find(needle)
    if idx < 0:
        raise RuntimeError(f"Could not find anchor for {label}: {needle!r}")

    pos = idx + len(needle)
    return text[:pos] + insertion + text[pos:]


def patch_provider_type(path: Path) -> bool:
    text = read_text(path)
    if '"openviking"' in text:
        return False

    pattern = r"(export type ProviderName\s*=\s*[^\n]+)"
    match = re.search(pattern, text)
    if not match:
        raise RuntimeError("Could not find ProviderName declaration")

    replacement = match.group(1) + ' | "openviking"'
    text = text[: match.start(1)] + replacement + text[match.end(1) :]
    write_text(path, text)
    return True


def patch_providers_index(path: Path) -> bool:
    original = text = read_text(path)

    if 'from "./openviking"' not in text:
        text = insert_after_once(
            text,
            'import { RAGProvider } from "./rag"\n',
            'import { OpenVikingProvider } from "./openviking"\n',
            "OpenViking import",
        )

    if "openviking: OpenVikingProvider," not in text:
        anchor = "  rag: RAGProvider,\n"
        if anchor not in text:
            raise RuntimeError("Could not find providers map anchor for rag")
        text = text.replace(anchor, anchor + "  openviking: OpenVikingProvider,\n", 1)

    export_pattern = r"export \{([^\n]+)\}\n"
    export_match = re.search(export_pattern, text)
    if not export_match:
        raise RuntimeError("Could not find providers export line")

    export_items = [item.strip() for item in export_match.group(1).split(",") if item.strip()]
    if "OpenVikingProvider" not in export_items:
        export_items.append("OpenVikingProvider")
        export_line = "export { " + ", ".join(export_items) + " }\n"
        text = text[: export_match.start()] + export_line + text[export_match.end() :]

    if text != original:
        write_text(path, text)
        return True
    return False


def patch_config(path: Path) -> bool:
    original = text = read_text(path)

    if "openvikingApiKey: string" not in text:
        text = insert_after_once(
            text,
            "  zepApiKey: string\n",
            "  openvikingApiKey: string\n  openvikingBaseUrl: string\n",
            "Config interface fields",
        )

    if "process.env.OPENVIKING_API_KEY" not in text:
        text = insert_after_once(
            text,
            '  zepApiKey: process.env.ZEP_API_KEY || "",\n',
            '  openvikingApiKey: process.env.OPENVIKING_API_KEY || "",\n'
            '  openvikingBaseUrl: process.env.OPENVIKING_BASE_URL || "http://localhost:1933",\n',
            "config env fields",
        )

    if 'case "openviking":' not in text:
        anchor = '    case "filesystem":\n'
        if anchor not in text:
            raise RuntimeError("Could not find switch anchor for filesystem provider")
        insertion = (
            '    case "openviking":\n'
            "      return { apiKey: config.openvikingApiKey, baseUrl: config.openvikingBaseUrl }\n"
        )
        text = text.replace(anchor, insertion + anchor, 1)

    if text != original:
        write_text(path, text)
        return True
    return False


def patch_cli_help(path: Path) -> bool:
    original = text = read_text(path)

    provider_block = (
        "  openviking     OpenViking - Self-hosted memory layer via OpenViking HTTP API\n"
        "                 Requires: OPENVIKING_BASE_URL (optional, default: http://localhost:1933)\n"
        "                 Optional: OPENVIKING_API_KEY\n\n"
    )
    if (
        "  openviking     OpenViking - Self-hosted memory layer via OpenViking HTTP API\n"
        not in text
    ):
        anchor = "Usage:\n"
        if anchor not in text:
            raise RuntimeError("Could not find providers help Usage section")
        text = text.replace(anchor, provider_block + anchor, 1)

    usage_line = "  -p openviking    Use OpenViking as the memory provider\n"
    if usage_line not in text:
        anchor = "  -p rag            Use hybrid RAG memory (OpenClaw/QMD style)\n"
        if anchor not in text:
            raise RuntimeError("Could not find rag usage line in providers help")
        text = text.replace(anchor, anchor + usage_line, 1)

    if text != original:
        write_text(path, text)
        return True
    return False


def copy_provider_template(memorybench_root: Path, template_path: Path) -> bool:
    target = memorybench_root / "src/providers/openviking/index.ts"
    target.parent.mkdir(parents=True, exist_ok=True)

    template = read_text(template_path)
    if target.exists() and read_text(target) == template:
        return False

    write_text(target, template)
    return True


def validate_memorybench_root(root: Path) -> None:
    required = [
        root / "src/types/provider.ts",
        root / "src/providers/index.ts",
        root / "src/utils/config.ts",
        root / "src/cli/index.ts",
    ]
    missing = [str(p) for p in required if not p.exists()]
    if missing:
        raise RuntimeError(
            "The given path does not look like MemoryBench (missing files):\n" + "\n".join(missing)
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Install OpenViking provider into a local MemoryBench checkout"
    )
    parser.add_argument(
        "--memorybench-path",
        required=True,
        help="Path to local supermemoryai/memorybench repo",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    script_dir = Path(__file__).resolve().parent
    provider_template = script_dir / "openviking_provider.ts"
    if not provider_template.exists():
        raise RuntimeError(f"Provider template not found: {provider_template}")

    memorybench_root = Path(args.memorybench_path).expanduser().resolve()
    validate_memorybench_root(memorybench_root)

    changed = []

    if copy_provider_template(memorybench_root, provider_template):
        changed.append("src/providers/openviking/index.ts")

    if patch_provider_type(memorybench_root / "src/types/provider.ts"):
        changed.append("src/types/provider.ts")

    if patch_providers_index(memorybench_root / "src/providers/index.ts"):
        changed.append("src/providers/index.ts")

    if patch_config(memorybench_root / "src/utils/config.ts"):
        changed.append("src/utils/config.ts")

    if patch_cli_help(memorybench_root / "src/cli/index.ts"):
        changed.append("src/cli/index.ts")

    print("OpenViking provider install complete.")
    if changed:
        print("Updated files:")
        for rel in changed:
            print(f"  - {rel}")
    else:
        print("No file changes were needed (already installed).")


if __name__ == "__main__":
    main()
