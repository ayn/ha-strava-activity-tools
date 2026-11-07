"""Resolve immutable provider targets without placing release text in shell commands."""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]


def load_config() -> dict:
    return json.loads((ROOT / "provider-compatibility.json").read_text())


def choose_target(config: dict, target: str, fetch) -> dict[str, str]:
    if config["upstream_repository"] != "craibo/ha_strava":
        raise ValueError("Unexpected upstream repository")
    if target == "baseline":
        repository = config["baseline"]["repository"]
        ref = config["baseline"]["ref"]
        label = "baseline"
    else:
        repository = config["upstream_repository"]
        if target == "latest-release":
            release = fetch(f"repos/{repository}/releases/latest")
            label = release.get("tag_name", "")
            if (
                release.get("draft")
                or release.get("prerelease")
                or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,127}", label)
            ):
                raise ValueError("Invalid stable release metadata")
        elif target == "upstream-main":
            label = "main"
        else:
            raise ValueError("Unknown compatibility target")
        ref = fetch(f"repos/{repository}/commits/{quote(label, safe='')}")["sha"]
    if repository not in {"ayn/ha_strava", "craibo/ha_strava"} or not re.fullmatch(
        r"[0-9a-f]{40}", ref
    ):
        raise ValueError("Expected approved repository and immutable commit")
    return {
        "repository": repository,
        "ref": ref,
        "label": label,
        "target": target,
        "known_unsupported": str(config["known_unsupported"].get(label) == ref).lower(),
    }


def github(path: str) -> dict:
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "provider-compatibility"}
    if token := os.environ.get("GITHUB_TOKEN"):
        headers["Authorization"] = f"Bearer {token}"
    with urlopen(
        Request(f"https://api.github.com/{path}", headers=headers), timeout=20
    ) as response:
        return json.load(response)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--target", choices=("baseline", "latest-release", "upstream-main"), required=True
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument("--summary", type=Path)
    args = parser.parse_args()
    target = choose_target(load_config(), args.target, github)
    print(json.dumps(target))
    if args.output:
        with args.output.open("a") as output:
            output.write("".join(f"{key}={value}\n" for key, value in target.items()))
    if args.summary:
        with args.summary.open("a") as summary:
            summary.write(
                f"## Provider compatibility\n\n{target['repository']} · {target['label']}\n\n"
                f"Commit: `{target['ref']}`\n\n"
            )
            if target["known_unsupported"] == "true":
                summary.write(
                    "**Known missing prerequisite:** this exact released build does not "
                    "contain the required activity-created event (upstream #349). "
                    "It is not certified compatible. Use force to demonstrate the failure.\n"
                )


if __name__ == "__main__":
    main()
