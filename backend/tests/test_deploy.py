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


# --- the parameters file (this is the step a dry run skips) -----------------------------------

def run_in_script_shell(body: str):
    """Source the script and run `body` in the same shell, so assignments stay plain (unexported) like typed answers."""
    clean = {k: v for k, v in os.environ.items() if k not in ("APP_PASSWORD", "ANTHROPIC_API_KEY", "DRY_RUN")}
    return subprocess.run(["bash", "-c", f'set -euo pipefail\nsource "{SCRIPT}"\n{body}'], capture_output=True, text=True,
                          stdin=subprocess.DEVNULL, env=clean)


def test_typed_secrets_reach_the_parameters_file_even_though_they_are_not_exported():
    r = run_in_script_shell(f"""
        APP_PASSWORD='{PASSWORD}'
        ANTHROPIC_API_KEY='{KEY}'
        IMAGE='testuser/ai-financial-agent:abc'
        write_params
        echo "PATH:$PARAMS_FILE"
        ls -l "$PARAMS_FILE" | cut -c1-10
        cat "$PARAMS_FILE"
    """)
    assert r.returncode == 0, r.stderr
    path = re.search(r"PATH:(\S+)", r.stdout).group(1)
    assert "-rw-------" in r.stdout                      # readable only by the user
    import json
    params = json.loads(r.stdout[r.stdout.index("{"):])["parameters"]
    assert params["appPassword"]["value"] == PASSWORD and params["anthropicApiKey"]["value"] == KEY
    assert params["image"]["value"] == "docker.io/testuser/ai-financial-agent:abc"
    assert not os.path.exists(path)                      # removed when the script ends


def test_an_empty_secret_stops_the_deploy_before_azure_is_called():
    r = run_in_script_shell(f"""
        APP_PASSWORD=''
        ANTHROPIC_API_KEY='{KEY}'
        IMAGE='testuser/ai-financial-agent:abc'
        write_params
    """)
    assert r.returncode != 0 and "empty" in r.stderr.lower()


# --- review fixes -------------------------------------------------------------------------------

REPO = Path(__file__).resolve().parents[2]


def test_no_stray_copy_files_are_tracked():
    tracked = subprocess.run(["git", "ls-files"], capture_output=True, text=True, cwd=REPO).stdout.splitlines()
    assert [f for f in tracked if " copy" in f] == []


def test_dockerignore_keeps_every_env_file_out_of_the_public_image():
    lines = (REPO / ".dockerignore").read_text().splitlines()
    assert "**/.env*" in lines
    assert "* copy.py" not in lines     # the stray file is deleted, not hidden


def test_readme_never_puts_the_api_key_on_a_command_line():
    assert not re.search(r"-e ANTHROPIC_API_KEY=", (REPO / "README.md").read_text())


def test_readme_no_longer_carries_the_dropped_hugging_face_route():
    text = (REPO / "README.md").read_text()
    assert "Hugging Face" not in text and not text.startswith("---")


def test_destroy_needs_only_the_azure_cli_but_deploy_also_needs_docker():
    azure_only = run_in_script_shell("PATH=/nonexistent; az() { :; }; preflight az")
    assert azure_only.returncode == 0, azure_only.stderr
    with_docker = run_in_script_shell("PATH=/nonexistent; az() { :; }; preflight az docker")
    assert with_docker.returncode != 0 and "docker" in with_docker.stderr.lower()


def test_a_missing_docker_hub_login_is_caught_before_the_slow_build(tmp_path):
    config = tmp_path / ".docker"
    config.mkdir()
    (config / "config.json").write_text('{"auths": {}}')
    r = run_in_script_shell(f'DOCKER_CONFIG="{config}"; docker_hub_login_ok')
    assert r.returncode != 0 and "docker login" in r.stderr
    (config / "config.json").write_text('{"auths": {"https://index.docker.io/v1/": {}}, "credsStore": "desktop"}')
    assert run_in_script_shell(f'DOCKER_CONFIG="{config}"; docker_hub_login_ok').returncode == 0


def make_repo(path: Path, dirty: bool) -> str:
    def git(*args):
        return subprocess.run(["git", *args], cwd=path, capture_output=True, text=True, check=True).stdout.strip()

    path.mkdir()
    git("init", "-q")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "t")
    (path / "a.txt").write_text("one")
    git("add", ".")
    git("commit", "-q", "-m", "one")
    if dirty:
        (path / "a.txt").write_text("two")
    return git("rev-parse", "--short", "HEAD")


def test_the_image_tag_is_the_commit_when_clean_and_unique_when_there_are_uncommitted_changes(tmp_path):
    clean_hash = make_repo(tmp_path / "clean", dirty=False)
    r = run_in_script_shell(f'ROOT="{tmp_path / "clean"}"; DOCKERHUB_USER=me; need_image; echo "$IMAGE"')
    assert r.stdout.strip() == f"me/ai-financial-agent:{clean_hash}"
    dirty_hash = make_repo(tmp_path / "dirty", dirty=True)
    r = run_in_script_shell(f'ROOT="{tmp_path / "dirty"}"; DOCKERHUB_USER=me; need_image 2>/dev/null; echo "$IMAGE"')
    assert re.fullmatch(rf"me/ai-financial-agent:{dirty_hash}-dirty\d+", r.stdout.strip())   # a rerun is never a silent no-op


def test_dry_run_does_not_ask_for_secrets():
    r = run_script("deploy", DOCKERHUB_USER="testuser")     # no password, no key, no terminal
    assert r.returncode == 0, r.stderr
    assert "az deployment group create" in r.stdout
