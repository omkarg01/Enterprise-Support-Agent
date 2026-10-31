"""Rollout and rollback of the app on the VM (NFR-28 / DL-ADP-03: DL-D4, DL-D5; NFR-29 / DL-ADP-04: DL-D3,
DL-D6, DL-D11; NFR-36; AC-15). Runs with the host's python3 (3.9 on Oracle Linux 9), standard library only,
from the repository root:

    python3 deploy/vm/release.py check REF          the preflight only
    python3 deploy/vm/release.py rollout REF        e.g. origin/main, a tag or a commit
    python3 deploy/vm/release.py rollback           back to the previous live release
    python3 deploy/vm/release.py status
    python3 deploy/vm/release.py attest-spend-limit USD    after setting the Anthropic console limit

A rollout is all at once (one VM, DL-D4): preflight, check out the commit, build the image tagged by it,
migrate (expand only, so the running release keeps working: tests/contracts), swap the api container, and
check /healthz. A failed build or migration leaves the live release untouched; a failed health check puts the
previous image back. Rollback is manual (DL-D5) and needs no schema change, because migrations only expand.

The preflight refuses when: the checkout has uncommitted changes; MONTHLY_SPEND_CAP_USD is missing or above
10, PII_TOKEN_KEY is missing, or development header sign-in is on; Claude is configured but the console spend
limit is not attested; Jev is configured but not pinned in deploy/vm/models.json (DL-D3); workflows are
running whose code the release changes (DL-D6: let them finish or move them with Continue-As-New first); port
8000 is held by a process outside compose; or the disk has less than 3 GB free. A release that changes the
pinned embedding model needs --maintenance: the knowledge collections are rebuilt in place before the swap
(DL-D11).

State (the release history and the spend-limit attestation) lives in ~/.support-agent/, outside the checkout.
"""

import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

CAP_USD = 10.0
MIN_FREE_BYTES = 3 * 1024 ** 3
HEALTH_URL = "http://127.0.0.1:8000/healthz"
HEALTH_SECONDS = 90
KEEP_IMAGES = 2
IMAGE = "support-agent-app"
COMPOSE = ["compose", "-f", "deploy/compose.yaml", "-f", "deploy/vm/compose.vm.yaml", "--env-file", ".env"]
WORKFLOW = re.compile(r"@workflow\.defn[^\n]*\n\s*class\s+(\w+)")


class Refused(RuntimeError):
    """The preflight found problems; nothing was changed."""

    def __init__(self, problems):
        super().__init__("; ".join(problems))
        self.problems = problems


class Failed(RuntimeError):
    """A step failed after the preflight; the message says what state the VM was left in."""


def read_env(path):
    values = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
    return values


def model_problems(lock, env):
    """What deploy/vm/models.json (DL-D3) lacks for this deployment's configuration."""
    problems = []
    if env.get("TYPESAFE_API_KEY") and not (lock.get("jev") or {}).get("model"):
        problems.append("jev: TYPESAFE_API_KEY is set but deploy/vm/models.json pins no Jev version")
    jev = (lock.get("jev") or {}).get("model") or ""
    if jev.endswith("latest"):
        problems.append(f"jev: {jev!r} is an alias; Jev must be pinned to an exact version")
    embedding = (lock.get("embedding") or {}).get("model") or ""
    if not embedding or ":" not in embedding or embedding.endswith(":latest"):
        problems.append(f"embedding: {embedding!r} is not pinned to a tag")
    if not isinstance((lock.get("embedding") or {}).get("dimensions"), int):
        problems.append("embedding: dimensions missing")
    for tier, spec in sorted((lock.get("tiers") or {}).items()):
        price = spec.get("usd_per_mtok")
        if not spec.get("model") or not (isinstance(price, list) and len(price) == 2):
            problems.append(f"tier {tier}: needs a model and a [input, output] price per million tokens")
    if float(lock.get("monthly_cap_usd", CAP_USD + 1)) > CAP_USD:
        problems.append(f"models.json monthly_cap_usd must be at most {CAP_USD:g}")
    return problems


def workflow_types(source):
    return set(WORKFLOW.findall(source))


def running_workflow_types(listing):
    """Workflow type names in `temporal workflow list --output json` (a JSON array or one object a line)."""
    listing = listing.strip()
    if not listing:
        return []
    try:
        items = json.loads(listing)
        items = items if isinstance(items, list) else [items]
    except ValueError:
        items = [json.loads(line) for line in listing.splitlines() if line.strip()]
    return [((item.get("type") or {}).get("name")) for item in items if isinstance(item, dict)]


class Release:
    def __init__(self, root=".", state_dir=None, run=None, docker=("docker",), http_status=None,
                 clock=time.time, sleep=time.sleep):
        self.root = Path(root)
        self.state_dir = Path(state_dir or Path.home() / ".support-agent")
        self.run_command, self.docker = run or self._subprocess, list(docker)
        self.http_status, self.clock, self.sleep = http_status or self._http_status, clock, sleep

    # Plumbing ----------------------------------------------------------------------------------------------

    def _subprocess(self, args, env=None):
        merged = dict(os.environ, **(env or {}))
        done = subprocess.run(args, cwd=str(self.root), env=merged, capture_output=True, text=True)
        return done.returncode, done.stdout, done.stderr

    @staticmethod
    def _http_status(url):
        try:
            with urllib.request.urlopen(url, timeout=5) as reply:
                return reply.status
        except Exception:  # noqa: BLE001 - not up yet, or failing: both mean "not healthy"
            return None

    def run(self, args, env=None, check=True):
        code, out, err = self.run_command(list(args), env)
        if check and code != 0:
            raise Failed(f"{' '.join(args[:4])} ... failed ({code}): {(err or out).strip()[-400:]}")
        return code, out

    def git(self, *args, check=True):
        return self.run(["git", *args], check=check)[1].strip()

    def compose(self, *args, image=None, revision=None, check=True):
        env = {"APP_IMAGE": image or "unset", "APP_REVISION": revision or "unknown"}
        return self.run([*self.docker, *COMPOSE, *args], env=env, check=check)

    # State -------------------------------------------------------------------------------------------------

    def history(self):
        path = self.state_dir / "releases.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else []

    def record(self, entry):
        self.state_dir.mkdir(parents=True, exist_ok=True)
        at = datetime.fromtimestamp(self.clock(), timezone.utc).isoformat()
        entries = self.history() + [dict(entry, at=at)]
        (self.state_dir / "releases.json").write_text(json.dumps(entries, indent=1), encoding="utf-8")

    def live(self, skip=0):
        """The most recent live release (skip=1: the one before it)."""
        lives = [e for e in reversed(self.history()) if e["status"] == "live"]
        return lives[skip] if len(lives) > skip else None

    def attestation(self):
        path = self.state_dir / "spend-limit.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None

    def attest_spend_limit(self, usd):
        if not 0 < float(usd) <= CAP_USD:
            raise Refused([f"the Anthropic console limit must be above 0 and at most {CAP_USD:g} USD"])
        self.state_dir.mkdir(parents=True, exist_ok=True)
        entry = {"anthropic_console_limit_usd": float(usd),
                 "at": datetime.fromtimestamp(self.clock(), timezone.utc).isoformat()}
        (self.state_dir / "spend-limit.json").write_text(json.dumps(entry), encoding="utf-8")
        return entry

    # Preflight ---------------------------------------------------------------------------------------------

    def preflight(self, commit, allow_unpinned=(), maintenance=False):
        problems = []
        _, dirty = self.run(["git", "status", "--porcelain", "--untracked-files=no"])   # "XY path" lines
        if dirty.strip():
            problems.append("the checkout has uncommitted changes: " + ", ".join(
                line[3:] for line in dirty.splitlines() if line.strip()))
        env = read_env(self.root / ".env") if (self.root / ".env").exists() else {}
        if not env:
            problems.append(".env is missing")
        cap = env.get("MONTHLY_SPEND_CAP_USD")
        if cap is None or not re.fullmatch(r"\d+(\.\d+)?", cap) or float(cap) > CAP_USD:
            problems.append(f"MONTHLY_SPEND_CAP_USD must be set and at most {CAP_USD:g} (NFR-36)")
        if env and not env.get("PII_TOKEN_KEY"):
            problems.append("PII_TOKEN_KEY is not set: the turn loop cannot tokenize messages (set a long "
                            "random value once and never change it)")
        if env.get("SUPPORT_AGENT_DEV_AUTH") == "1":
            problems.append("SUPPORT_AGENT_DEV_AUTH=1 would let anyone sign in with a header")
        if env.get("ANTHROPIC_API_KEY") and not self.attestation():
            problems.append("ANTHROPIC_API_KEY is set: set the Anthropic console spend limit, then run "
                            "attest-spend-limit 10")
        try:
            lock = json.loads(self.git("show", f"{commit}:deploy/vm/models.json"))
        except (Failed, ValueError) as error:
            lock, problems = {}, problems + [f"deploy/vm/models.json unreadable at {commit[:12]}: {error}"]
        problems += [p for p in model_problems(lock, env) if p.split(":")[0] not in allow_unpinned]
        current = self.live()
        if current and lock:
            try:
                old = json.loads(self.git("show", f"{current['commit']}:deploy/vm/models.json"))
            except (Failed, ValueError):
                old = {}
            if old.get("embedding") != lock.get("embedding") and not maintenance:
                problems.append("the release changes the pinned embedding model: run it with --maintenance "
                                "to rebuild the knowledge index in place (DL-D11)")
        if current:
            problems += self.workflow_problems(current["commit"], commit)
        if self.port_held_outside_compose():
            problems.append("port 8000 is held by a process outside compose (a hand-started server?): "
                            + self.port_held_outside_compose()[-120:])
        _, df = self.run(["df", "--output=avail", "-B1", "/"], check=False)
        free = [int(n) for n in re.findall(r"\d+", df)]
        if free and free[-1] < MIN_FREE_BYTES:
            problems.append(f"only {free[-1] / 1024 ** 3:.1f} GB free on /; a build needs 3 GB")
        return problems

    def port_held_outside_compose(self):
        """The listener on 8000 when it is not Docker's: '' if free or Docker's. Without root, ss cannot name
        the owner of a root process (docker-proxy), so an unnamed listener is checked against docker ps."""
        _, listeners = self.run(["ss", "-ltnpH", "sport = :8000"], check=False)
        line = listeners.strip().splitlines()[0] if listeners.strip() else ""
        if not line or "docker-proxy" in line:
            return ""
        if "users:" not in line:
            _, published = self.run([*self.docker, "ps", "--filter", "publish=8000", "--format",
                                     "{{.Names}}"], check=False)
            if published.strip():
                return ""
        return line

    def workflow_problems(self, old, new):
        changed = [p for p in self.git("diff", "--name-only", old, new, "--", "src").splitlines() if p]
        types = set()
        for path in changed:
            code, source = self.run(["git", "show", f"{old}:{path}"], check=False)
            if code == 0:
                types |= workflow_types(source)
        if not types:
            return []
        code, listing = self.compose("exec", "-T", "temporal", "temporal", "workflow", "list", "--query",
                                     "ExecutionStatus='Running'", "--output", "json", check=False)
        if code != 0:
            return ["cannot list running workflows in Temporal, so it is unknown whether the release breaks "
                    "them"]
        running = [name for name in running_workflow_types(listing) if name in types]
        if not running:
            return []
        counts = {name: running.count(name) for name in sorted(set(running))}
        return ["workflows running on code this release changes: "
                + ", ".join(f"{n} x{c}" for n, c in counts.items())
                + " (let them finish, or move them to the new code with Continue-As-New, DL-D6)"]

    # Steps -------------------------------------------------------------------------------------------------

    def resolve(self, ref):
        self.git("fetch", "--quiet", "origin", check=False)
        return self.git("rev-parse", "--verify", f"{ref}^{{commit}}")

    def healthy(self):
        deadline = self.clock() + HEALTH_SECONDS
        while self.clock() < deadline:
            if self.http_status(HEALTH_URL) == 200:
                return True
            self.sleep(2)
        return False

    def swap(self, image, commit):
        self.compose("up", "-d", "--no-deps", "api", image=image, revision=commit)

    def prune(self):
        keep = {e["image"] for e in [self.live(), self.live(1)] if e}
        _, out = self.run([*self.docker, "image", "ls", IMAGE, "--format", "{{.Repository}}:{{.Tag}}"],
                          check=False)
        for image in out.split():
            if image not in keep:
                self.run([*self.docker, "image", "rm", image], check=False)

    def rollout(self, ref, allow_unpinned=(), maintenance=False, log=print):
        commit = self.resolve(ref)
        problems = self.preflight(commit, allow_unpinned, maintenance)
        if problems:
            raise Refused(problems)
        previous = self.live()
        image = f"{IMAGE}:{commit[:12]}"
        log(f"rollout {commit[:12]} (live: {previous['commit'][:12] if previous else 'none'})")
        self.git("checkout", "--quiet", "--detach", commit)
        try:
            log("building " + image)
            self.run([*self.docker, "build", "-f", "deploy/vm/Dockerfile", "-t", image, "--label",
                      f"org.opencontainers.image.revision={commit}", "."])
            log("migrating (expand only)")
            self.run([*self.docker, "run", "--rm", "--env-file", ".env", image, "python",
                      "deploy/vm/migrate.py"])
            if maintenance:
                log("rebuilding the knowledge index in place (maintenance window)")
                self.compose("run", "--rm", "--no-deps", "api", "python", "deploy/vm/reindex.py", image=image,
                             revision=commit)
        except Failed as error:
            self.restore_checkout(previous)
            self.record({"commit": commit, "image": image, "status": "failed", "reason": str(error)})
            raise Failed(f"{error}; the live release was not touched") from error
        log("swapping the api container")
        self.swap(image, commit)
        if self.healthy():
            self.record({"commit": commit, "image": image, "status": "live",
                         "allow_unpinned": list(allow_unpinned)})
            self.prune()
            log(f"live: {commit[:12]}")
            return commit
        reason = f"{HEALTH_URL} did not answer 200 within {HEALTH_SECONDS} s"
        self.record({"commit": commit, "image": image, "status": "rolled_back", "reason": reason})
        if previous:
            self.swap(previous["image"], previous["commit"])
            self.restore_checkout(previous)
            raise Failed(f"{reason}; put {previous['commit'][:12]} back"
                         + ("" if self.healthy() else ", which is not healthy either"))
        self.compose("stop", "api", image=image, revision=commit, check=False)
        raise Failed(f"{reason}; there was no previous release, so the api is stopped")

    def restore_checkout(self, previous):
        if previous:
            self.git("checkout", "--quiet", "--detach", previous["commit"], check=False)

    def rollback(self, log=print):
        current, previous = self.live(), self.live(1)
        if not previous:
            raise Refused(["there is no earlier live release to go back to"])
        log(f"rollback {current['commit'][:12]} -> {previous['commit'][:12]}")
        self.git("checkout", "--quiet", "--detach", previous["commit"])
        self.swap(previous["image"], previous["commit"])
        if not self.healthy():
            raise Failed(f"{previous['commit'][:12]} did not become healthy")
        self.record({"commit": previous["commit"], "image": previous["image"], "status": "live",
                     "rollback_of": current["commit"]})
        return previous["commit"]


def docker_command():
    """docker as this user if it may, else through sudo (a fresh login has no docker group yet)."""
    try:
        ok = subprocess.run(["docker", "info"], capture_output=True).returncode == 0
    except FileNotFoundError:
        ok = False
    return ("docker",) if ok else ("sudo", "docker")


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    allow = []
    while "--allow-unpinned" in argv:
        at = argv.index("--allow-unpinned")
        allow.append(argv[at + 1])
        del argv[at:at + 2]
    maintenance = "--maintenance" in argv
    argv = [a for a in argv if a != "--maintenance"]
    if not argv or argv[0] not in ("check", "rollout", "rollback", "status", "attest-spend-limit"):
        print(__doc__)
        return 2
    release = Release(docker=docker_command())
    try:
        if argv[0] == "check":
            commit = release.resolve(argv[1] if len(argv) > 1 else "HEAD")
            problems = release.preflight(commit, allow, maintenance)
            print("\n".join(problems) if problems else f"{commit[:12]}: ready to roll out")
            return 1 if problems else 0
        if argv[0] == "rollout":
            release.rollout(argv[1], allow, maintenance)
        elif argv[0] == "rollback":
            release.rollback()
        elif argv[0] == "attest-spend-limit":
            print(release.attest_spend_limit(argv[1]))
        else:
            for entry in release.history()[-10:]:
                print(entry["at"], entry["status"], entry["commit"][:12], entry.get("reason", ""))
        return 0
    except Refused as refused:
        print("refused:\n  " + "\n  ".join(refused.problems))
        return 1
    except Failed as failed:
        print(f"failed: {failed}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
