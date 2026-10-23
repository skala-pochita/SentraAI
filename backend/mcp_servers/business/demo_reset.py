"""make demo-reset: targets/ 를 /workspace 로 복사하고 git 저장소로 초기화 (명세 20.1).

/workspace/vuln-spring-app/   취약 코드 (git, main)
/workspace/agent-config/      manifest.yaml + system_prompt.md + 구현 소스 snapshot (AGENT_VARIANT, git, main)
"""

from __future__ import annotations

import argparse
import logging
import shutil
import subprocess
from pathlib import Path

import yaml

from core.settings import get_settings

log = logging.getLogger("demo_reset")


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, timeout=30)  # noqa: S603,S607


def _init_repo(path: Path, message: str) -> None:
    _git(path, "init", "-q", "-b", "main")
    _git(path, "config", "user.email", "auditor-bot@demo.io")
    _git(path, "config", "user.name", "AI Security Auditor")
    _git(path, "add", "-A")
    _git(path, "commit", "-q", "-m", message)


def reset(targets_dir: Path, workspace: Path, variant: str) -> None:
    workspace.mkdir(parents=True, exist_ok=True)
    repo_dst = workspace / "vuln-spring-app"
    if repo_dst.exists():
        shutil.rmtree(repo_dst)
    shutil.copytree(targets_dir / "vuln-spring-app", repo_dst)
    _init_repo(repo_dst, "initial import (demo)")

    cfg_dst = workspace / "agent-config"
    if cfg_dst.exists():
        shutil.rmtree(cfg_dst)
    cfg_dst.mkdir()
    agent_src = targets_dir / "target-agent"
    manifest_src = agent_src / "manifests" / f"{variant}.yaml"
    manifest = yaml.safe_load(manifest_src.read_text(encoding="utf-8"))
    shutil.copy(manifest_src, cfg_dst / "manifest.yaml")
    shutil.copy(agent_src / manifest["system_prompt_file"], cfg_dst / "system_prompt.md")
    # Source snapshots are evidence candidates; they do not prove the running image version.
    for name in ("app.py", "policy.py"):
        if (agent_src / name).is_file():
            shutil.copy(agent_src / name, cfg_dst / name)
    _init_repo(cfg_dst, f"agent config ({variant})")
    log.info("workspace reset: %s (variant=%s)", workspace, variant)


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    s = get_settings()
    ap = argparse.ArgumentParser()
    ap.add_argument("--targets", default=str(s.repo_targets_dir))
    ap.add_argument("--workspace", default=str(s.workspace_dir))
    ap.add_argument("--variant", default=s.agent_variant)
    a = ap.parse_args()
    reset(Path(a.targets), Path(a.workspace), a.variant)


if __name__ == "__main__":
    main()
