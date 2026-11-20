#!/usr/bin/env python3
"""
Start / stop / inspect the fleet of agy workers.

  fleet.py start --team [--skip-permissions]          # un agente dedicado por rol
  fleet.py start --workers 3 [--model gemini-3.8-flash-high] [--skip-permissions] [-- extra worker args]
  fleet.py status
  fleet.py stop
  fleet.py logs agy-1 [-n 50]

Each worker runs detached (own session) so it survives the shell that launched it.
Logs: data/logs/<name>.log   PIDs: data/fleet/<name>.pid  (relativo a la carpeta del tablero)
"""
import argparse
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
WORKER = Path(__file__).resolve().parent / "agy_worker.py"
LOG_DIR = BASE / "data" / "logs"
PID_DIR = BASE / "data" / "fleet"


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def running_workers():
    PID_DIR.mkdir(parents=True, exist_ok=True)
    out = {}
    for f in sorted(PID_DIR.glob("*.pid")):
        try:
            pid = int(f.read_text().strip())
        except ValueError:
            f.unlink(missing_ok=True)
            continue
        if alive(pid):
            out[f.stem] = pid
        else:
            f.unlink(missing_ok=True)
    return out


# One dedicated worker per specialised role (`fleet.py start --team`).
TEAM = {
    "agy-backend": "Backend",
    "agy-frontend": "Frontend",
    "agy-ux": "UX",
    "agy-qa": "QA",
    "agy-supervisor": "Supervisor",
    "agy-security": "Security",
    "agy-business": "BusinessLogic",
}


def launch(name, roles, args, extra):
    cmd = [sys.executable, str(WORKER), "--name", name, "--roles", roles, "--default-workdir", args.workdir]
    if args.model:
        cmd += ["--model", args.model]
    if args.skip_permissions:
        cmd.append("--skip-permissions")
    cmd += extra
    log = open(LOG_DIR / f"{name}.log", "a")
    proc = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                            start_new_session=True, cwd=str(BASE))
    (PID_DIR / f"{name}.pid").write_text(str(proc.pid))
    time.sleep(0.3)
    return proc.pid


def cmd_start(args, extra):
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    PID_DIR.mkdir(parents=True, exist_ok=True)
    current = running_workers()
    if args.team:
        for name, role in TEAM.items():
            if name in current:
                print(f"{name} already running")
                continue
            print(f"started {name} ({role}) pid={launch(name, role, args, extra)}")
        return
    started = []
    n = 0
    while len(started) < args.workers:
        n += 1
        name = f"{args.prefix}-{n}"
        if name in current:
            continue
        started.append((name, launch(name, args.roles, args, extra)))
    for name, pid in started:
        print(f"started {name} pid={pid} log={LOG_DIR / (name + '.log')}")
    if not started:
        print("no workers started")


def cmd_status(_args, _extra):
    workers = running_workers()
    if not workers:
        print("no workers running")
    for name, pid in workers.items():
        print(f"{name}\tpid={pid}\trunning")


def cmd_stop(args, _extra):
    workers = running_workers()
    targets = {k: v for k, v in workers.items() if not args.names or k in args.names}
    for name, pid in targets.items():
        os.kill(pid, signal.SIGTERM)  # worker requeues its task and reports 'stopped'
    deadline = time.time() + 15
    while time.time() < deadline and any(alive(p) for p in targets.values()):
        time.sleep(0.5)
    for name, pid in targets.items():
        if alive(pid):
            os.killpg(os.getpgid(pid), signal.SIGKILL)
        (PID_DIR / f"{name}.pid").unlink(missing_ok=True)
        print(f"stopped {name}")
    if not targets:
        print("no workers running")


def cmd_logs(args, _extra):
    path = LOG_DIR / f"{args.name}.log"
    if not path.exists():
        print(f"no log for {args.name}")
        return
    lines = path.read_text(errors="replace").splitlines()
    print("\n".join(lines[-args.n:]))


def main():
    argv = sys.argv[1:]
    extra = []
    if "--" in argv:
        i = argv.index("--")
        argv, extra = argv[:i], argv[i + 1:]

    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("start")
    s.add_argument("--workers", type=int, default=2)
    s.add_argument("--prefix", default="agy")
    s.add_argument("--roles", default=",".join(TEAM.values()))
    s.add_argument("--team", action="store_true", help="Un worker dedicado por rol (backend, frontend, ux, qa, supervisor, security, business)")
    s.add_argument("--model", default=None)
    s.add_argument("--workdir", default=os.environ.get("BOARD_WORKDIR", str(Path.home() / "agent_workspace")))
    s.add_argument("--skip-permissions", action="store_true")
    sub.add_parser("status")
    st = sub.add_parser("stop")
    st.add_argument("names", nargs="*")
    lg = sub.add_parser("logs")
    lg.add_argument("name")
    lg.add_argument("-n", type=int, default=60)
    args = p.parse_args(argv)
    {"start": cmd_start, "status": cmd_status, "stop": cmd_stop, "logs": cmd_logs}[args.cmd](args, extra)


if __name__ == "__main__":
    main()
