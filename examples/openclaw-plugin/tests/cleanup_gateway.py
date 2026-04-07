"""
OpenClaw Gateway profile cleanup script.

Deletes the state directory for one or more gateway profiles.
Each profile's state directory follows the convention:
  default  -> ~/.openclaw/
  <name>   -> ~/.openclaw-<name>/

Usage:
  # Preview what will be deleted (dry-run, default)
  python cleanup_gateway.py eval-ov eval-mc

  # Actually delete
  python cleanup_gateway.py eval-ov eval-mc --force

  # List all existing profiles
  python cleanup_gateway.py --list
"""

import argparse
import shutil
import sys
from pathlib import Path


def resolve_state_dir(profile: str) -> Path:
    home = Path.home()
    if profile == "default":
        return home / ".openclaw"
    return home / f".openclaw-{profile}"


def discover_profiles() -> list[tuple[str, Path]]:
    """Find all .openclaw* profile directories under the user's home."""
    home = Path.home()
    results: list[tuple[str, Path]] = []
    for entry in sorted(home.iterdir()):
        if not entry.is_dir():
            continue
        name = entry.name
        if name == ".openclaw":
            results.append(("default", entry))
        elif name.startswith(".openclaw-"):
            profile = name[len(".openclaw-"):]
            if profile:
                results.append((profile, entry))
    return results


def dir_size_mb(path: Path) -> float:
    total = sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
    return total / (1024 * 1024)


def print_profile_info(profile: str, state_dir: Path) -> None:
    config_file = state_dir / "openclaw.json"
    has_config = config_file.exists()
    size = dir_size_mb(state_dir)
    print(f"  {profile:20s}  {str(state_dir):50s}  {size:6.1f} MB  {'(has config)' if has_config else '(no config)'}")


def main():
    parser = argparse.ArgumentParser(
        description="Clean up OpenClaw gateway profiles by removing their state directories",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # List all profiles
  python cleanup_gateway.py --list

  # Preview deletion (dry-run)
  python cleanup_gateway.py eval-ov eval-mc

  # Delete profiles
  python cleanup_gateway.py eval-ov eval-mc --force

  # Delete with glob pattern
  python cleanup_gateway.py --pattern "eval-*" --force
""",
    )

    parser.add_argument("profiles", nargs="*", help="Profile names to delete (e.g. eval-ov eval-mc)")
    parser.add_argument("--list", action="store_true", help="List all existing gateway profiles")
    parser.add_argument("--pattern", default=None, help="Glob pattern to match profile names (e.g. 'eval-*')")
    parser.add_argument("--force", action="store_true", help="Actually delete (without this flag, only previews)")

    args = parser.parse_args()

    if args.list:
        profiles = discover_profiles()
        if not profiles:
            print("No gateway profiles found.")
            return
        print(f"\nFound {len(profiles)} profile(s):\n")
        print(f"  {'Profile':20s}  {'Directory':50s}  {'Size':>8s}  Note")
        print(f"  {'-'*20}  {'-'*50}  {'-'*8}  {'-'*12}")
        for name, path in profiles:
            print_profile_info(name, path)
        print()
        return

    targets: list[str] = list(args.profiles)

    if args.pattern:
        import fnmatch
        all_profiles = discover_profiles()
        matched = [name for name, _ in all_profiles if fnmatch.fnmatch(name, args.pattern)]
        targets.extend(matched)

    targets = list(dict.fromkeys(targets))

    if not targets:
        parser.print_help()
        sys.exit(1)

    print(f"\n{'[DRY-RUN] ' if not args.force else ''}Cleanup targets:\n")

    found: list[tuple[str, Path]] = []
    not_found: list[str] = []

    for profile in targets:
        state_dir = resolve_state_dir(profile)
        if state_dir.exists():
            found.append((profile, state_dir))
        else:
            not_found.append(profile)

    if not_found:
        for name in not_found:
            expected = resolve_state_dir(name)
            print(f"  [SKIP] {name:20s}  directory not found: {expected}")
        print()

    if not found:
        print("No matching profile directories to delete.")
        return

    for profile, state_dir in found:
        size = dir_size_mb(state_dir)
        if args.force:
            try:
                shutil.rmtree(state_dir)
                print(f"  [DELETED] {profile:20s}  {state_dir}  ({size:.1f} MB)")
            except Exception as e:
                print(f"  [ERROR]   {profile:20s}  {state_dir}  {e}")
        else:
            print(f"  [WOULD DELETE] {profile:20s}  {state_dir}  ({size:.1f} MB)")

    print()
    if not args.force:
        print("This was a dry-run. Add --force to actually delete.\n")


if __name__ == "__main__":
    main()
