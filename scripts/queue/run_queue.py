#!/usr/bin/env python3
"""Queue worker: run SLAM methods over the UCU sequences 1..13, starting from one end of the range.

The unit of work is a sequence. For every sequence the worker does, in one pass:
  hf download -> export_tum.py (scripts/data/fetch_ucu.sh) -> every (method x run)
  -> check the output -> scripts/eval.py -> a row in results/summary_<host>.csv
  -> delete the .mcap and the export (only if every wanted run is finished).

Two PCs split the range from opposite ends:
  main PC :  run_queue.sh --from 1  --dir up     (1, 2, 3, ...)
  brother :  run_queue.sh --from 13 --dir down   (13, 12, 11, ...)
Each PC pushes its progress to its own branch runner-<host> (scripts/report/push_status.sh). Before every sequence
the worker fetches the other runner-* branches (read only, into $DATA_ROOT/peers/<host>/, never into results/)
and skips runs that are already in another PC's summary and sequences another PC is working on right now
(its report is younger than --peer-stale-h). Taking a sequence, the worker pushes its state at once and fetches
again; if both PCs took the same sequence, the host whose name sorts first keeps it. Without network the worker
uses the last cache and keeps computing. --set-stop N / --stop-before N stay as a manual emergency boundary.

Methods are registry entries method:mode (scripts/queue/methods.conf); --methods takes 'dpvo' (every mode of a
method), 'orbslam3:mono_inertial' (one mode) or 'all'.

Disk: a sequence is taken only if its download+export fits into the minimum of `df` of DATA_ROOT, the free space of
the Windows drive holding the WSL vhdx (asked via powershell.exe when DATA_ROOT is inside WSL, where `df` shows the
virtual vhdx size) and DATA_BUDGET_GB (env, optional) minus the current size of DATA_ROOT.

Test hooks (environment): QUEUE_FETCH_CMD (fake fetch), QUEUE_PEERS_DIR (directory with runner-<host>/ sub-folders
instead of git), QUEUE_PUSH_CMD (fake push_status.sh), QUEUE_WIN_FREE_GB (<GB> or 'none'). See test_run_queue.py.

Stdlib only; runs under any Python >= 3.8 in WSL. See scripts/queue/methods.conf for the method contract.
"""
from __future__ import annotations

import argparse
import collections
import contextlib
import csv
import json
import os
import re
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import NamedTuple, Optional

try:
    import fcntl
except ImportError:                       # not POSIX: the peer cache is used without a file lock
    fcntl = None

REPO_ROOT = Path(__file__).resolve().parents[2]
N_SEQ = 13
# raw .mcap size per sequence in GB, measured through the public HF API (PLAN_local.md section 4)
MCAP_GB = {1: 6.5, 2: 10.2, 3: 10.1, 4: 12.8, 5: 12.7, 6: 18.8, 7: 18.5,
           8: 18.7, 9: 11.8, 10: 19.5, 11: 10.4, 12: 10.9, 13: 10.7}
EXPORT_SHARE = 0.35        # TUM export is ~0.32 of the .mcap (measured on seq01) + margin
DISK_RESERVE_GB = 5.0
POSE_TOL = 0.98            # a run is "ok" when poses >= POSE_TOL * frames (stride 1 => equal)
DATASET = "ucu"
MAX_CONSECUTIVE_FAILED_SEQS = 2
# Exit code 4 of a run script = "the method ran but gave NO usable trajectory" (never initialised, lost everything, diverged, crashed
# inside the method). That is a RESULT (row in the summary: no ATE, coverage 0, tracking_lost true), not an infrastructure failure.
EXIT_NO_RESULT = 4
RUN_ATTEMPTS = 2           # a run script that fails (other than EXIT_NO_RESULT) is started once more before it counts as failed
# A method that fails (after the retry) on this many DIFFERENT sequences is switched off for the rest of the session, so one broken
# method cannot stop the other methods. The next START tries it again.
QUARANTINE_AFTER_SEQS = 2
DEFAULT_REPORT_REPO = "https://github.com/oToTak/slam-project-methods.git"   # same default as push_status.sh
PEER_GIT_TIMEOUT_S = 120


class Method(NamedTuple):
    name: str
    mode: str
    script: str
    gpu: bool
    runs: Optional[int] = None            # runs per sequence; None = --runs
    pose_tol: Optional[float] = None      # per-method POSE_TOL (methods that honestly lose tracking); None = POSE_TOL

    @property
    def key(self):
        return f"{self.name}:{self.mode}"


class QueueAbort(Exception):
    def __init__(self, code, msg):
        super().__init__(msg)
        self.code = code


class Interrupted(Exception):
    pass


INTERRUPT = threading.Event()
_current = {"proc": None, "reason": None}


# ----------------------------------------------------------------------------- small helpers

def seq_name(n):
    return f"seq{n:02d}"


def now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def clean_host(h):
    return re.sub(r"[^A-Za-z0-9_.-]", "_", h) or "host"


def default_data_root():
    # same rule as scripts/setup/env.sh
    return "/mnt/t/slam-data" if Path("/mnt/t").is_dir() else str(Path.home() / "slam-data")


def fmt_gb(x):
    return f"{x:.1f} GB"


def sequence_order(frm, direction, stop_before=None):
    """Positions visited from ``frm`` in the direction of travel, up to (not including) ``stop_before``."""
    step = 1 if direction == "up" else -1
    out, n = [], frm
    while 1 <= n <= N_SEQ:
        if stop_before is not None and ((step == 1 and n >= stop_before) or (step == -1 and n <= stop_before)):
            break
        out.append(n)
        n += step
    return out


def load_registry(path):
    """{'method:mode': Method} in file order. Line: method | mode | script | gpu|cpu [| runs [| pose_tol]] ('-' = default runs)."""
    reg = {}
    for ln, line in enumerate(Path(path).read_text().splitlines(), 1):
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        parts = [p.strip() for p in line.split("|")]
        ok = len(parts) in (4, 5, 6) and all(parts) and parts[3] in ("gpu", "cpu")             and not any(c in parts[0] + parts[1] for c in ":, ")
        runs, tol = None, None
        if ok and len(parts) >= 5 and parts[4] != "-":
            ok = parts[4].isdigit() and int(parts[4]) >= 1
            runs = int(parts[4]) if ok else None
        if ok and len(parts) == 6:
            try:
                tol = float(parts[5])
            except ValueError:
                ok = False
            ok = ok and 0 < tol <= 1
        if not ok:
            raise SystemExit(f"{path}:{ln}: expected 'method | mode | script | gpu|cpu [| runs [| pose_tol]]', got: {line}")
        m = Method(parts[0], parts[1], parts[2], parts[3] == "gpu", runs, tol)
        if m.key in reg:
            raise SystemExit(f"{path}:{ln}: {m.key} is already in the registry")
        reg[m.key] = m
    return reg


def select_methods(reg, spec):
    """--methods value -> registry keys in registry order. Tokens: 'all', 'method' (all its modes), 'method:mode'."""
    want = set()
    tokens = [t.strip() for t in spec.split(",") if t.strip()]
    for tok in tokens:
        if tok == "all":
            hits = list(reg)
        elif ":" in tok:
            hits = [tok] if tok in reg else []
        else:
            hits = [k for k, m in reg.items() if m.name == tok]
        if not hits:
            raise SystemExit(f"unknown method {tok!r}; registry has: {', '.join(reg)}")
        want.update(hits)
    if not want:
        raise SystemExit(f"no methods selected by {spec!r}; registry has: {', '.join(reg)}")
    return [k for k in reg if k in want]


def host_rank(h):
    """Sort key of host names; the tie-break of a simultaneous claim: the first one keeps the sequence."""
    return (h.casefold(), h)


def pid_alive(pid):
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except (PermissionError, ValueError, OverflowError):
        return True
    return True


def count_data_lines(path):
    n = 0
    try:
        with open(path, errors="replace") as f:
            for line in f:
                s = line.strip()
                if s and not s.startswith("#"):
                    n += 1
    except OSError:
        return 0
    return n


class Log:
    def __init__(self):
        self.fh = None

    def attach(self, fh):
        self.fh = fh

    def __call__(self, msg):
        line = f"{time.strftime('%H:%M:%S')} {msg}"
        try:
            print(line, flush=True)
        except OSError:                   # terminal closed (SIGHUP): keep logging to the file
            pass
        if self.fh:
            self.fh.write(line + "\n")


# ----------------------------------------------------------------------------- context and paths

class Ctx:
    def __init__(self, args):
        self.args = args
        self.data_root = Path(os.environ.get("DATA_ROOT") or default_data_root())
        self.results_root = Path(args.results_root)
        self.host = clean_host(os.environ.get("QUEUE_HOST") or socket.gethostname())
        self.logs = self.data_root / "logs"
        self.summary = self.results_root / f"summary_{self.host}.csv"
        self.state_file = self.logs / f"queue_{self.host}.state.json"
        self.stop_file = self.logs / f"queue_{self.host}.stop"
        self.registry = load_registry(args.registry)
        self.methods = select_methods(self.registry, args.methods)      # registry keys 'method:mode'
        self.state = {}
        self.peers_root = self.data_root / "peers"
        self.use_peers = not getattr(args, "no_peers", False)
        self.stale_h = getattr(args, "peer_stale_h", 3.0)
        self.peers = []               # loaded from the cache by refresh_peers()
        self.peers_loaded_at = 0.0
        self.reporter = None
        self.fail_seqs = collections.defaultdict(set)    # method key -> sequences where it failed (after the retry) in this session
        self.quarantined = []                            # method keys switched off for the rest of the session
        self.open_seqs = []                              # sequences left on disk by a failure in this session

    def runs_for(self, key):
        return self.registry[key].runs or self.args.runs

    @property
    def max_runs(self):
        return max((self.runs_for(k) for k in self.methods), default=0)

    def label(self, key):
        """'dpvo' when the method has one mode in the registry, else 'orbslam3:mono_inertial'."""
        name = self.registry[key].name
        return name if sum(m.name == name for m in self.registry.values()) == 1 else key

    def raw_dir(self, n):
        return self.data_root / "ucu" / f"sequence-{n}"

    def export_dir(self, n):
        return self.data_root / "ucu_tum" / seq_name(n)

    def res_dir(self, key, n):
        m = self.registry[key]
        return self.results_root / m.name / m.mode / DATASET / seq_name(n)


def read_summary_rows(path, host, owners):
    """Add (method, mode, dataset, seq, run) of every row of one summary csv to owners {key: {hosts}}."""
    try:
        with open(path, newline="") as f:
            for row in csv.DictReader(f):
                k = (row.get("method"), row.get("mode"), row.get("dataset"), row.get("seq"), row.get("run"))
                owners.setdefault(k, set()).add(host)
    except OSError:
        pass


def summary_owners(ctx, peers=True):
    """{(method, mode, dataset, seq, run): {hosts}} from results/summary*.csv of this PC (sidecar tables excluded)
    and, with peers=True, from the cached summaries of the other PCs."""
    owners = {}
    for p in sorted(ctx.results_root.glob("summary*.csv")):
        if p.name.startswith("summary_extra"):
            continue
        host = p.stem[len("summary_"):] if p.stem.startswith("summary_") else ctx.host   # legacy summary.csv: this PC
        read_summary_rows(p, host, owners)
    if peers:
        for peer in ctx.peers:
            for k in peer["keys"]:
                owners.setdefault(k, set()).add(peer["host"])
    return owners


def summary_keys(ctx, peers=True):
    return set(summary_owners(ctx, peers))


def run_key(ctx, key, n, r):
    m = ctx.registry[key]
    return (m.name, m.mode, DATASET, seq_name(n), str(r))


def run_state(ctx, key, n, r, keys=None):
    """'done' | 'bad' | None (pending). A summary row (here or on another PC) counts as done: results are append-only."""
    d = ctx.res_dir(key, n)
    if (d / f".done_run{r}").exists():
        return "done"
    if (d / f".bad_run{r}").exists():
        return "bad"
    keys = summary_owners(ctx) if keys is None else keys
    if run_key(ctx, key, n, r) in keys:
        return "done"
    return None


def pending_runs(ctx, n, keys=None):
    keys = summary_owners(ctx) if keys is None else keys
    return [(r, k) for r in range(1, ctx.max_runs + 1) for k in ctx.methods
            if r <= ctx.runs_for(k) and run_state(ctx, k, n, r, keys) is None]


def seq_complete(ctx, n, keys=None):
    """Every wanted run is finished. A run flagged 'bad' (a trajectory with few poses: the method lost tracking, which on hard
    sequences is a RESULT, its row is in the summary) counts as finished: it must not keep the 15-20 GB export on the disk
    or stop the worker. Only runs that failed to produce a result (no marker) keep a sequence open."""
    keys = summary_owners(ctx) if keys is None else keys
    return all(run_state(ctx, k, n, r, keys) in ("done", "bad") for k in ctx.methods for r in range(1, ctx.runs_for(k) + 1))


def cell_state(ctx, key, n, owners):
    """(done, wanted, bad, hosts that did the done runs) of one method on one sequence."""
    want = ctx.runs_for(key)
    done, bad, hosts = 0, 0, set()
    for r in range(1, want + 1):
        st = run_state(ctx, key, n, r, owners)
        if st == "done":
            done += 1
            hosts |= owners.get(run_key(ctx, key, n, r)) or {ctx.host}     # marker without a row: this PC
        elif st == "bad":
            bad += 1
    return done, want, bad, hosts


# ----------------------------------------------------------------------------- peers: the other PCs' runner-<host> branches

@contextlib.contextmanager
def peers_lock(ctx, exclusive):
    """flock on $DATA_ROOT/peers/.lock: the worker and a --status started by push_status.sh may refresh at once."""
    ctx.peers_root.mkdir(parents=True, exist_ok=True)
    if fcntl is None:
        yield
        return
    with open(ctx.peers_root / ".lock", "a") as f:
        fcntl.flock(f, fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def peer_host_ok(h):
    return bool(re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]*", h))


def summary_name_ok(name):
    return bool(re.fullmatch(r"summary[A-Za-z0-9_.-]*\.csv", name)) and not name.startswith("summary_extra")


def snapshot_from_dir(src, me):
    """Test source: <src>/runner-<host>/{results/summary_<host>.csv, queue_state.json, .commit_time (epoch s)}."""
    src = Path(src)
    if not src.is_dir():
        raise RuntimeError(f"нема каталогу {src}")
    out = {}
    for b in sorted(src.iterdir()):
        host = b.name[len("runner-"):]
        if not b.is_dir() or not b.name.startswith("runner-") or host == me or not peer_host_ok(host):
            continue
        files = {p.name: p.read_bytes() for p in sorted((b / "results").glob("summary*.csv")) if summary_name_ok(p.name)}
        if (b / "queue_state.json").is_file():
            files["queue_state.json"] = (b / "queue_state.json").read_bytes()
        ct = b / ".commit_time"
        commit = int(float(ct.read_text().strip())) if ct.is_file() else \
            int(max((p.stat().st_mtime for p in b.rglob("*") if p.is_file()), default=0))
        out[host] = {"files": files, "commit_time": commit}
    return out


def report_repo():
    """The repository with the runner-* branches: same lookup as push_status.sh (REPORT_REPO, then SLAM_GIT_URL)."""
    return os.environ.get("REPORT_REPO") or os.environ.get("SLAM_GIT_URL") or DEFAULT_REPORT_REPO


def conf_dir():
    """Settings of this PC (github_token); SLAM_CONF_DIR from scripts/setup/env.sh, default ~/.config/slam."""
    return Path(os.environ.get("SLAM_CONF_DIR") or Path.home() / ".config" / "slam")


def git_env():
    """Same token mechanism as push_status.sh (github_token + askpass); no token: anonymous read."""
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
    if report_repo().startswith("https://"):
        cfg = conf_dir()
        tok = cfg / "github_token"
        if tok.is_file() and tok.stat().st_size > 0:
            ask = cfg / "git-askpass-peers.sh"      # own file: push_status.sh rewrites git-askpass.sh concurrently
            text = '#!/bin/sh\ncase "$1" in *sername*) echo x-access-token ;; *) cat "%s" ;; esac\n' % tok
            if not ask.is_file() or ask.read_text() != text:
                ask.write_text(text)
                ask.chmod(0o700)
            env["GIT_ASKPASS"] = str(ask)
    return env


def git_out(args, env, binary=False):
    r = subprocess.run(["git", "-c", "credential.helper=", *args], env=env, capture_output=True,
                       timeout=PEER_GIT_TIMEOUT_S, start_new_session=True)
    if r.returncode != 0:
        err = (r.stderr.decode(errors="replace").strip().splitlines() or [f"код {r.returncode}"])[-1]
        raise RuntimeError(f"git {args[2] if args[0] == '--git-dir' else args[0]}: {err}")
    return r.stdout if binary else r.stdout.decode(errors="replace")


def snapshot_from_git(ctx):
    """Fetch refs/heads/runner-* (depth 1) into a private bare repo $DATA_ROOT/peers/.repo.git and read the other
    hosts' files with ``git show`` (no checkout, the worker's own working tree is never touched)."""
    env = git_env()
    repo = ctx.peers_root / ".repo.git"
    if not (repo / "HEAD").exists():
        git_out(["init", "-q", "--bare", str(repo)], env)
    g = ["--git-dir", str(repo)]
    git_out(g + ["fetch", "-q", "--prune", "--no-tags", "--depth=1", report_repo(),
                 "+refs/heads/runner-*:refs/remotes/origin/runner-*"], env)
    out = {}
    refs = git_out(g + ["for-each-ref", "--format=%(refname:strip=3) %(committerdate:unix)", "refs/remotes/origin/"], env)
    for line in refs.splitlines():
        branch, _, ts = line.strip().partition(" ")
        host = branch[len("runner-"):]
        if not branch.startswith("runner-") or host == ctx.host or not peer_host_ok(host):
            continue
        ref = f"refs/remotes/origin/{branch}"
        files = {}
        for where, wanted in ((["results/"], summary_name_ok), ([], lambda nm: nm == "queue_state.json")):
            for ent in git_out(g + ["ls-tree", ref, *where], env).splitlines():
                meta, _, p = ent.partition("\t")
                name = p.rsplit("/", 1)[-1]
                if meta.split()[1:2] == ["blob"] and wanted(name):
                    files[name] = git_out(g + ["show", f"{ref}:{p}"], env, binary=True)
        out[host] = {"files": files, "commit_time": int(ts) if ts.isdigit() else 0}
    return out


def write_peer_cache(ctx, snap):
    """Replace $DATA_ROOT/peers/<host>/ with the snapshot (call under the exclusive lock)."""
    root, now = ctx.peers_root, int(time.time())
    for host, s in snap.items():
        tmp = root / f".{host}.new"
        shutil.rmtree(tmp, ignore_errors=True)
        tmp.mkdir(parents=True)
        for name, data in s["files"].items():
            (tmp / name).write_bytes(data)
        (tmp / "meta.json").write_text(json.dumps({"host": host, "commit_time": s["commit_time"], "fetched_at": now}))
        shutil.rmtree(root / host, ignore_errors=True)
        tmp.rename(root / host)
    for d in root.iterdir():                         # branch deleted on the remote: forget that host
        if d.is_dir() and not d.name.startswith(".") and d.name not in snap:
            shutil.rmtree(d, ignore_errors=True)


def load_peers(ctx):
    peers = []
    if not ctx.peers_root.is_dir():
        return peers
    try:
        with peers_lock(ctx, False):
            for d in sorted(ctx.peers_root.iterdir()):
                if not d.is_dir() or d.name.startswith(".") or d.name == ctx.host:
                    continue
                meta = read_json(d / "meta.json")
                owners = {}
                for p in sorted(d.glob("summary*.csv")):
                    if summary_name_ok(p.name):
                        read_summary_rows(p, d.name, owners)
                peers.append({"host": d.name, "keys": set(owners), "state": read_json(d / "queue_state.json"),
                              "commit_time": meta.get("commit_time") or 0, "fetched_at": meta.get("fetched_at")})
    except OSError:
        pass
    return peers


def refresh_peers(ctx, log, max_age_s=0.0):
    """Fetch the other PCs' branches into the cache and load it into ctx.peers. Never raises and never blocks the
    computation for long: on any failure it logs a warning and keeps the last cache."""
    if not ctx.use_peers:
        ctx.peers = []
        return
    if max_age_s and time.time() - ctx.peers_loaded_at < max_age_s:
        return
    src = os.environ.get("QUEUE_PEERS_DIR")
    fetch_file = ctx.peers_root / ".last_fetch.json"
    try:
        with peers_lock(ctx, True):
            snap = snapshot_from_dir(src, ctx.host) if src else snapshot_from_git(ctx)
            write_peer_cache(ctx, snap)
            fetch_file.write_text(json.dumps({"ok_at": int(time.time()), "error": None}))
    except Exception as e:                       # network, token, git, disk: never stop the worker because of peers
        err = str(e) or e.__class__.__name__
        log(f"УВАГА: не вдалось оновити стан інших ПК ({err}); працюю з останнім кешем {ctx.peers_root}")
        try:
            prev = read_json(fetch_file)
            fetch_file.write_text(json.dumps({"ok_at": prev.get("ok_at"), "error": err, "error_at": int(time.time())}))
        except OSError:
            pass
    ctx.peers = load_peers(ctx)
    ctx.peers_loaded_at = time.time()


def peer_age_h(peer, now=None):
    ct = peer.get("commit_time") or 0
    return None if not ct else ((now or time.time()) - ct) / 3600


def peer_fresh(ctx, peer):
    age = peer_age_h(peer)
    return age is not None and age < ctx.stale_h


def peer_claims(ctx, n):
    """Hosts whose fresh report (younger than --peer-stale-h) says they are working on sequence n."""
    return sorted((p["host"] for p in ctx.peers if p["state"].get("seq") == seq_name(n) and peer_fresh(ctx, p)),
                  key=host_rank)


def fmt_age(h):
    if h is None:
        return "невідомо коли"
    if h < 1:
        return f"{max(h * 60, 0):.0f} хв тому"
    return f"{h:.1f} год тому"


# ----------------------------------------------------------------------------- state / boundary files

def read_json(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return {}


def set_state(ctx, **kw):
    ctx.state.update(kw)
    ctx.logs.mkdir(parents=True, exist_ok=True)
    tmp = ctx.state_file.with_suffix(".tmp")
    tmp.write_text(json.dumps(ctx.state, ensure_ascii=False))
    tmp.replace(ctx.state_file)


def clear_state(ctx):
    try:
        if read_json(ctx.state_file).get("pid") == os.getpid():
            ctx.state_file.unlink()
    except OSError:
        pass


def read_stop(ctx):
    try:
        return int(ctx.stop_file.read_text().strip())
    except (OSError, ValueError):
        return None


def write_stop(ctx, value):
    ctx.logs.mkdir(parents=True, exist_ok=True)
    if value is None:
        try:
            ctx.stop_file.unlink()
        except FileNotFoundError:
            pass
    else:
        ctx.stop_file.write_text(f"{value}\n")


def allowed(ctx, n):
    """May position n still be taken? --only ignores the boundary; the boundary file is re-read every time."""
    if ctx.args.only:
        return True
    stop = read_stop(ctx)
    if stop is None:
        return True
    return n < stop if ctx.args.dir == "up" else n > stop


# ----------------------------------------------------------------------------- subprocess with process group

def _kill_group(proc, sig):
    try:
        os.killpg(proc.pid, sig)          # start_new_session => pgid == pid
    except (ProcessLookupError, PermissionError):
        pass


def _terminate(proc, reason):
    _current["reason"] = reason
    _kill_group(proc, signal.SIGTERM)
    t = threading.Timer(20, _kill_group, (proc, signal.SIGKILL))
    t.daemon = True
    t.start()


def _on_signal(signum, frame):
    proc = _current["proc"]
    if INTERRUPT.is_set():                # second press: do not wait for a graceful end
        if proc is not None:
            _kill_group(proc, signal.SIGKILL)
        os._exit(130)
    INTERRUPT.set()
    try:
        print("\n[!] Перервано (Ctrl-C або закрито вікно): завершую поточний прогін, готове лишається. Ще раз Ctrl-C — негайний вихід.", flush=True)
    except OSError:
        pass
    if proc is not None and proc.poll() is None:
        _terminate(proc, "interrupt")


def run_streamed(cmd, *, env, cwd, logs, timeout_s):
    """Run cmd in its own process group; copy its output to every file in ``logs``.
    Returns (returncode, reason, last_lines); reason is None | 'timeout' | 'interrupt'."""
    proc = subprocess.Popen(cmd, cwd=str(cwd), env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, errors="replace", bufsize=1, start_new_session=True)
    _current["proc"], _current["reason"] = proc, None
    if INTERRUPT.is_set():                # signal arrived between Popen calls
        _terminate(proc, "interrupt")
    timer = threading.Timer(timeout_s, _terminate, (proc, "timeout"))
    timer.daemon = True
    timer.start()
    tail = collections.deque(maxlen=15)
    try:
        for line in proc.stdout:
            for fh in logs:
                fh.write(line)
            tail.append(line.rstrip("\n"))
        rc = proc.wait()
    finally:
        timer.cancel()
        reason = _current["reason"]
        _current["proc"], _current["reason"] = None, None
    return rc, reason, list(tail)


# ----------------------------------------------------------------------------- disk and data

# Files a method needs in the exported sequence besides rgb.txt. An export made before such a file existed is completed by
# scripts/data/fetch_ucu.sh (it adds odom.txt in place), so the worker fetches again when a method of the pending runs lacks its file.
EXPORT_NEEDS = {"wheel_odom": "odom.txt"}


def export_ready(ctx, n, keys=None):
    """True if the exported sequence n has rgb.txt and every extra file the methods in `keys` (registry keys) need."""
    d = ctx.export_dir(n)
    if not (d / "rgb.txt").exists():
        return False
    names = {ctx.registry[k].name for k in (ctx.methods if keys is None else keys)}
    return all((d / EXPORT_NEEDS[m]).exists() for m in names if m in EXPORT_NEEDS)


def disk_need_gb(ctx, n, keys=None):
    """Free space needed to bring sequence n to the exported state (0 if the export is already complete for `keys`)."""
    if export_ready(ctx, n, keys):
        return 0.0
    size = MCAP_GB[n]
    have_mcap = any(ctx.raw_dir(n).glob("*.mcap")) if ctx.raw_dir(n).is_dir() else False
    if (ctx.export_dir(n) / "rgb.txt").exists():      # only the missing extra file: the .mcap again, no new export
        return 0.0 if have_mcap else size + DISK_RESERVE_GB
    return (0.0 if have_mcap else size) + EXPORT_SHARE * size + DISK_RESERVE_GB


def free_gb(path):
    p = Path(path)
    while not p.exists() and p != p.parent:
        p = p.parent
    return shutil.disk_usage(p).free / 1e9


# In WSL `df` of the ext4 root shows the virtual size of ext4.vhdx (e.g. 993 GB free), not the free space of the
# Windows drive that holds the vhdx. Ask Windows for that drive (registry Lxss -> BasePath of this distro).
WIN_FREE_PS = (r"$k = Get-ChildItem 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Lxss' | Get-ItemProperty | "
               r"Where-Object { $_.DistributionName -eq '%s' } | Select-Object -First 1; "
               r"if (-not $k) { exit 3 }; $p = $k.BasePath -replace '^\\\\\?\\', ''; "
               r"$r = [System.IO.Path]::GetPathRoot($p); $d = New-Object System.IO.DriveInfo($r); "
               r"Write-Output ($r + '|' + $d.AvailableFreeSpace)")


def on_windows_drive(path):
    """True for a drvfs mount like /mnt/t/...: there `df` already reports the real Windows drive."""
    parts = Path(path).resolve().parts
    return len(parts) >= 3 and parts[1] == "mnt" and len(parts[2]) == 1


def windows_free_gb():
    """(free GB, drive) of the Windows drive holding this WSL distro's vhdx, or (None, reason).
    Test hook: QUEUE_WIN_FREE_GB=<GB> or 'none' (= could not be determined)."""
    hook = os.environ.get("QUEUE_WIN_FREE_GB")
    if hook is not None:
        try:
            return float(hook), "тест"
        except ValueError:
            return None, "тестова заглушка: не визначено"
    distro = os.environ.get("WSL_DISTRO_NAME", "")
    if not re.fullmatch(r"[A-Za-z0-9._-]+", distro):
        return None, "не WSL (нема WSL_DISTRO_NAME)"
    try:
        r = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", WIN_FREE_PS % distro],
                           capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired) as e:
        return None, f"powershell.exe: {e}"
    drive, _, free = (r.stdout.strip().splitlines() or [""])[-1].partition("|")
    if r.returncode != 0 or not free.strip().isdigit():
        return None, f"powershell.exe не дав відповіді (код {r.returncode})"
    return int(free) / 1e9, drive.strip()


def dir_size_gb(path):
    """Apparent size of everything under path (files only, symlinks not followed)."""
    total = 0
    for root, dirs, files in os.walk(path):
        for f in files:
            try:
                st = os.lstat(os.path.join(root, f))
            except OSError:
                continue
            if not os.path.islink(os.path.join(root, f)):
                total += st.st_size
    return total / 1e9


def data_budget_gb():
    v = os.environ.get("DATA_BUDGET_GB", "").strip()
    if not v:
        return None
    try:
        b = float(v)
    except ValueError:
        raise QueueAbort(1, f"DATA_BUDGET_GB={v!r}: має бути число гігабайтів")
    if b <= 0:
        raise QueueAbort(1, f"DATA_BUDGET_GB={v!r}: має бути більше нуля")
    return b


def disk_room(ctx):
    """How much the worker may still put under DATA_ROOT, and why: min of
    `df`, the Windows drive under the WSL vhdx (only when DATA_ROOT is inside WSL), DATA_BUDGET_GB - size of DATA_ROOT.
    Returns (avail_gb, explanation, warnings)."""
    df = free_gb(ctx.data_root)
    parts, warns = [(df, f"df {fmt_gb(df)}")], []
    if on_windows_drive(ctx.data_root):
        parts[0] = (df, f"df {fmt_gb(df)} (диск Windows через /mnt, число правдиве)")
    else:
        win, where = windows_free_gb()
        if win is None:
            warns.append(f"не вдалось дізнатися вільне місце диска Windows під WSL ({where}); "
                         "df у WSL показує віртуальний розмір vhdx і може брехати")
        else:
            parts.append((win, f"диск Windows {where} під vhdx {fmt_gb(win)}"))
    budget = data_budget_gb()
    if budget is not None:
        used = dir_size_gb(ctx.data_root) if ctx.data_root.is_dir() else 0.0
        parts.append((budget - used, f"бюджет DATA_BUDGET_GB {budget:g} GB − зайнято {fmt_gb(used)} = {fmt_gb(budget - used)}"))
    avail = max(min(v for v, _ in parts), 0.0)
    why = "; ".join(t for _, t in parts)
    return avail, (f"доступно {fmt_gb(avail)} = мінімум із: {why}" if len(parts) > 1 else f"доступно {why}"), warns


def cleanup_seq(ctx, log, n):
    """Delete the .mcap and the export of one sequence. Refuses anything outside DATA_ROOT or with an unexpected name."""
    root = ctx.data_root.resolve()
    for p in (ctx.raw_dir(n), ctx.export_dir(n), Path(str(ctx.export_dir(n)) + ".partial")):
        if not p.exists():
            continue
        if root not in p.resolve().parents or not re.fullmatch(r"sequence-\d+|seq\d\d(\.partial)?", p.name):
            log(f"  не видаляю {p}: незвична назва/шлях")
            continue
        shutil.rmtree(p)
        log(f"  видалено {p}")


# ----------------------------------------------------------------------------- one run

def child_env(ctx, m=None):
    env = dict(os.environ)
    env.update(NO_EVAL="1", QUEUE_HOST=ctx.host, RESULTS_ROOT=str(ctx.results_root),
               SUMMARY_FILE=str(ctx.summary), DATA_ROOT=str(ctx.data_root))
    if m is not None:                     # one script may serve several modes of a method
        env.update(QUEUE_METHOD=m.name, QUEUE_MODE=m.mode)
    return env


def eval_command(ctx, key, n, r, run_log):
    m = ctx.registry[key]
    py = Path(os.environ.get("SLAM_VENV", str(Path.home() / "venvs" / "slam"))) / "bin" / "python"
    py = str(py) if py.exists() else sys.executable
    return [py, str(ctx.args.eval_script), "--method", m.name, "--mode", m.mode, "--dataset", DATASET,
            "--seq", seq_name(n), "--run", str(r), "--summary", str(ctx.summary),
            "--results-root", str(ctx.results_root), "--log", str(run_log)]


def write_marker(path, **fields):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(f"{k}={v}\n" for k, v in fields.items()))


def do_run(ctx, log, seq_fh, n, r, key):
    """One (method:mode, run) on sequence n. Returns 'done' | 'bad' | 'failed'. Raises Interrupted."""
    m, seq = ctx.registry[key], seq_name(n)
    d = ctx.res_dir(key, n)
    traj = d / f"traj_run{r}.txt"
    run_log = ctx.logs / f"queue_{ctx.host}_{seq}_{m.name}_{m.mode}_run{r}.log"
    set_state(ctx, seq=seq, method=key, run=r, since=now_iso())
    t0 = time.time()
    log(f"-- {seq} {key} run{r}/{ctx.runs_for(key)}: старт")
    with open(run_log, "a", buffering=1, encoding="utf-8") as rl:
        logs = [seq_fh, rl]
        if traj.exists():
            log(f"   траєкторія {traj.name} уже є, але рядка в summary нема: роблю лише оцінку")
        else:
            cmd = ["bash", str(REPO_ROOT / m.script), DATASET, seq, "--run", str(r)]
            for attempt in range(1, RUN_ATTEMPTS + 1):
                try:
                    rc, reason, tail = run_streamed(cmd, env=child_env(ctx, m), cwd=REPO_ROOT, logs=logs,
                                                    timeout_s=ctx.args.timeout_h * 3600)
                except OSError as e:
                    log(f"   НЕ ВДАЛОСЬ запустити {cmd[1]}: {e}")
                    return "failed"
                if reason == "interrupt":                 # we killed it: drop the unfinished run, keep the finished ones
                    if traj.exists():                     # half-written file of the killed run
                        traj.unlink()
                    raise Interrupted()
                if reason == "timeout":
                    log(f"   ТАЙМАУТ ({ctx.args.timeout_h} год), прогін перервано; незавершене лишається без маркера")
                    return "failed"
                if rc == 0:
                    break
                if rc == EXIT_NO_RESULT:                  # a result of the method, not a failure: the evaluation below records it
                    log(f"   {m.script}: метод не дав придатної траєкторії (код {EXIT_NO_RESULT}: не ініціалізувався / втратив трекінг / "
                        "розійшовся / впав усередині методу). Це результат прогону: рядок піде в summary з tracking_lost=true")
                    break
                log(f"   ПОМИЛКА: {m.script} завершився з кодом {rc}. Кінець логу:")
                for ln in tail:
                    log(f"   | {ln}")
                if attempt < RUN_ATTEMPTS and not traj.exists():
                    log(f"   пробую ще раз ({attempt + 1}/{RUN_ATTEMPTS})")
                    continue
                return "failed"
        rc, reason, tail = run_streamed(eval_command(ctx, key, n, r, run_log), env=child_env(ctx, m), cwd=REPO_ROOT,
                                        logs=logs, timeout_s=1800)
        if reason == "interrupt":
            raise Interrupted()                       # trajectory is complete: next start does the evaluation only
        if rc != 0:
            log(f"   ПОМИЛКА оцінки (eval.py, код {rc}). Кінець логу:")
            for ln in tail:
                log(f"   | {ln}")
            return "failed"
    if run_key(ctx, key, n, r) not in summary_keys(ctx, peers=False):
        log(f"   ПОМИЛКА: eval.py не додав рядок у {ctx.summary.name}")
        return "failed"
    frames = count_data_lines(ctx.export_dir(n) / "rgb.txt")
    poses = count_data_lines(traj)
    wall = time.time() - t0
    tol = ctx.registry[key].pose_tol or POSE_TOL
    ok = frames > 0 and poses >= tol * frames
    write_marker(d / (f".done_run{r}" if ok else f".bad_run{r}"), host=ctx.host, finished=now_iso(),
                 wall_s=f"{wall:.0f}", poses=poses, frames=frames)
    if ok:
        log(f"   готово за {wall / 60:.1f} хв: поз {poses}/{frames}; рядок додано в {ctx.summary.name}")
        return "done"
    log(f"   ПЕРЕВІР: поз {poses} із {frames} кадрів (поріг {tol:.0%}); рядок у {ctx.summary.name} є, "
        f"маркер .bad_run{r} поставлено (це результат методу, черга йде далі)")
    return "bad"


# ----------------------------------------------------------------------------- one sequence

def fetch_seq(ctx, log, fh, n):
    cmd = (shlex.split(os.environ["QUEUE_FETCH_CMD"]) if os.environ.get("QUEUE_FETCH_CMD")
           else ["bash", str(REPO_ROOT / "scripts" / "data" / "fetch_ucu.sh")]) + [str(n), "--keep-mcap"]
    log(f"   завантаження й експорт sequence-{n} (час піде у fetch_timings.csv)")
    try:
        rc, reason, tail = run_streamed(cmd, env=child_env(ctx), cwd=REPO_ROOT, logs=[fh],
                                        timeout_s=ctx.args.fetch_timeout_h * 3600)
    except OSError as e:
        raise QueueAbort(3, f"не вдалось запустити завантаження: {e}")
    if reason == "interrupt":
        raise Interrupted()
    if rc != 0 or reason == "timeout" or not (ctx.export_dir(n) / "rgb.txt").exists():
        for ln in tail:
            log(f"   | {ln}")
        raise QueueAbort(3, f"завантаження/експорт sequence-{n} не вдалось (код {rc}, {reason or 'без таймауту'}). "
                            "Перевір інтернет і вхід у Hugging Face: ~/.local/bin/hf auth whoami. Повторний запуск продовжить.")


def done_elsewhere(ctx, n, owners):
    """Other hosts that have at least one wanted run of sequence n in their summary."""
    hosts = set()
    for k in ctx.methods:
        for r in range(1, ctx.runs_for(k) + 1):
            hosts |= owners.get(run_key(ctx, k, n, r), set())
    hosts.discard(ctx.host)
    return sorted(hosts, key=host_rank)


def claim_seq(ctx, log, n):
    """Publish at once that this PC takes sequence n, then look again: if another PC took it at the same moment,
    the host whose name sorts first keeps it (both PCs reach the same verdict). True = the sequence is ours."""
    seq = seq_name(n)
    set_state(ctx, seq=seq, method=None, run=None, since=now_iso())
    if not ctx.use_peers or ctx.reporter is None:
        return True                       # nothing published: the other PC sees only finished runs
    ctx.reporter.push(force=True)
    refresh_peers(ctx, log)
    rivals = [h for h in peer_claims(ctx, n) if host_rank(h) < host_rank(ctx.host)]
    if rivals:
        log(f"{seq}: {', '.join(rivals)} узяв його одночасно з нами; за правилом (перша назва хоста за абеткою) "
            "сиквенс лишається йому, пропускаю")
        set_state(ctx, seq=None, method=None, run=None, since=now_iso())
        return False
    return True


def note_method_failure(ctx, log, key, n):
    """A run of `key` failed on sequence n (after the retry). The second sequence on which the same method fails switches it off
    for the rest of the session: the other methods go on, and the data of sequences held open only by this method is released."""
    ctx.fail_seqs[key].add(n)
    if len(ctx.fail_seqs[key]) < QUARANTINE_AFTER_SEQS or key not in ctx.methods:
        return
    ctx.methods.remove(key)
    ctx.quarantined.append(key)
    log(f"!! МЕТОД {key} ВИМКНЕНО до кінця цієї сесії: прогін завершився помилкою на сиквенсах "
        f"{', '.join(seq_name(s) for s in sorted(ctx.fail_seqs[key]))} (навіть після повтору). Інші методи йдуть далі. "
        f"Його недораховані прогони лишаються в черзі: наступний START спробує їх знову (дані тих сиквенсів тоді качаються наново).")
    for s in list(ctx.open_seqs):
        if ctx.methods and seq_complete(ctx, s):
            log(f"{seq_name(s)}: без {key} усе готове")
            if ctx.args.keep_data:
                log("  --keep-data: .mcap й експорт лишаю")
            else:
                cleanup_seq(ctx, log, s)
            ctx.open_seqs.remove(s)


def process_seq(ctx, log, n):
    """Returns 'skipped' | 'busy' | 'done' | 'failed' | 'stopped'."""
    seq = seq_name(n)
    owners = summary_owners(ctx)
    todo = pending_runs(ctx, n, owners)
    if not todo:
        who = done_elsewhere(ctx, n, owners)
        log(f"{seq}: усе вже пораховано{' (є в ' + ', '.join(who) + ')' if who else ''}, пропускаю")
        return "skipped"
    busy = peer_claims(ctx, n)
    if busy:
        ages = {p["host"]: peer_age_h(p) for p in ctx.peers}
        log(f"{seq}: зараз рахує {', '.join(f'{h} (звіт {fmt_age(ages.get(h))})' for h in busy)}, пропускаю")
        return "busy"
    if not claim_seq(ctx, log, n):
        return "busy"
    todo = pending_runs(ctx, n)           # the other PC may have finished something in the meantime
    if not todo:
        log(f"{seq}: поки захоплював, усе вже пораховано іншим ПК, пропускаю")
        set_state(ctx, seq=None, method=None, run=None, since=now_iso())
        return "skipped"
    ctx.logs.mkdir(parents=True, exist_ok=True)
    seq_log = ctx.logs / f"queue_{ctx.host}_{seq}_{time.strftime('%Y%m%d_%H%M%S')}.log"
    with open(seq_log, "a", buffering=1, encoding="utf-8") as fh:
        log.attach(fh)
        try:
            log(f"== {seq}: прогонів до виконання {len(todo)}; лог {seq_log}")
            todo_keys = {k for _, k in todo}
            if not export_ready(ctx, n, todo_keys):
                need = disk_need_gb(ctx, n, todo_keys)
                if not ctx.args.no_disk_check:
                    avail, why, warns = disk_room(ctx)
                    for w in warns:
                        log(f"   УВАГА: {w}")
                    if avail < need:
                        raise QueueAbort(4, f"мало місця для даних ({ctx.data_root}): для {seq} треба ≈ {fmt_gb(need)}, {why}. "
                                            "Нічого не видаляю: звільни місце (або збільш DATA_BUDGET_GB) й запусти ту саму команду.")
                    log(f"   місце: треба ≈ {fmt_gb(need)}, {why}")
                fetch_seq(ctx, log, fh, n)
            failed_here = set()               # methods that failed on this sequence: their remaining runs would fail the same way
            for r, method in todo:
                if INTERRUPT.is_set():
                    raise Interrupted()
                if not allowed(ctx, n):
                    log(f"{seq}: межа змінена (stop-before {read_stop(ctx)}), зупиняюсь; дані лишаю до наступного запуску")
                    return "stopped"
                if method not in ctx.methods or method in failed_here:
                    continue
                if do_run(ctx, log, fh, n, r, method) == "failed":
                    failed_here.add(method)
                    note_method_failure(ctx, log, method, n)
            if not ctx.methods:
                return "failed"
            if seq_complete(ctx, n):
                flagged = sum(1 for k in ctx.methods for r in range(1, ctx.runs_for(k) + 1) if run_state(ctx, k, n, r) == "bad")
                log(f"{seq}: усі прогони готові" + (f" (з них {flagged} позначено ПЕРЕВІР: мало поз, рядки в summary є; дивись маркери .bad_run*)"
                                                     if flagged else ""))
                if ctx.args.keep_data:
                    log("  --keep-data: .mcap й експорт лишаю")
                else:
                    cleanup_seq(ctx, log, n)
                return "done"
            log(f"{seq}: НЕ всі прогони готові — .mcap й експорт лишаю для розбору (повторний запуск доробить)")
            if n not in ctx.open_seqs:
                ctx.open_seqs.append(n)
            return "failed"
        finally:
            log.attach(None)


# ----------------------------------------------------------------------------- reporter and keep-awake

KEEP_AWAKE_PS = ("Add-Type -Name P -Namespace W -MemberDefinition '[DllImport(\"kernel32.dll\")] "
                 "public static extern uint SetThreadExecutionState(uint f);'; "
                 "[void][W.P]::SetThreadExecutionState([uint32]2147483649); [void][Console]::In.ReadToEnd()")


class KeepAwake:
    """Keeps Windows from sleeping while the worker lives (SetThreadExecutionState via powershell.exe).
    The helper exits when our end of its stdin closes, also if this process is killed hard."""
    def __init__(self):
        self.proc = None

    def start(self):
        try:
            self.proc = subprocess.Popen(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", KEEP_AWAKE_PS],
                                         stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
        except OSError:
            self.proc = None
            return False

    def stop(self):
        if self.proc is not None:
            try:
                self.proc.stdin.close()
                self.proc.wait(timeout=10)
            except Exception:
                self.proc.kill()
            self.proc = None


class Reporter:
    """Every ``every_min`` minutes (and once at the end) runs scripts/report/push_status.sh, which pushes progress and
    results to a git branch. Never raises and never blocks the worker; failures only go to stdout and the report log."""
    def __init__(self, ctx, every_min):
        self.ctx, self.every = ctx, every_min * 60
        self.stop_ev = threading.Event()
        self.thread = None
        self.last_msg = None
        self.lock = threading.Lock()      # a claim push from the main thread vs. the periodic one
        self.script = REPO_ROOT / "scripts" / "report" / "push_status.sh"
        self.logfile = ctx.logs / f"report_{ctx.host}.log"

    def _say(self, msg):
        line = f"{time.strftime('%H:%M:%S')} [звіт] {msg}"
        try:
            print(line, flush=True)
        except OSError:
            pass
        try:
            with open(self.logfile, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError:
            pass

    def push(self, force=False):
        with self.lock:
            self._push(force)

    def _push(self, force):
        a = self.ctx.args
        env = dict(os.environ, PUSH_FORCE="1" if force else "0", QUEUE_HOST=self.ctx.host, QUEUE_FROM=str(a.frm), QUEUE_DIR=a.dir,
                   QUEUE_METHODS=",".join(self.ctx.methods), QUEUE_RUNS=str(a.runs), DATA_ROOT=str(self.ctx.data_root))
        cmd = shlex.split(os.environ["QUEUE_PUSH_CMD"]) if os.environ.get("QUEUE_PUSH_CMD") else ["bash", str(self.script)]
        try:
            r = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=300,
                               start_new_session=True)
            msg = (r.stdout.strip().splitlines() or [""])[-1] if r.returncode == 0 else \
                  "ПОМИЛКА: " + ((r.stderr.strip().splitlines() or r.stdout.strip().splitlines() or ["?"])[-1])
        except (OSError, subprocess.TimeoutExpired) as e:
            msg = f"ПОМИЛКА: {e}"
        # repeat only changes, so a long outage does not flood the log
        if msg.split(" ", 1)[-1] != (self.last_msg or "").split(" ", 1)[-1] or "pushed" in msg:
            self._say(msg)
        self.last_msg = msg

    def _loop(self):
        while not self.stop_ev.wait(0 if self.last_msg is None else self.every):
            self.push()

    def start(self):
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def finish(self):
        self.stop_ev.set()
        if self.thread is not None:
            self.thread.join(timeout=310)
        self.push(force=True)            # last state, after the worker has cleared its own state file


def report_possible(ctx):
    if os.environ.get("QUEUE_PUSH_CMD"):
        return None
    if not (REPO_ROOT / "scripts" / "report" / "push_status.sh").exists():
        return "нема scripts/report/push_status.sh"
    if report_repo().startswith("https://") and not (conf_dir() / "github_token").is_file():
        return "нема токена GitHub: bash scripts/report/push_status.sh --token"
    return None


# ----------------------------------------------------------------------------- commands

def check_environment(ctx, log, need_hf):
    if any(ctx.registry[m].gpu for m in ctx.methods) and not ctx.args.no_gpu_check:
        try:
            ok = subprocess.run(["nvidia-smi", "-L"], capture_output=True, timeout=30).returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            ok = False
        if not ok:
            raise QueueAbort(3, "nvidia-smi не бачить GPU у WSL: онови драйвер NVIDIA у Windows і зроби `wsl --shutdown`.")
    if need_hf and not os.environ.get("QUEUE_FETCH_CMD"):
        hf = Path(os.environ.get("SLAM_VENV", str(Path.home() / "venvs" / "slam"))) / "bin" / "hf"
        try:
            ok = subprocess.run([str(hf) if hf.exists() else "hf", "auth", "whoami"], capture_output=True, timeout=60).returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            ok = False
        if not ok:
            raise QueueAbort(3, "не залогінений у Hugging Face. Виконай (токен вводиш сам): ~/.local/bin/hf auth login")


def cmd_run(ctx):
    a, log = ctx.args, Log()
    order = [a.only] if a.only else sequence_order(a.frm, a.dir, None)
    prev = read_json(ctx.state_file)
    if prev.get("pid") and prev["pid"] != os.getpid() and pid_alive(prev["pid"]):
        raise QueueAbort(1, f"на цьому ПК воркер уже працює (pid {prev['pid']}, {prev.get('seq')}). Зупини його Ctrl-C або дивись --status.")
    if a.stop_before is not None:
        write_stop(ctx, a.stop_before)
    stop = None if a.only else read_stop(ctx)
    if a.only is None and stop is not None and not sequence_order(a.frm, a.dir, stop):
        raise QueueAbort(1, f"відрізок порожній: з {a.frm} у напрямку {a.dir} до stop-before {stop} нічого брати. "
                            "Зніми межу: run_queue.sh --set-stop none")
    ctx.logs.mkdir(parents=True, exist_ok=True)
    set_state(ctx, pid=os.getpid(), host=ctx.host, seq=None, method=None, run=None, since=now_iso(),
              frm=a.frm, dir=a.dir, runs=a.runs, methods=ctx.methods)
    signal.signal(signal.SIGINT, _on_signal)
    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGHUP, _on_signal)     # terminal window closed: stop like Ctrl-C, do not orphan the method process
    log(f"Воркер {ctx.host}: з {a.frm} {'вгору' if a.dir == 'up' else 'вниз'}"
        f"{', лише ' + seq_name(a.only) if a.only else ''}, межа stop-before = {stop if stop is not None else 'нема'}, "
        f"методи {','.join(f'{k}×{ctx.runs_for(k)}' for k in ctx.methods)}, summary {ctx.summary}")
    counts = collections.Counter()
    consecutive_failed = 0
    code = 0
    finished = False
    keep = KeepAwake()
    if not a.no_keepawake:
        log("ПК не засинатиме, поки йде робота (Windows keep-awake)" if keep.start()
            else "УВАГА: не вдалось увімкнути keep-awake (powershell.exe): не дай ПК заснути вручну")
    reporter = None
    if a.report_every > 0 or ctx.use_peers:
        why = report_possible(ctx)
        if why:
            log(f"УВАГА: звіти на GitHub вимкнені: {why}"
                + ("; інший ПК не бачитиме, який сиквенс я взяв, лише готові прогони" if ctx.use_peers else ""))
        else:
            reporter = Reporter(ctx, a.report_every)
            if a.report_every > 0:
                reporter.start()
                log(f"звіт на GitHub: раз на {a.report_every:g} хв, при захопленні сиквенсу і в кінці (журнал {reporter.logfile.name})")
    ctx.reporter = reporter
    if not ctx.use_peers:
        log("інші ПК не враховую (--no-peers)")
    try:
        refresh_peers(ctx, log)
        if ctx.use_peers:
            log(f"інші ПК: {', '.join(p['host'] for p in ctx.peers) or 'гілок runner-* інших ПК ще нема'}")
        check_environment(ctx, log, need_hf=any(pending_runs(ctx, n) and not export_ready(ctx, n, {k for _, k in pending_runs(ctx, n)}) for n in order if allowed(ctx, n)))
        for n in order:
            if INTERRUPT.is_set():
                raise Interrupted()
            if not allowed(ctx, n):
                log(f"{seq_name(n)}: за межею (stop-before {read_stop(ctx)}) — кінець відрізка")
                break
            refresh_peers(ctx, log, max_age_s=60)
            res = process_seq(ctx, log, n)
            counts[res] += 1
            if not ctx.methods:
                log("усі методи вимкнено через помилки — рахувати нічим, зупиняюсь (скажи Теодору, надішли текст вище)")
                code = 2
                break
            if res == "stopped":
                break
            if res == "failed":
                code = 2
                consecutive_failed += 1
                if consecutive_failed >= MAX_CONSECUTIVE_FAILED_SEQS:
                    log(f"{MAX_CONSECUTIVE_FAILED_SEQS} сиквенси поспіль із помилками — схоже на проблему середовища, зупиняюсь")
                    break
            elif res == "done":
                consecutive_failed = 0
        else:
            finished = True
            log("у моєму напрямку більше нічого брати" + ("" if a.only else " (відрізок пройдено до кінця)"))
    except Interrupted:
        log("Перервано. Готові прогони збережені; повторний запуск тієї самої команди продовжить.")
        code = 130
    except QueueAbort as e:
        log(f"СТОП: {e}")
        code = e.code
    finally:
        clear_state(ctx)
        if reporter is not None:
            reporter.finish()
        keep.stop()
    if finished:
        print()
        print_table(ctx, summary_owners(ctx))
    if ctx.quarantined:
        code = code or 2
        log("УВАГА: у цій сесії вимикались методи через помилки: " + ", ".join(ctx.quarantined) + " — їхні недораховані прогони лишились у черзі")
    log("підсумок: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())) + f" | код виходу {code}")
    return code


def peer_lines(ctx):
    """Lines about the other PCs: last report, what they compute now, whether the cache is fresh."""
    if not ctx.use_peers:
        return ["Інші ПК: не враховую (--no-peers)"]
    fetch = read_json(ctx.peers_root / ".last_fetch.json")
    out = []
    if fetch.get("error"):
        ok_at = fetch.get("ok_at")
        out.append(f"УВАГА: стан інших ПК не оновився ({fetch['error']}); показую кеш"
                   + (f" від {time.strftime('%Y-%m-%d %H:%M', time.localtime(ok_at))}" if ok_at else " (порожній)"))
    if not ctx.peers:
        out.append("Інші ПК: гілок runner-* інших ПК ще нема")
    for p in ctx.peers:
        age = peer_age_h(p)
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(p["commit_time"])) if p["commit_time"] else "?"
        st = p["state"]
        if not st.get("pid"):
            now = "не рахує"
        else:
            now = f"рахує {st.get('seq') or '(готується)'} {st.get('method') or ''} " \
                  f"{('run' + str(st['run'])) if st.get('run') else ''}".rstrip() + f" з {st.get('since')}"
            if not peer_fresh(ctx, p):
                now += f" — звіт старший за {ctx.stale_h:g} год, вважаю зупиненим (не блокує)"
        out.append(f"Інший ПК {p['host']}: останній звіт {when} ({fmt_age(age)}); {now}")
    return out


def disk_label(ctx, n):
    if ctx.raw_dir(n).is_dir() and any(ctx.raw_dir(n).glob("*.mcap")):
        return "є .mcap"
    return "є експорт" if (ctx.export_dir(n) / "rgb.txt").exists() else "-"


def print_table(ctx, owners, running_seq=None):
    """13 rows: per method 'done/wanted' and who did it (merged with the other PCs), state, data on disk."""
    rows = []
    for n in range(1, N_SEQ + 1):
        cells, bad, done_all, done_any = [], 0, True, False
        for k in ctx.methods:
            d, want, b, hosts = cell_state(ctx, k, n, owners)
            bad += b
            done_all &= d == want
            done_any |= d > 0
            cells.append(f"{ctx.label(k)} {d}/{want}" + (f" {'+'.join(sorted(hosts, key=host_rank))}" if hosts else ""))
        busy = peer_claims(ctx, n)
        if seq_name(n) == running_seq:
            label = "в роботі"
        elif done_all:
            label = "готово"
        elif busy:
            label = "рахує " + "+".join(busy)
        elif bad:
            label = "ПЕРЕВІР"
        elif done_any:
            label = "частково"
        else:
            label = "чекає"
        rows.append((n, ", ".join(cells), label))
    w = max([26] + [len(c) for _, c, _ in rows])
    lw = max([12] + [len(lb) for _, _, lb in rows])
    print(f"{'#':>2}  {'seq':6} {'.mcap':>8}  {'прогони (готово/треба, хто)':<{w}} {'стан':<{lw}} дані на диску")
    for n, c, lb in rows:
        print(f"{n:>2}  {seq_name(n):6} {fmt_gb(MCAP_GB[n]):>8}  {c:<{w}} {lb:<{lw}} {disk_label(ctx, n)}")


def cmd_dry_run(ctx):
    a = ctx.args
    refresh_peers(ctx, lambda msg: print(f"[dry-run] {msg}"))
    order = [a.only] if a.only else sequence_order(a.frm, a.dir, a.stop_before if a.stop_before is not None else read_stop(ctx))
    owners = summary_owners(ctx)
    print(f"[dry-run] хост {ctx.host}; методи {', '.join(f'{k}×{ctx.runs_for(k)}' for k in ctx.methods)}; summary {ctx.summary}")
    for line in peer_lines(ctx):
        print(f"[dry-run] {line}")
    print(f"[dry-run] порядок: {' -> '.join(seq_name(n) for n in order) or '(порожньо)'}")
    avail, why, warns = disk_room(ctx)
    for w in warns:
        print(f"[dry-run] УВАГА: {w}")
    print(f"[dry-run] місце для даних: {why}" + ("; --keep-data: дані сиквенсів лишаються й з'їдають це місце" if a.keep_data else ""))
    print(f"{'seq':6} {'.mcap':>8}  {'прогонів лишилось':<18} {'треба місця':>12}  {'дія':<34} хто що порахував")
    total_dl, peak, room = 0.0, 0.0, avail
    for n in order:
        todo = pending_runs(ctx, n, owners)
        busy = peer_claims(ctx, n) if todo else []
        tk = {k for _, k in todo}
        need = disk_need_gb(ctx, n, tk) if todo and not busy else 0.0
        peak = max(peak, need)
        if todo and not busy and not export_ready(ctx, n, tk):
            total_dl += 0.0 if any(ctx.raw_dir(n).glob("*.mcap")) else MCAP_GB[n]
        if not todo:
            act = "пропустити (готово)"
        elif busy:
            act = f"пропустити (рахує {'+'.join(busy)})"
        elif need and need > room:
            act = f"СТОП: не вміститься (є {fmt_gb(room)})"
        else:
            act = "качати + рахувати" if need else "рахувати (експорт є)"
            if need and a.keep_data:
                room -= need - DISK_RESERVE_GB      # the data of this sequence stays on disk
        cells = []
        for k in ctx.methods:
            d, want, _, hosts = cell_state(ctx, k, n, owners)
            cells.append(f"{ctx.label(k)} {d}/{want}" + (f" {'+'.join(sorted(hosts, key=host_rank))}" if hosts else ""))
        print(f"{seq_name(n):6} {fmt_gb(MCAP_GB[n]):>8}  {len(todo):<18} {fmt_gb(need) if need else '-':>12}  {act:<34} {', '.join(cells)}")
    print(f"[dry-run] доступно {fmt_gb(avail)}; найбільша потреба для одного сиквенсу: {fmt_gb(peak)}; "
          f"всього завантажити: {fmt_gb(total_dl)} (стан іншого ПК може змінитись до старту)")
    print("[dry-run] нічого не завантажено й не запущено.")
    return 0


def cmd_status(ctx):
    a = ctx.args
    refresh_peers(ctx, lambda msg: None)          # a failed refresh is shown by peer_lines() below
    st = read_json(ctx.state_file)
    alive = bool(st.get("pid")) and pid_alive(st["pid"])
    stop = read_stop(ctx)
    owners = summary_owners(ctx)
    avail, why, warns = disk_room(ctx)
    print(f"Хост: {ctx.host}   дані: {ctx.data_root}   summary: {ctx.summary.name}")
    print(f"Місце для даних: {why}")
    for w in warns:
        print(f"УВАГА: {w}")
    print(f"Методи (прогонів): {', '.join(f'{k}×{ctx.runs_for(k)}' for k in ctx.methods)}   "
          f"межа stop-before: {stop if stop is not None else 'нема'}")
    if alive:
        print(f"Воркер ПРАЦЮЄ (pid {st['pid']}): {st.get('seq')} {st.get('method') or ''} "
              f"{('run' + str(st['run'])) if st.get('run') else ''} з {st.get('since')}; напрямок {st.get('dir')} від {st.get('frm')}")
    else:
        print("Воркер не працює" + (" (знайдено застарілий файл стану — ігнорую)" if st else ""))
    for line in peer_lines(ctx):
        print(line)
    print()
    print_table(ctx, owners, st.get("seq") if alive else None)
    order_all = sequence_order(a.frm, a.dir, None)
    chain = []
    for n in order_all:
        if seq_complete(ctx, n, owners):
            chain.append(n)
        else:
            break
    print()
    nxt = next((n for n in ([a.only] if a.only else order_all) if pending_runs(ctx, n, owners) and not peer_claims(ctx, n)), None)
    if alive and st.get("seq"):
        here = f"{int(str(st['seq'])[3:])} ({st['seq']}, зараз рахується)"
    elif nxt is None:
        here = "у твоєму напрямку більше нічого брати"
    elif allowed(ctx, nxt):
        here = f"{nxt} ({seq_name(nxt)}, наступний у напрямку {a.dir} від {a.frm})"
    else:
        here = f"{nxt} ({seq_name(nxt)}) — це вже за межею stop-before {stop}: твій відрізок завершено"
    print(f"Ти тут: {here}")
    if chain:
        print(f"Готово підряд від твого кінця ({a.frm}, {a.dir}): {seq_name(chain[0])} … {seq_name(chain[-1])}")
    else:
        print(f"Від твого кінця ({a.frm}, {a.dir}) повністю готових сиквенсів ще нема.")
    return 0


def build_parser():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--from", dest="frm", type=int, default=None, metavar="N", help="start position 1..13 (default: 1 for --dir up, 13 for down)")
    ap.add_argument("--dir", choices=["up", "down"], default="up", help="direction of travel (default up)")
    ap.add_argument("--stop-before", type=int, default=None, metavar="N",
                    help="do not take sequence N or any later one in the direction of travel (also saved for a running worker)")
    ap.add_argument("--set-stop", metavar="N|none", help="only write/clear the boundary file and exit (a running worker picks it up)")
    ap.add_argument("--only", type=int, default=None, metavar="N", help="process just this one sequence")
    ap.add_argument("--methods", default="dpvo",
                    help="comma list: 'dpvo' (every mode of a method), 'orbslam3:mono_inertial' (one mode) or 'all' (default dpvo)")
    ap.add_argument("--runs", type=int, default=1,
                    help="runs per method per sequence (default 1); a 'runs' column in methods.conf overrides it per method")
    ap.add_argument("--status", action="store_true", help="print the table of all 13 positions and 'you are here', then exit")
    ap.add_argument("--dry-run", action="store_true", help="print the plan and the disk need; download and run nothing")
    ap.add_argument("--timeout-h", type=float, default=4.0,
                    help="timeout of one method run, hours (default 4: the slowest run measured is ~1 h; a hung method must not eat a night)")
    ap.add_argument("--fetch-timeout-h", type=float, default=8.0, help="timeout of download+export of one sequence, hours (default 8)")
    ap.add_argument("--keep-data", action="store_true", help="never delete the .mcap and the export")
    ap.add_argument("--report-every", type=float, default=10.0, metavar="MIN",
                    help="push progress to git every MIN minutes and at the end (0 = no periodic push; the claim of a "
                         "sequence is still pushed unless --no-peers; needs scripts/report/push_status.sh --token)")
    ap.add_argument("--no-peers", action="store_true",
                    help="ignore the other PCs' runner-* branches (no fetch, no claim push)")
    ap.add_argument("--peer-stale-h", type=float, default=3.0, metavar="H",
                    help="another PC's claim of a sequence counts only if its branch was updated less than H hours ago (default 3)")
    ap.add_argument("--no-keepawake", action="store_true", help="do not stop Windows from sleeping while the worker runs")
    ap.add_argument("--results-root", default=str(REPO_ROOT / "results"))
    ap.add_argument("--registry", default=str(Path(__file__).with_name("methods.conf")))
    # test hooks / escape hatches (documented in test_run_queue.py)
    ap.add_argument("--eval-script", default=str(REPO_ROOT / "scripts" / "eval.py"), help=argparse.SUPPRESS)
    ap.add_argument("--no-disk-check", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--no-gpu-check", action="store_true", help=argparse.SUPPRESS)
    return ap


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = build_parser()
    a = ap.parse_args(argv)
    if a.frm is None:
        a.frm = 1 if a.dir == "up" else N_SEQ
    for name, v in (("--from", a.frm), ("--stop-before", a.stop_before), ("--only", a.only)):
        if v is not None and not 1 <= v <= N_SEQ:
            ap.error(f"{name} must be 1..{N_SEQ}")
    if a.runs < 1:
        ap.error("--runs must be >= 1")
    if a.stop_before is not None and not a.only and (a.stop_before <= a.frm if a.dir == "up" else a.stop_before >= a.frm):
        ap.error(f"--stop-before {a.stop_before} leaves nothing to do when going {a.dir} from {a.frm}")
    ctx = Ctx(a)
    try:
        data_budget_gb()                  # a bad DATA_BUDGET_GB stops every command before any work
    except QueueAbort as e:
        print(f"СТОП: {e}", file=sys.stderr)
        return e.code
    if a.set_stop is not None:
        if a.set_stop.lower() == "none":
            write_stop(ctx, None)
            print(f"межу знято ({ctx.stop_file})")
        else:
            try:
                v = int(a.set_stop)
                assert 1 <= v <= N_SEQ
            except (ValueError, AssertionError):
                ap.error(f"--set-stop expects 1..{N_SEQ} or none")
            write_stop(ctx, v)
            print(f"межу виставлено: не братиму {seq_name(v)} і далі в напрямку руху ({ctx.stop_file}); працюючий воркер підхопить її сам")
        return 0
    if a.status:
        return cmd_status(ctx)
    if a.dry_run:
        return cmd_dry_run(ctx)
    try:
        return cmd_run(ctx)
    except QueueAbort as e:
        print(f"СТОП: {e}", file=sys.stderr)
        return e.code


if __name__ == "__main__":
    sys.exit(main())
