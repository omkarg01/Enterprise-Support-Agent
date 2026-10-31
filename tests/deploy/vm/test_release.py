"""M5-06: rollout and rollback on the VM (NFR-28, NFR-29, NFR-36, AC-15), driven against a fake VM that
answers git, docker, ss and df the way the real ones do; the real Temporal CLI's listing format is checked at
the end against the compose Temporal."""

import ast
import importlib.util
import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location("release", ROOT / "deploy" / "vm" / "release.py")
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)

LOCK = json.loads((ROOT / "deploy" / "vm" / "models.json").read_text(encoding="utf-8"))
OLD, NEW, NEWER = "a" * 40, "b" * 40, "c" * 40
ENV = ("MONTHLY_SPEND_CAP_USD=10\nDATABASE_URL=postgresql://u:p@db.test/neondb\nGROQ_API_KEY=gsk\n"
       "PII_TOKEN_KEY=k\n")
TICKET = "@workflow.defn\nclass TicketWorkflow:\n    pass\n"


class FakeVM:
    """What the VM answers. `healthy`: images whose /healthz answers 200."""

    def __init__(self, tmp_path):
        self.calls, self.head, self.live_image = [], OLD, None
        self.dirty, self.listeners, self.free, self.published = "", "", 20 * 1024 ** 3, ""
        self.locks = {OLD: LOCK, NEW: LOCK, NEWER: LOCK}
        self.changed, self.sources, self.running, self.temporal_ok = [], {}, [], True
        self.failing, self.images = set(), []
        self.healthy = {f"support-agent-app:{c[:12]}" for c in (OLD, NEW, NEWER)}
        tmp_path.mkdir(parents=True, exist_ok=True)
        (tmp_path / ".env").write_text(ENV, encoding="utf-8")

    def __call__(self, args, env=None):
        self.calls.append((args, env))
        line = " ".join(args)
        for marker in self.failing:
            if marker in line:
                return 1, "", f"{marker} broke"
        if args[0] == "git":
            return self.git(args[1:])
        if args[0] == "ss":
            return 0, self.listeners, ""
        if args[0] == "df":
            return 0, f"Avail\n{self.free}\n", ""
        docker = args[args.index("docker") + 1:]
        if docker[:1] == ["build"]:
            self.images.append(docker[docker.index("-t") + 1])
            return 0, "", ""
        if docker[:1] == ["ps"]:
            return 0, self.published, ""
        if docker[:2] == ["image", "ls"]:
            return 0, "\n".join(self.images), ""
        if docker[:2] == ["image", "rm"]:
            self.images.remove(docker[2])
            return 0, "", ""
        if "workflow" in docker:
            return (0, json.dumps([{"type": {"name": n}} for n in self.running]), "") if self.temporal_ok \
                else (1, "", "connection refused")
        if "up" in docker:
            self.live_image = env["APP_IMAGE"]
        return 0, "", ""

    def git(self, args):
        if args[0] == "status":
            return 0, self.dirty, ""
        if args[0] == "rev-parse":
            ref = args[-1].removesuffix("^{commit}")
            return (0, {"origin/main": NEW, "newer": NEWER, "old": OLD}.get(ref, ref), "")
        if args[0] == "show":
            commit, _, path = args[1].partition(":")
            if path == "deploy/vm/models.json":
                return 0, json.dumps(self.locks[commit]), ""
            return (0, self.sources[path], "") if path in self.sources else (128, "", "no such path")
        if args[0] == "diff":
            return 0, "\n".join(self.changed), ""
        if args[0] == "checkout":
            self.head = args[-1]
        return 0, "", ""

    def ran(self, word):
        return [args for args, _ in self.calls if word in " ".join(args)]


@pytest.fixture
def vm(tmp_path):
    return FakeVM(tmp_path)


def releaser(vm, tmp_path):
    now = [1_800_000_000.0]

    def sleep(seconds):
        now[0] += seconds
    return release.Release(root=tmp_path, state_dir=tmp_path / "state", run=vm, clock=lambda: now[0],
                           sleep=sleep, http_status=lambda url: 200 if vm.live_image in vm.healthy else 503)


def deployed(vm, tmp_path, *commits):
    """A release tool whose history already has these commits live, in order."""
    tool = releaser(vm, tmp_path)
    for commit in commits:
        tool.rollout(commit, log=lambda *a: None)
    return tool


# A rollout --------------------------------------------------------------------------------------------------

def test_a_first_rollout_builds_migrates_swaps_and_records_the_release(vm, tmp_path):
    tool = releaser(vm, tmp_path)
    assert tool.rollout("origin/main", log=lambda *a: None) == NEW
    steps = [" ".join(args) for args, _ in vm.calls if args[0] != "git" or "checkout" in args]
    order = [next(i for i, s in enumerate(steps) if word in s)
             for word in ("checkout", "build", "migrate.py", "up -d --no-deps api")]
    assert order == sorted(order)
    assert vm.head == NEW and vm.live_image == f"support-agent-app:{NEW[:12]}"
    build = vm.ran("build")[0]
    assert build[build.index("-f") + 1] == "deploy/vm/Dockerfile" and build[-1] == "."
    swap_env = [env for args, env in vm.calls if "up" in args][0]
    assert swap_env == {"APP_IMAGE": f"support-agent-app:{NEW[:12]}", "APP_REVISION": NEW}
    assert [(e["commit"], e["status"]) for e in tool.history()] == [(NEW, "live")]


def test_only_the_live_and_the_previous_image_are_kept(vm, tmp_path):
    tool = deployed(vm, tmp_path, "old", "origin/main", "newer")
    assert vm.images == [f"support-agent-app:{NEW[:12]}", f"support-agent-app:{NEWER[:12]}"]
    assert tool.live()["commit"] == NEWER and tool.live(1)["commit"] == NEW


# The preflight ----------------------------------------------------------------------------------------------

def refusal(vm, tmp_path, tool=None, **options):
    builds = len(vm.ran("build"))
    with pytest.raises(release.Refused) as refused:
        (tool or releaser(vm, tmp_path)).rollout(options.pop("ref", "origin/main"), log=lambda *a: None,
                                                 **options)
    assert len(vm.ran("build")) == builds and vm.live_image in (None, tool and tool.live()["image"])
    return " | ".join(refused.value.problems)


def env(tmp_path, text):
    (tmp_path / ".env").write_text(text, encoding="utf-8")


def test_uncommitted_changes_on_the_vm_refuse_the_rollout(vm, tmp_path):
    vm.dirty = " M deploy/setup-vm.sh\n M src/channels/api/__main__.py"
    assert "uncommitted changes: deploy/setup-vm.sh, src/channels/api/__main__.py" in refusal(vm, tmp_path)


@pytest.mark.parametrize("settings, problem", [
    ("DATABASE_URL=x\n", "MONTHLY_SPEND_CAP_USD must be set"),
    ("MONTHLY_SPEND_CAP_USD=25\n", "MONTHLY_SPEND_CAP_USD must be set and at most 10"),
    ("MONTHLY_SPEND_CAP_USD=ten\n", "MONTHLY_SPEND_CAP_USD must be set"),
    ("MONTHLY_SPEND_CAP_USD=10\nSUPPORT_AGENT_DEV_AUTH=1\n", "anyone sign in with a header"),
    ("MONTHLY_SPEND_CAP_USD=10\n", "PII_TOKEN_KEY is not set"),
])
def test_the_spend_cap_and_header_sign_in_are_checked(vm, tmp_path, settings, problem):
    env(tmp_path, settings)
    assert problem in refusal(vm, tmp_path)


def test_claude_needs_the_console_spend_limit_attested(vm, tmp_path):
    env(tmp_path, ENV + "ANTHROPIC_API_KEY=sk-ant-x\n")
    assert "attest-spend-limit" in refusal(vm, tmp_path)
    tool = releaser(vm, tmp_path)
    for wrong in ("0", "11"):
        with pytest.raises(release.Refused):
            tool.attest_spend_limit(wrong)
    assert tool.attest_spend_limit("10")["anthropic_console_limit_usd"] == 10.0
    assert tool.rollout("origin/main", log=lambda *a: None) == NEW


def test_jev_must_be_pinned_unless_the_operator_allows_it_for_this_release(vm, tmp_path):
    env(tmp_path, ENV + "TYPESAFE_API_KEY=ts\n")
    assert "pins no Jev version" in refusal(vm, tmp_path)
    tool = releaser(vm, tmp_path)
    tool.rollout("origin/main", allow_unpinned=("jev",), log=lambda *a: None)
    assert tool.history()[-1]["allow_unpinned"] == ["jev"]
    vm.locks[NEWER] = {**LOCK, "jev": {"model": "jev-latest"}}
    assert "is an alias" in refusal(vm, tmp_path, tool, ref="newer")


@pytest.mark.parametrize("change, problem", [
    ({"embedding": {**LOCK["embedding"], "model": "nomic-embed-text"}}, "not pinned to a tag"),
    ({"embedding": {**LOCK["embedding"], "model": "nomic-embed-text:latest"}}, "not pinned"),
    ({"embedding": {"provider": "ollama", "model": "nomic-embed-text:v1.5"}}, "dimensions missing"),
    ({"tiers": {"2": {"provider": "anthropic", "model": "claude-opus-5-5"}}}, "tier 2: needs a model and"),
    ({"monthly_cap_usd": 50}, "monthly_cap_usd must be at most 10"),
])
def test_the_model_lock_is_checked_at_the_commit_being_released(vm, tmp_path, change, problem):
    vm.locks[NEW] = {**LOCK, **change}
    assert problem in refusal(vm, tmp_path)


def test_a_new_embedding_model_needs_a_maintenance_rollout_that_rebuilds_the_index(vm, tmp_path):
    tool = deployed(vm, tmp_path, "old")
    vm.locks[NEW] = {**LOCK, "embedding": {"provider": "ollama", "model": "mxbai-embed-large:v1",
                                           "dimensions": 1024}}
    assert "--maintenance" in refusal(vm, tmp_path, tool)
    tool.rollout("origin/main", maintenance=True, log=lambda *a: None)
    steps = [" ".join(args) for args, _ in vm.calls]
    assert steps.index(next(s for s in steps if "reindex.py" in s)) < len(steps) - 1
    reindex = next(i for i, s in enumerate(steps) if "reindex.py" in s)
    swap = max(i for i, s in enumerate(steps) if "up -d --no-deps api" in s)
    assert reindex < swap


def test_workflows_running_on_changed_code_hold_the_release(vm, tmp_path):
    tool = deployed(vm, tmp_path, "old")
    vm.changed = ["src/workflows/ticket.py", "src/cost/router.py"]
    vm.sources = {"src/workflows/ticket.py": TICKET}
    vm.running = ["TicketWorkflow", "TicketWorkflow", "HeldRequestWorkflow"]
    problem = refusal(vm, tmp_path, tool)
    assert "TicketWorkflow x2" in problem and "Continue-As-New" in problem and "HeldRequest" not in problem
    vm.running = ["HeldRequestWorkflow"]                       # its code did not change
    assert tool.rollout("origin/main", log=lambda *a: None) == NEW


def test_an_unknown_temporal_state_holds_a_release_that_changes_workflow_code(vm, tmp_path):
    tool = deployed(vm, tmp_path, "old")
    vm.changed, vm.sources = ["src/workflows/ticket.py"], {"src/workflows/ticket.py": TICKET}
    vm.temporal_ok = False
    assert "cannot list running workflows" in refusal(vm, tmp_path, tool)


def test_a_hand_started_server_on_the_port_and_a_full_disk_refuse(vm, tmp_path):
    vm.listeners = 'LISTEN 0 2048 0.0.0.0:8000 0.0.0.0:* users:(("python",pid=95112,fd=6))'
    vm.free = 2 * 1024 ** 3
    problems = refusal(vm, tmp_path)
    assert "held by a process outside compose" in problems and "2.0 GB free" in problems
    vm.listeners = 'LISTEN 0 4096 0.0.0.0:8000 0.0.0.0:* users:(("docker-proxy",pid=1,fd=4))'
    vm.free = 20 * 1024 ** 3
    assert releaser(vm, tmp_path).rollout("origin/main", log=lambda *a: None) == NEW


def test_a_listener_ss_cannot_name_is_ours_only_if_a_container_publishes_the_port(vm, tmp_path):
    vm.listeners = "LISTEN 0 4096 0.0.0.0:8000 0.0.0.0:*"            # root's docker-proxy, seen without root
    assert "held by a process outside compose" in refusal(vm, tmp_path)
    vm.published = "support-agent-api-1"
    assert releaser(vm, tmp_path).rollout("origin/main", log=lambda *a: None) == NEW


# Failures and rollback --------------------------------------------------------------------------------------

@pytest.mark.parametrize("step", ["build", "migrate.py"])
def test_a_failed_build_or_migration_leaves_the_live_release_untouched(vm, tmp_path, step):
    tool = deployed(vm, tmp_path, "old")
    vm.failing = {step}
    with pytest.raises(release.Failed, match="the live release was not touched"):
        tool.rollout("origin/main", log=lambda *a: None)
    assert vm.live_image == f"support-agent-app:{OLD[:12]}" and vm.head == OLD
    assert tool.history()[-1]["status"] == "failed" and tool.live()["commit"] == OLD


def test_an_unhealthy_release_puts_the_previous_one_back(vm, tmp_path):
    tool = deployed(vm, tmp_path, "old")
    vm.healthy.discard(f"support-agent-app:{NEW[:12]}")
    with pytest.raises(release.Failed, match=f"put {OLD[:12]} back"):
        tool.rollout("origin/main", log=lambda *a: None)
    assert vm.live_image == f"support-agent-app:{OLD[:12]}" and vm.head == OLD
    assert [e["status"] for e in tool.history()] == ["live", "rolled_back"] and tool.live()["commit"] == OLD


def test_an_unhealthy_first_release_is_stopped(vm, tmp_path):
    vm.healthy.clear()
    with pytest.raises(release.Failed, match="no previous release, so the api is stopped"):
        releaser(vm, tmp_path).rollout("origin/main", log=lambda *a: None)
    assert vm.ran("stop api")


def test_rollback_returns_to_the_previous_live_release(vm, tmp_path):
    tool = deployed(vm, tmp_path, "old", "origin/main")
    assert tool.rollback(log=lambda *a: None) == OLD
    assert vm.live_image == f"support-agent-app:{OLD[:12]}" and vm.head == OLD
    assert tool.history()[-1] == {**tool.history()[-1], "status": "live", "rollback_of": NEW}
    assert len(vm.ran("migrate.py")) == 2                       # a rollback changes no schema
    with pytest.raises(release.Refused):
        deployed(FakeVM(tmp_path / "x"), tmp_path / "x").rollback(log=lambda *a: None)


# The tool runs on the VM's python3 --------------------------------------------------------------------------

def test_the_release_tool_is_python_3_9_and_standard_library_only():
    source = (ROOT / "deploy" / "vm" / "release.py").read_text(encoding="utf-8")
    tree = ast.parse(source, feature_version=(3, 9))
    imported = {alias.name.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.Import)
                for alias in node.names}
    imported |= {node.module.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    assert imported <= set(sys.stdlib_module_names)
    assert "removeprefix" not in source or sys.version_info >= (3, 9)


@pytest.mark.parametrize("listing, names", [
    ("", []), ("[]", []),
    ('[{"type": {"name": "TicketWorkflow"}}]', ["TicketWorkflow"]),
    ('{"type": {"name": "A"}}\n{"type": {"name": "B"}}', ["A", "B"]),
])
def test_running_workflows_are_read_from_either_listing_format(listing, names):
    assert release.running_workflow_types(listing) == names


def test_the_pinned_temporal_cli_lists_running_workflows_in_a_format_the_tool_reads():
    compose = ["docker", "compose", "-f", "deploy/compose.yaml", "--env-file", ".env.example"]
    subprocess.run([*compose, "up", "-d", "--wait", "temporal"], cwd=ROOT, check=True, capture_output=True,
                   timeout=180)
    listing = [*compose, "exec", "-T", "temporal", "temporal", "workflow", "list", "--query",
               "ExecutionStatus='Running'", "--output", "json"]
    for _ in range(30):                          # the dev server takes a few seconds to answer after a start
        done = subprocess.run(listing, cwd=ROOT, capture_output=True, text=True, timeout=60)
        if done.returncode == 0:
            break
        time.sleep(1)
    assert done.returncode == 0, done.stderr
    assert all(isinstance(name, str) for name in release.running_workflow_types(done.stdout))


def test_every_workflow_class_in_the_code_is_found_by_the_tool():
    found = set()
    for path in (ROOT / "src").rglob("*.py"):
        found |= release.workflow_types(path.read_text(encoding="utf-8"))
    assert found == {"ErasureWorkflow", "HeldRequestWorkflow", "SagaWorkflow", "TicketWorkflow"}
