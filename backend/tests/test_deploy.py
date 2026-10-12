"""Azure deployment files: the Bicep template and the script that runs it. No cloud calls: the script has a dry run."""

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

DEPLOY = Path(__file__).resolve().parents[2] / "deploy" / "azure"
BICEP = DEPLOY / "main.bicep"
SCRIPT = DEPLOY / "deploy.sh"
PASSWORD = "dummy-pass-123456"
KEY = "dummy-key-abc"


def run_script(*args, **env):
    clean = {k: v for k, v in os.environ.items() if k not in ("APP_PASSWORD", "ANTHROPIC_API_KEY", "DOCKERHUB_USER")}
    return subprocess.run(["bash", str(SCRIPT), *args], capture_output=True, text=True, stdin=subprocess.DEVNULL,
                          env={**clean, "DRY_RUN": "1", **env})


# --- the template ----------------------------------------------------------------------------

def test_bicep_keeps_one_replica_that_can_scale_to_zero():
    text = BICEP.read_text()
    assert "minReplicas: 0" in text      # costs nothing while idle
    assert "maxReplicas: 1" in text      # the database lives in the container, so a second copy would not share it


def test_bicep_listens_on_the_container_port_over_https_only():
    text = BICEP.read_text()
    assert "targetPort: 7860" in text and "external: true" in text and "allowInsecure: false" in text


def test_bicep_takes_the_password_and_key_as_secure_parameters_and_never_inlines_them():
    text = BICEP.read_text()
    assert len(re.findall(r"@secure\(\)\s*\nparam (appPassword|anthropicApiKey) string", text)) == 2
    assert "secretRef: 'app-password'" in text and "secretRef: 'anthropic-api-key'" in text
    assert "sk-ant" not in text


def test_bicep_has_no_http_probe_because_every_page_is_behind_the_password():
    assert "probes" not in BICEP.read_text()


def test_bicep_caps_log_ingestion_to_bound_the_cost():
    assert "dailyQuotaGb" in BICEP.read_text()


@pytest.mark.skipif(shutil.which("az") is None, reason="Azure CLI not installed")
def test_bicep_compiles():
    r = subprocess.run(["az", "bicep", "build", "--file", str(BICEP), "--stdout"], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-500:]


# --- the script ------------------------------------------------------------------------------

def test_script_has_valid_shell_syntax_and_fails_fast():
    assert subprocess.run(["bash", "-n", str(SCRIPT)]).returncode == 0
    assert "set -euo pipefail" in SCRIPT.read_text()


def test_deploy_dry_run_lists_every_step_and_builds_for_amd64():
    r = run_script("deploy", DOCKERHUB_USER="testuser", APP_PASSWORD=PASSWORD, ANTHROPIC_API_KEY=KEY)
    assert r.returncode == 0, r.stderr
    out = r.stdout
    assert "docker buildx build --platform linux/amd64" in out    # Azure Container Apps does not run arm64 images
    assert "testuser/ai-financial-agent:" in out
    assert "docker push" in out
    assert "az group create" in out
    assert "az deployment group create" in out
    assert out.index("docker push") < out.index("az deployment group create")


def test_dry_run_never_prints_the_secrets():
    r = run_script("deploy", DOCKERHUB_USER="testuser", APP_PASSWORD=PASSWORD, ANTHROPIC_API_KEY=KEY)
    assert PASSWORD not in r.stdout + r.stderr and KEY not in r.stdout + r.stderr


def test_script_keeps_secrets_off_the_command_line():
    text = SCRIPT.read_text()
    assert "read -rs" in text                       # typed secrets are not echoed
    assert "umask 077" in text and "trap" in text   # the parameters file is private and removed afterwards
    assert not re.search(r"(appPassword|anthropicApiKey)=\"?\$", text)   # never passed as az arguments


def test_a_short_password_is_refused_before_anything_runs():
    r = run_script("deploy", DOCKERHUB_USER="testuser", APP_PASSWORD="short", ANTHROPIC_API_KEY=KEY)
    assert r.returncode != 0 and "12" in r.stderr
    assert "docker" not in r.stdout


def test_deploy_needs_a_docker_hub_user():
    r = run_script("deploy", APP_PASSWORD=PASSWORD, ANTHROPIC_API_KEY=KEY)
    assert r.returncode != 0 and "DOCKERHUB_USER" in r.stderr


def test_update_pushes_a_new_image_and_swaps_it_in():
    r = run_script("update", DOCKERHUB_USER="testuser")
    assert r.returncode == 0, r.stderr
    assert "docker push" in r.stdout and "az containerapp update" in r.stdout and "--image" in r.stdout
    assert "az group create" not in r.stdout


def test_destroy_deletes_the_whole_resource_group():
    r = run_script("destroy")
    assert r.returncode == 0, r.stderr
    assert "az group delete" in r.stdout and "rg-ai-financial-agent" in r.stdout


def test_unknown_commands_print_usage_and_fail():
    r = run_script("explode")
    assert r.returncode != 0 and "Usage" in r.stderr
