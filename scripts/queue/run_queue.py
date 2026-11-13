#!/usr/bin/env python3
"""Queue worker: run SLAM methods over the UCU sequences 1..13, starting from one end of the range.

The unit of work is a sequence. For every sequence the worker does, in one pass:
  hf download -> export_tum.py (scripts/data/fetch_ucu.sh) -> every (method x run)
  -> check the output -> scripts/eval.py -> a row in results/summary_<host>.csv
  -> delete the .mcap and the export (only if every wanted run is finished).

Two PCs split the range from opposite ends (no shared file, no network between them):
  main PC :  run_queue.sh --from 1  --dir up     (1, 2, 3, ...)
  brother :  run_queue.sh --from 13 --dir down   (13, 12, 11, ...)
A human asks the other side where they are and sets the meeting point with --set-stop N
(or --stop-before N); a running worker re-reads it before every run, so no restart is needed.
Finished runs are skipped, so overlapping ranges only cost a quick "already done".

Stdlib only; runs under any Python >= 3.8 in WSL. See scripts/queue/methods.conf for the method contract.
"""
from __future__ import annotations

import argparse
import collections
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
from typing import NamedTuple

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


class Method(NamedTuple):
    name: str
    mode: str
    script: str
    gpu: bool


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
    reg = {}
    for ln, line in enumerate(Path(path).read_text().splitlines(), 1):
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) != 4 or parts[3] not in ("gpu", "cpu") or not all(parts):
            raise SystemExit(f"{path}:{ln}: expected 'method | mode | script | gpu|cpu', got: {line}")
        reg[parts[0]] = Method(parts[0], parts[1], parts[2], parts[3] == "gpu")
    return reg


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
        names = list(self.registry) if args.methods.strip() == "all" else [m.strip() for m in args.methods.split(",") if m.strip()]
        unknown = [m for m in names if m not in self.registry]
        if unknown or not names:
            raise SystemExit(f"unknown method(s) {unknown or args.methods!r}; registry {args.registry} has: {', '.join(self.registry)}")
        self.methods = names
        self.state = {}

    def raw_dir(self, n):
        return self.data_root / "ucu" / f"sequence-{n}"

    def export_dir(self, n):
        return self.data_root / "ucu_tum" / seq_name(n)

    def res_dir(self, method, n):
        return self.results_root / method / self.registry[method].mode / DATASET / seq_name(n)


def summary_keys(ctx):
    """(method, mode, dataset, seq, run) of every row in results/summary*.csv (any host; sidecar tables excluded)."""
    keys = set()
    for p in ctx.results_root.glob("summary*.csv"):
        if p.name.startswith("summary_extra"):
            continue
        try:
            with open(p, newline="") as f:
                for row in csv.DictReader(f):
                    keys.add((row.get("method"), row.get("mode"), row.get("dataset"), row.get("seq"), row.get("run")))
        except OSError:
            pass
    return keys


def run_state(ctx, method, n, r, keys=None):
    """'done' | 'bad' | None (pending). A summary row counts as done: results are append-only."""
    d = ctx.res_dir(method, n)
    if (d / f".done_run{r}").exists():
        return "done"
    if (d / f".bad_run{r}").exists():
        return "bad"
    keys = summary_keys(ctx) if keys is None else keys
    if (method, ctx.registry[method].mode, DATASET, seq_name(n), str(r)) in keys:
        return "done"
    return None


def pending_runs(ctx, n):
    keys = summary_keys(ctx)
    return [(r, m) for r in range(1, ctx.args.runs + 1) for m in ctx.methods
            if run_state(ctx, m, n, r, keys) is None]


def seq_complete(ctx, n):
    keys = summary_keys(ctx)
    return all(run_state(ctx, m, n, r, keys) == "done" for m in ctx.methods for r in range(1, ctx.args.runs + 1))


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

def disk_need_gb(ctx, n):
    """Free space needed to bring sequence n to the exported state (0 if the export already exists)."""
    if (ctx.export_dir(n) / "rgb.txt").exists():
        return 0.0
    size = MCAP_GB[n]
    have_mcap = any(ctx.raw_dir(n).glob("*.mcap")) if ctx.raw_dir(n).is_dir() else False
    return (0.0 if have_mcap else size) + EXPORT_SHARE * size + DISK_RESERVE_GB


def free_gb(path):
    p = Path(path)
    while not p.exists() and p != p.parent:
        p = p.parent
    return shutil.disk_usage(p).free / 1e9


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

def child_env(ctx):
    env = dict(os.environ)
    env.update(NO_EVAL="1", QUEUE_HOST=ctx.host, RESULTS_ROOT=str(ctx.results_root),
               SUMMARY_FILE=str(ctx.summary), DATA_ROOT=str(ctx.data_root))
    return env


def eval_command(ctx, method, n, r, run_log):
    m = ctx.registry[method]
    py = Path(os.environ.get("SLAM_VENV", str(Path.home() / "venvs" / "slam"))) / "bin" / "python"
    py = str(py) if py.exists() else sys.executable
    return [py, str(ctx.args.eval_script), "--method", method, "--mode", m.mode, "--dataset", DATASET,
            "--seq", seq_name(n), "--run", str(r), "--summary", str(ctx.summary),
            "--results-root", str(ctx.results_root), "--log", str(run_log)]


def write_marker(path, **fields):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(f"{k}={v}\n" for k, v in fields.items()))


def do_run(ctx, log, seq_fh, n, r, method):
    """One (method, run) on sequence n. Returns 'done' | 'bad' | 'failed'. Raises Interrupted."""
    m, seq = ctx.registry[method], seq_name(n)
    d = ctx.res_dir(method, n)
    traj = d / f"traj_run{r}.txt"
    run_log = ctx.logs / f"queue_{ctx.host}_{seq}_{method}_run{r}.log"
    set_state(ctx, seq=seq, method=method, run=r, since=now_iso())
    t0 = time.time()
    log(f"-- {seq} {method} run{r}/{ctx.args.runs}: старт")
    with open(run_log, "a", buffering=1, encoding="utf-8") as rl:
        logs = [seq_fh, rl]
        if traj.exists():
            log(f"   траєкторія {traj.name} уже є, але рядка в summary нема: роблю лише оцінку")
        else:
            cmd = ["bash", str(REPO_ROOT / m.script), DATASET, seq, "--run", str(r)]
            try:
                rc, reason, tail = run_streamed(cmd, env=child_env(ctx), cwd=REPO_ROOT, logs=logs,
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
            if rc != 0:
                log(f"   ПОМИЛКА: {m.script} завершився з кодом {rc}. Кінець логу:")
                for ln in tail:
                    log(f"   | {ln}")
                return "failed"
        rc, reason, tail = run_streamed(eval_command(ctx, method, n, r, run_log), env=child_env(ctx), cwd=REPO_ROOT,
                                        logs=logs, timeout_s=1800)
        if reason == "interrupt":
            raise Interrupted()                       # trajectory is complete: next start does the evaluation only
        if rc != 0:
            log(f"   ПОМИЛКА оцінки (eval.py, код {rc}). Кінець логу:")
            for ln in tail:
                log(f"   | {ln}")
            return "failed"
    if (method, m.mode, DATASET, seq, str(r)) not in summary_keys(ctx):
        log(f"   ПОМИЛКА: eval.py не додав рядок у {ctx.summary.name}")
        return "failed"
    frames = count_data_lines(ctx.export_dir(n) / "rgb.txt")
    poses = count_data_lines(traj)
    wall = time.time() - t0
    ok = frames > 0 and poses >= POSE_TOL * frames
    write_marker(d / (f".done_run{r}" if ok else f".bad_run{r}"), host=ctx.host, finished=now_iso(),
                 wall_s=f"{wall:.0f}", poses=poses, frames=frames)
    if ok:
        log(f"   готово за {wall / 60:.1f} хв: поз {poses}/{frames}; рядок додано в {ctx.summary.name}")
        return "done"
    log(f"   ПЕРЕВІР: поз {poses} із {frames} кадрів (поріг {POSE_TOL:.0%}); рядок у {ctx.summary.name} є, "
        f"маркер .bad_run{r} поставлено, дані сиквенсу залишаю")
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


def process_seq(ctx, log, n):
    """Returns 'skipped' | 'done' | 'failed' | 'stopped'."""
    seq = seq_name(n)
    todo = pending_runs(ctx, n)
    if not todo:
        log(f"{seq}: усе вже пораховано, пропускаю")
        return "skipped"
    ctx.logs.mkdir(parents=True, exist_ok=True)
    seq_log = ctx.logs / f"queue_{ctx.host}_{seq}_{time.strftime('%Y%m%d_%H%M%S')}.log"
    with open(seq_log, "a", buffering=1, encoding="utf-8") as fh:
        log.attach(fh)
        try:
            log(f"== {seq}: прогонів до виконання {len(todo)}; лог {seq_log}")
            set_state(ctx, seq=seq, method=None, run=None, since=now_iso())
            if not (ctx.export_dir(n) / "rgb.txt").exists():
                need = disk_need_gb(ctx, n)
                free = free_gb(ctx.data_root)
                if not ctx.args.no_disk_check and free < need:
                    raise QueueAbort(4, f"мало місця на диску даних ({ctx.data_root}): для {seq} треба ≈ {fmt_gb(need)}, "
                                        f"вільно {fmt_gb(free)}. Нічого не видаляю: звільни місце й запусти ту саму команду.")
                fetch_seq(ctx, log, fh, n)
            for r, method in todo:
                if INTERRUPT.is_set():
                    raise Interrupted()
                if not allowed(ctx, n):
                    log(f"{seq}: межа змінена (stop-before {read_stop(ctx)}), зупиняюсь; дані лишаю до наступного запуску")
                    return "stopped"
                do_run(ctx, log, fh, n, r, method)
            if seq_complete(ctx, n):
                log(f"{seq}: усі прогони готові")
                if ctx.args.keep_data:
                    log("  --keep-data: .mcap й експорт лишаю")
                else:
                    cleanup_seq(ctx, log, n)
                return "done"
            log(f"{seq}: НЕ всі прогони готові — .mcap й експорт лишаю для розбору (повторний запуск доробить)")
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
        a = self.ctx.args
        env = dict(os.environ, PUSH_FORCE="1" if force else "0", QUEUE_HOST=self.ctx.host, QUEUE_FROM=str(a.frm), QUEUE_DIR=a.dir,
                   QUEUE_METHODS=",".join(self.ctx.methods), QUEUE_RUNS=str(a.runs), DATA_ROOT=str(self.ctx.data_root))
        try:
            r = subprocess.run(["bash", str(self.script)], env=env, capture_output=True, text=True, timeout=300,
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
    if not (REPO_ROOT / "scripts" / "report" / "push_status.sh").exists():
        return "нема scripts/report/push_status.sh"
    repo = os.environ.get("REPORT_REPO", "https://github.com/oToTak/slam-project-methods.git")
    if repo.startswith("https://") and not (Path.home() / ".config" / "slam" / "github_token").is_file():
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
        f"методи {','.join(ctx.methods)}, прогонів {a.runs}, summary {ctx.summary}")
    counts = collections.Counter()
    consecutive_failed = 0
    code = 0
    keep = KeepAwake()
    if not a.no_keepawake:
        log("ПК не засинатиме, поки йде робота (Windows keep-awake)" if keep.start()
            else "УВАГА: не вдалось увімкнути keep-awake (powershell.exe): не дай ПК заснути вручну")
    reporter = None
    if a.report_every > 0:
        why = report_possible(ctx)
        if why:
            log(f"УВАГА: звіти на GitHub вимкнені: {why}")
        else:
            reporter = Reporter(ctx, a.report_every)
            reporter.start()
            log(f"звіт на GitHub: раз на {a.report_every:g} хв і в кінці (журнал {reporter.logfile.name})")
    try:
        check_environment(ctx, log, need_hf=any(pending_runs(ctx, n) and not (ctx.export_dir(n) / "rgb.txt").exists() for n in order if allowed(ctx, n)))
        for n in order:
            if INTERRUPT.is_set():
                raise Interrupted()
            if not allowed(ctx, n):
                log(f"{seq_name(n)}: за межею (stop-before {read_stop(ctx)}) — кінець відрізка")
                break
            res = process_seq(ctx, log, n)
            counts[res] += 1
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
            log("відрізок пройдено до кінця")
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
    log("підсумок: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())) + f" | код виходу {code}")
    return code


def cmd_dry_run(ctx):
    a = ctx.args
    order = [a.only] if a.only else sequence_order(a.frm, a.dir, a.stop_before if a.stop_before is not None else read_stop(ctx))
    print(f"[dry-run] хост {ctx.host}; методи {','.join(ctx.methods)}; прогонів {a.runs}; summary {ctx.summary}")
    print(f"[dry-run] порядок: {' -> '.join(seq_name(n) for n in order) or '(порожньо)'}")
    print(f"{'seq':6} {'.mcap':>8}  {'прогонів лишилось':<18} {'треба місця':>12}  дія")
    total_dl, peak = 0.0, 0.0
    for n in order:
        todo = pending_runs(ctx, n)
        need = disk_need_gb(ctx, n) if todo else 0.0
        peak = max(peak, need)
        if todo and not (ctx.export_dir(n) / "rgb.txt").exists():
            total_dl += 0.0 if any(ctx.raw_dir(n).glob("*.mcap")) else MCAP_GB[n]
        act = "пропустити (готово)" if not todo else ("качати + рахувати" if need else "рахувати (експорт є)")
        print(f"{seq_name(n):6} {fmt_gb(MCAP_GB[n]):>8}  {len(todo):<18} {fmt_gb(need) if need else '-':>12}  {act}")
    print(f"[dry-run] вільно на {ctx.data_root}: {fmt_gb(free_gb(ctx.data_root))}; найбільша потреба для одного сиквенсу: {fmt_gb(peak)}; "
          f"всього завантажити: {fmt_gb(total_dl)}")
    print("[dry-run] нічого не завантажено й не запущено.")
    return 0


def cmd_status(ctx):
    a = ctx.args
    st = read_json(ctx.state_file)
    alive = bool(st.get("pid")) and pid_alive(st["pid"])
    stop = read_stop(ctx)
    keys = summary_keys(ctx)
    print(f"Хост: {ctx.host}   дані: {ctx.data_root} (вільно {fmt_gb(free_gb(ctx.data_root))})   summary: {ctx.summary.name}")
    print(f"Методи: {','.join(ctx.methods)}   прогонів на метод: {a.runs}   межа stop-before: {stop if stop is not None else 'нема'}")
    if alive:
        print(f"Воркер ПРАЦЮЄ (pid {st['pid']}): {st.get('seq')} {st.get('method') or ''} "
              f"{('run' + str(st['run'])) if st.get('run') else ''} з {st.get('since')}; напрямок {st.get('dir')} від {st.get('frm')}")
    else:
        print("Воркер не працює" + (" (знайдено застарілий файл стану — ігнорую)" if st else ""))
    print()
    print(f"{'#':>2}  {'seq':6} {'.mcap':>8}  {'прогони (готово/треба)':<26} {'стан':<12} дані на диску")
    running_seq = st.get("seq") if alive else None
    for n in range(1, N_SEQ + 1):
        cells, bad, done_all, done_any = [], 0, True, False
        for m in ctx.methods:
            states = [run_state(ctx, m, n, r, keys) for r in range(1, a.runs + 1)]
            d = states.count("done")
            bad += states.count("bad")
            done_all &= d == a.runs
            done_any |= d > 0
            cells.append(f"{m} {d}/{a.runs}")
        if seq_name(n) == running_seq:
            label = "в роботі"
        elif done_all:
            label = "готово"
        elif bad:
            label = "ПЕРЕВІР"
        elif done_any:
            label = "частково"
        else:
            label = "чекає"
        on_disk = "є .mcap" if ctx.raw_dir(n).is_dir() and any(ctx.raw_dir(n).glob("*.mcap")) else ("є експорт" if (ctx.export_dir(n) / "rgb.txt").exists() else "-")
        print(f"{n:>2}  {seq_name(n):6} {fmt_gb(MCAP_GB[n]):>8}  {', '.join(cells):<26} {label:<12} {on_disk}")
    order_all = sequence_order(a.frm, a.dir, None)
    chain = []
    for n in order_all:
        if seq_complete(ctx, n):
            chain.append(n)
        else:
            break
    print()
    nxt = next((n for n in ([a.only] if a.only else order_all) if pending_runs(ctx, n)), None)
    if alive and st.get("seq"):
        here = f"{int(str(st['seq'])[3:])} ({st['seq']}, зараз рахується)"
    elif nxt is None:
        here = "відрізок пройдено до кінця"
    elif allowed(ctx, nxt):
        here = f"{nxt} ({seq_name(nxt)}, наступний у напрямку {a.dir} від {a.frm})"
    else:
        here = f"{nxt} ({seq_name(nxt)}) — це вже за межею stop-before {stop}: твій відрізок завершено"
    print(f"Ти тут: {here}")
    if chain:
        print(f"Готово підряд від твого кінця ({a.frm}, {a.dir}): {seq_name(chain[0])} … {seq_name(chain[-1])}  ← це й повідом іншому ПК")
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
    ap.add_argument("--methods", default="dpvo", help="comma list of methods from the registry, or 'all' (default dpvo)")
    ap.add_argument("--runs", type=int, default=1, help="runs per method per sequence (default 1)")
    ap.add_argument("--status", action="store_true", help="print the table of all 13 positions and 'you are here', then exit")
    ap.add_argument("--dry-run", action="store_true", help="print the plan and the disk need; download and run nothing")
    ap.add_argument("--timeout-h", type=float, default=12.0, help="timeout of one method run, hours (default 12)")
    ap.add_argument("--fetch-timeout-h", type=float, default=8.0, help="timeout of download+export of one sequence, hours (default 8)")
    ap.add_argument("--keep-data", action="store_true", help="never delete the .mcap and the export")
    ap.add_argument("--report-every", type=float, default=10.0, metavar="MIN",
                    help="push progress to git every MIN minutes and at the end (0 = off; needs scripts/report/push_status.sh --token)")
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
