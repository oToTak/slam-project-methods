"""Build one status table of all PCs from their runner-<host> branches (stdlib only).

    python3 -I scripts/report/build_status.py                     # refs/remotes/origin/runner-*
    python3 -I scripts/report/build_status.py --refs 'refs/heads/runner-*'
    python3 -I scripts/report/build_status.py --dir TEODOR=/path/to/branch-checkout   # tests/local

Reads every runner-<host> branch WITHOUT checking it out (git cat-file), plus
scripts/queue/methods.conf, and writes into --out (default status/):
  STATUS.md                     13 sequences x method:mode, done/needed and who computed it,
                                what each PC is doing now, total "X of Y runs"
  status.json                   the same for machines
  summaries/summary_<host>.csv  copies of the summary tables found on the branches

Branch contract (TASK_sync_two_pcs.md section 2): status.txt, queue_state.json,
results/summary_<host>.csv, results/<method>/<mode>/ucu/seqNN/...
The files are rewritten ONLY when their content changes; a new report time alone is not a change,
so the GitHub Action (.github/workflows/status.yml) does not fill main with commits.
Never touches results/, code or the runner-* branches.
"""

import argparse
import csv
import hashlib
import io
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

N_SEQ = 13
DATASET = "ucu"
DEFAULT_RUNS = 3
DEFAULT_STALE_H = 3.0
DEFAULT_REFS = "refs/remotes/origin/runner-*"
HERE = Path(__file__).resolve().parent
DEFAULT_CONF = HERE.parent / "queue" / "methods.conf"
MARKER_RE = re.compile(r"^results/([^/]+)/([^/]+)/ucu/seq(\d+)/\.(done|bad)_run(\d+)$")
SUMMARY_RE = re.compile(r"^results/(summary[^/]*)\.csv$")
VOLATILE = "\x00VOLATILE\x00"  # stands for report/generation times when hashing the content


def warn(msg):
    print(f"[warn] {msg}", file=sys.stderr)


def seq_name(n):
    return f"seq{n:02d}"


def seq_number(s):
    m = re.fullmatch(r"seq(\d+)", (s or "").strip())
    return int(m.group(1)) if m else None


# ----------------------------------------------------------------------------- registry


def load_registry(path, default_runs=DEFAULT_RUNS):
    """[(key, method, mode, runs)] from methods.conf, in file order.

    Accepts the old 4-column format  method | mode | script | gpu|cpu
    and the new one with an optional 5th column  runs  (a number, 'runs=N' or empty).
    """
    out, seen = [], {}
    for ln, raw in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) not in (4, 5) or not parts[0] or not parts[1]:
            warn(f"{path}:{ln}: expected 'method | mode | script | gpu|cpu [| runs]': {line}")
            continue
        method, mode = parts[0], parts[1]
        runs = default_runs
        if len(parts) == 5 and parts[4]:
            m = re.fullmatch(r"(?:runs\s*=\s*)?(\d+)", parts[4])
            if not m or int(m.group(1)) < 1:
                warn(f"{path}:{ln}: bad runs column '{parts[4]}', using {default_runs}")
            else:
                runs = int(m.group(1))
        key = f"{method}:{mode}"
        if key in seen:
            warn(f"{path}:{ln}: {key} listed twice, the later line wins")
            out[seen[key]] = (key, method, mode, runs)
            continue
        seen[key] = len(out)
        out.append((key, method, mode, runs))
    if not out:
        raise SystemExit(f"{path}: no methods found")
    return out


# ----------------------------------------------------------------------------- sources


class GitSource:
    """One runner-<host> branch, read with git plumbing (no checkout)."""

    def __init__(self, repo, ref):
        self.repo, self.ref = repo, ref
        name = ref.rsplit("/", 1)[-1]
        self.host = name[len("runner-") :] if name.startswith("runner-") else name

    def _git(self, *args):
        r = subprocess.run(["git", "-C", str(self.repo), *args], capture_output=True)
        return r.stdout if r.returncode == 0 else None

    def files(self):
        out = self._git("ls-tree", "-r", "-z", "--name-only", self.ref)
        return [p for p in (out or b"").decode("utf-8", "replace").split("\0") if p]

    def read(self, path):
        data = self._git("cat-file", "blob", f"{self.ref}:{path}")
        return None if data is None else data.decode("utf-8", "replace")

    def last_report(self):
        out = self._git("log", "-1", "--format=%cI", self.ref)
        return parse_time(out.decode().strip()) if out else None


class DirSource:
    """A plain directory with the branch contents (tests, or a local copy)."""

    def __init__(self, host, root):
        self.host, self.root, self.ref = host, Path(root), str(root)

    def files(self):
        if not self.root.is_dir():
            return []
        files = (p for p in self.root.rglob("*") if p.is_file())
        return sorted(p.relative_to(self.root).as_posix() for p in files)

    def read(self, path):
        try:
            return (self.root / path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None

    def last_report(self):
        paths = [self.root / f for f in ("status.txt", "queue_state.json")]
        times = [p.stat().st_mtime for p in paths if p.is_file()]
        return datetime.fromtimestamp(max(times), timezone.utc) if times else None


def git_sources(repo, patterns):
    refs = []
    for pat in patterns:
        r = subprocess.run(
            ["git", "-C", str(repo), "for-each-ref", "--format=%(refname)", pat],
            capture_output=True,
            text=True,
        )
        if r.returncode != 0:
            raise SystemExit(f"git for-each-ref {pat} failed: {r.stderr.strip()}")
        refs += [x for x in r.stdout.split() if x not in refs]
    by_host = {}
    for ref in refs:
        s = GitSource(repo, ref)
        if s.host in by_host:
            warn(f"host {s.host}: both {by_host[s.host].ref} and {ref}; using the first")
            continue
        by_host[s.host] = s
    return [by_host[h] for h in sorted(by_host)]


def parse_time(s):
    try:
        t = datetime.fromisoformat(s.strip().replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


# ----------------------------------------------------------------------------- reading one PC


def read_host(src):
    """Everything the table needs from one branch."""
    files = src.files()
    rows, summaries = [], {}
    for path in files:
        m = SUMMARY_RE.match(path)
        if not m or m.group(1).startswith("summary_extra"):
            continue
        text = src.read(path)
        if text is None:
            continue
        summaries[m.group(1)] = text
        for row in csv.DictReader(io.StringIO(text)):
            rows.append({k: (v or "").strip() for k, v in row.items() if k})
    markers = []
    for path in files:
        m = MARKER_RE.match(path)
        if m:
            key = f"{m.group(1)}:{m.group(2)}"
            markers.append((key, int(m.group(3)), m.group(4), int(m.group(5))))
    try:
        state = json.loads(src.read("queue_state.json") or "{}")
    except ValueError:
        state = {}
    status_txt = src.read("status.txt") or ""
    if "Воркер ПРАЦЮЄ" in status_txt:
        worker = "running"
    elif "Воркер не працює" in status_txt:
        worker = "idle"
    else:
        worker = "running" if state.get("pid") else "unknown"
    return {
        "host": src.host,
        "ref": src.ref,
        "rows": rows,
        "summaries": summaries,
        "markers": markers,
        "state": state if isinstance(state, dict) else {},
        "worker": worker,
        "last_report": src.last_report(),
    }


# ----------------------------------------------------------------------------- merging


def state_matches(state_method, key):
    """queue_state 'method' may be 'dpvo' (old worker) or 'dpvo:mono' (method:mode registry)."""
    return bool(state_method) and (state_method == key or state_method == key.split(":", 1)[0])


def build(registry, hosts, now, stale_h):
    keys = {k: runs for k, _, _, runs in registry}
    done = {}  # (key, n, r) -> set(host)
    bad = {}
    other = set()
    for h in hosts:
        for row in h["rows"]:
            n = seq_number(row.get("seq"))
            key = f"{row.get('method', '')}:{row.get('mode', '')}"
            try:
                r = int(row.get("run", ""))
            except ValueError:
                continue
            if row.get("dataset") != DATASET or n is None or not 1 <= n <= N_SEQ:
                other.add(f"{key} {row.get('dataset')} {row.get('seq')}")
                continue
            if key not in keys:
                other.add(f"{key} {DATASET} (нема в methods.conf)")
                continue
            done.setdefault((key, n, r), set()).add(h["host"])
        for key, n, kind, r in h["markers"]:
            if key in keys and 1 <= n <= N_SEQ:
                (done if kind == "done" else bad).setdefault((key, n, r), set()).add(h["host"])

    host_info = {}
    for h in hosts:
        st, last = h["state"], h["last_report"]
        stale = last is None or (now - last).total_seconds() > stale_h * 3600
        busy = h["worker"] == "running" and not stale
        host_info[h["host"]] = {
            "ref": h["ref"],
            "last_report": last.isoformat() if last else None,
            "stale": stale,
            "worker": h["worker"],
            "busy": busy,
            "seq": st.get("seq") if busy else None,
            "method": st.get("method") if busy else None,
            "run": st.get("run") if busy else None,
            "since": st.get("since") if busy else None,
            "dir": st.get("dir"),
            "runs_done": 0,
        }

    sequences, total_done, total_need = [], 0, 0
    col_done = {k: 0 for k in keys}
    for n in range(1, N_SEQ + 1):
        cells = {}
        for key, runs in keys.items():
            by_run = {r: sorted(done.get((key, n, r), ())) for r in range(1, runs + 1)}
            by_run = {r: hs for r, hs in by_run.items() if hs}
            for hs in by_run.values():
                for host in hs:
                    host_info.setdefault(host, {"runs_done": 0})["runs_done"] += 1
            bad_runs = [r for r in range(1, runs + 1) if (key, n, r) in bad and r not in by_run]
            working = sorted(
                host
                for host, i in host_info.items()
                if i.get("busy") and i.get("seq") == seq_name(n) and state_matches(i["method"], key)
            )
            cells[key] = {
                "done": len(by_run),
                "needed": runs,
                "runs": {str(r): hs for r, hs in by_run.items()},
                "bad": bad_runs,
                "in_progress": working,
            }
            total_done += len(by_run)
            total_need += runs
            col_done[key] += len(by_run)
        sequences.append({"n": n, "seq": seq_name(n), "cells": cells})

    return {
        "dataset": DATASET,
        "total_done": total_done,
        "total_needed": total_need,
        "methods": [{"key": k, "method": m, "mode": md, "runs": r} for k, m, md, r in registry],
        "method_done": col_done,
        "hosts": host_info,
        "sequences": sequences,
        "other_results": sorted(other),
    }


# ----------------------------------------------------------------------------- rendering


def fmt_time(iso):
    t = parse_time(iso) if iso else None
    return t.strftime("%Y-%m-%d %H:%M %z") if t else "—"


def cell_text(c):
    hosts = {}
    for hs in c["runs"].values():
        for h in hs:
            hosts[h] = hosts.get(h, 0) + 1
    if len(hosts) == 1:
        who = next(iter(hosts))
    else:
        who = " + ".join(f"{h} {k}" for h, k in sorted(hosts.items()))
    parts = [("✅ " if c["done"] >= c["needed"] else "") + f"{c['done']}/{c['needed']}"]
    if who:
        parts.append(who)
    if c["bad"]:
        parts.append(f"✗{len(c['bad'])}")
    for h in c["in_progress"]:
        parts.append(f"⏳{h}")
    return " ".join(parts)


def host_state_text(i):
    if i.get("stale"):
        return "⚠ звітів нема понад поріг (ПК вимкнений або воркер завис)"
    if i.get("busy") and i.get("seq"):
        what = f"рахує {i['seq']}"
        if i.get("method"):
            what += f" · {i['method']}"
        if i.get("run"):
            what += f" · прогін {i['run']}"
        if i.get("since"):
            what += f" (з {i['since']})"
        return what
    if i.get("busy"):
        return "воркер працює, між сиквенсами"
    if i.get("worker") == "unknown":
        return "невідомо (нема status.txt)"
    return "не рахує"


def render_md(data, generated, stale_h, volatile=False):
    def vt(x):
        return VOLATILE if volatile else x

    total, need = data["total_done"], data["total_needed"]
    pct = f"{100 * total / need:.0f} %" if need else "—"
    keys = [m["key"] for m in data["methods"]]
    out = [
        "# Статус обчислень",
        "",
        f"Зроблено **{total} з {need}** прогонів ({pct}). Датасет `{data['dataset']}`, "
        f"{N_SEQ} сиквенсів, методи з `scripts/queue/methods.conf`.",
        "",
        "Зібрано автоматично з гілок `runner-<host>` "
        "(`scripts/report/build_status.py`, Action `status.yml`). Не редагувати вручну.",
        "",
        "## Зараз",
        "",
    ]
    if data["hosts"]:
        out += ["| ПК | стан | останній звіт | прогонів зробив |", "|---|---|---|---:|"]
        for h, i in sorted(data["hosts"].items()):
            last = vt(fmt_time(i.get("last_report")))
            out.append(f"| {h} | {host_state_text(i)} | {last} | {i.get('runs_done', 0)} |")
    else:
        out.append("Жодної гілки `runner-*` не знайдено.")
    out += ["", "## Сиквенси × методи", ""]
    heads = [f"{m['key']} (×{m['runs']})" for m in data["methods"]]
    out.append("| # | seq | " + " | ".join(heads) + " |")
    out.append("|---:|---|" + "---|" * len(keys))
    for s in data["sequences"]:
        out.append(
            f"| {s['n']} | {s['seq']} | "
            + " | ".join(cell_text(s["cells"][k]) for k in keys)
            + " |"
        )
    col_need = {m["key"]: m["runs"] * N_SEQ for m in data["methods"]}
    out.append(
        "|  | **разом** | "
        + " | ".join(f"**{data['method_done'][k]}/{col_need[k]}**" for k in keys)
        + " |"
    )
    out += [
        "",
        "Клітинка: готово/треба і хто рахував (кілька ПК: скільки прогонів кожен). "
        "✅ усе готово, ⏳ зараз рахується, ✗N — N прогонів позначені невдалими (`.bad_run`).",
        f"«Звітів нема»: гілка ПК не оновлювалась понад {stale_h:g} год.",
    ]
    if data["other_results"]:
        out += ["", "Інші результати на гілках (не входять у таблицю):"]
        out += [f"- `{x}`" for x in data["other_results"]]
    out += ["", f"_Зміст востаннє змінився: {vt(generated)}_", ""]
    return "\n".join(out)


def summary_copies(hosts):
    """{file name in status/summaries: text}"""
    out = {}
    for h in hosts:
        for stem, text in sorted(h["summaries"].items()):
            own = stem == f"summary_{h['host']}"
            out[f"summary_{h['host']}.csv" if own else f"summary_{h['host']}__{stem}.csv"] = text
    return out


def stable_json(data):
    d = json.loads(json.dumps(data))
    for i in d["hosts"].values():
        if "last_report" in i:
            i["last_report"] = VOLATILE
    return d


def content_hash(data, md_stable, copies):
    h = hashlib.sha256()
    h.update(json.dumps(stable_json(data), sort_keys=True, ensure_ascii=False).encode())
    h.update(md_stable.encode())
    for name, text in sorted(copies.items()):
        h.update(f"\0{name}\0".encode() + text.encode())
    return h.hexdigest()


# ----------------------------------------------------------------------------- main


def write_if_changed(out_dir, data, copies, now, stale_h):
    md_stable = render_md(data, None, stale_h, volatile=True)
    digest = content_hash(data, md_stable, copies)
    old = {}
    try:
        old = json.loads((out_dir / "status.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    if old.get("content_hash") == digest and (out_dir / "STATUS.md").is_file():
        return False
    generated = now.strftime("%Y-%m-%d %H:%M UTC")
    (out_dir / "summaries").mkdir(parents=True, exist_ok=True)
    payload = {"generated_at": now.isoformat(), "content_hash": digest, **data}
    (out_dir / "status.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n"
    )
    (out_dir / "STATUS.md").write_text(
        render_md(data, generated, stale_h), encoding="utf-8", newline="\n"
    )
    for p in (out_dir / "summaries").glob("summary_*.csv"):
        if p.name not in copies:
            p.unlink()
    for name, text in copies.items():
        (out_dir / "summaries" / name).write_text(text, encoding="utf-8", newline="\n")
    return True


def main(argv=None):
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--repo", default=".", help="git repository with the runner refs")
    ap.add_argument("--refs", action="append", help=f"ref glob (default {DEFAULT_REFS})")
    ap.add_argument(
        "--dir", action="append", default=[], metavar="HOST=PATH", help="dir, not ref"
    )
    ap.add_argument("--methods-conf", default=str(DEFAULT_CONF))
    ap.add_argument("--runs", type=int, default=DEFAULT_RUNS, help="default runs")
    ap.add_argument("--stale-h", type=float, default=DEFAULT_STALE_H, help="stale after, hours")
    ap.add_argument("--out", default="status")
    ap.add_argument("--now", help="ISO time instead of the clock (tests)")
    a = ap.parse_args(argv)

    now = parse_time(a.now) if a.now else datetime.now(timezone.utc)
    if now is None:
        raise SystemExit(f"bad --now: {a.now}")
    registry = load_registry(a.methods_conf, a.runs)
    sources = []
    for spec in a.dir:
        host, sep, path = spec.partition("=")
        if not sep or not host:
            raise SystemExit(f"--dir expects HOST=PATH, got {spec}")
        sources.append(DirSource(host, path))
    if a.refs or not a.dir:
        sources += git_sources(a.repo, a.refs or [DEFAULT_REFS])
    hosts = [read_host(s) for s in sources]
    data = build(registry, hosts, now, a.stale_h)
    changed = write_if_changed(Path(a.out), data, summary_copies(hosts), now, a.stale_h)
    names = ", ".join(h["host"] for h in hosts) or "none"
    print(
        f"{'written' if changed else 'unchanged'}: {a.out}/  "
        f"({data['total_done']}/{data['total_needed']} runs, hosts: {names})"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
