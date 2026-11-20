#!/usr/bin/env python3
"""
Orchestrator CLI for the Agentic Board (used by Claude as the planner/brain).

  boardctl.py status                       # columns, tasks and live agents
  boardctl.py plan plan.json               # create a batch plan (deps may be "#1", "#2"...)
  boardctl.py task TASK-03 [-n 30]         # task detail + last log lines
  boardctl.py context TASK-03              # what a worker will receive
  boardctl.py kb [query] [--project P] [--task T] [--kind K]
  boardctl.py note "texto" --kind decision --title "..." [--task T] [--project P]
  boardctl.py activity "mensaje" [--level info|warning|error|success] [--task T]
  boardctl.py requeue TASK-03 --reason "..." [--description-file f.md]
  boardctl.py complete TASK-03 --summary "..."      # close a task the orchestrator did itself
  boardctl.py msg agy-1 "texto" [--kind question] [--task T]   # mensaje a instancia, rol o 'all'
  boardctl.py inbox [--all]                # mensajes dirigidos al orquestador
  boardctl.py reset                        # empty the board (knowledge is kept)
  boardctl.py watch [--timeout 1500]       # block until something needs the orchestrator
"""
import argparse
import json
import os
import sys
import time
import urllib.request

API = os.environ.get("BOARD_API", "http://localhost:1000/api").rstrip("/")
ORCH_NAME = os.environ.get("ORCHESTRATOR_NAME", "claude-orquestador")
ALIVE_STATES = {"working", "waiting", "idle"}


def req(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(f"{API}{path}", data=data, method=method,
                               headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(r, timeout=30) as resp:
        raw = resp.read().decode()
        return json.loads(raw) if raw else None


def get(path):
    return req("GET", path)


def post(path, body=None):
    return req("POST", path, body if body is not None else {})


def orchestrator_heartbeat(status, message, task=None):
    try:
        post("/agents/heartbeat", {"name": ORCH_NAME, "kind": "orchestrator", "status": status,
                                   "roles": ["Orchestrator"], "model": "Claude Code",
                                   "current_task": task, "last_message": message, "pid": os.getpid()})
    except Exception:
        pass


def fmt_task(t):
    lock = " 🔒" if t.get("is_blocked") and t["status"] == "todo" else ""
    who = f" ⚙{t['assignee']}" if t.get("assignee") else ""
    kb = f" 📚{t['knowledge_count']}" if t.get("knowledge_count") else ""
    deps = f" deps={','.join(t['dependencies'])}" if t.get("dependencies") else ""
    return f"  {t['id']} [{t['priority']}] @{t['agent']}{who}{kb}{lock}{deps} — {t['title']}"


def cmd_status(_a):
    ov = get("/overview")
    print(f"Progreso {ov['progress_percentage']}%  total={ov['total_tasks']} todo={ov['todo_count']} "
          f"in_progress={ov['in_progress_count']} review={ov['review_count']} done={ov['done_count']} "
          f"complete={ov['is_completed']}")
    for col in ("review", "in_progress", "todo", "done"):
        tasks = ov["tasks_by_column"][col]
        if tasks:
            print(f"\n[{col}]")
            for t in tasks:
                print(fmt_task(t))
                if col == "review" and t.get("logs"):
                    print(f"      último log: {t['logs'][-1]['message'][:300]}")
    agents = get("/agents/live")
    if agents:
        print("\n[agentes]")
        for a in agents:
            print(f"  {a['name']:<20} {a['status']:<9} task={a.get('current_task') or '-':<9} "
                  f"done={a['tasks_done']} · {(a.get('last_message') or '')[:120]}")


def cmd_plan(a):
    src = sys.stdin.read() if a.file == "-" else open(a.file).read()
    plan = json.loads(src)
    res = post("/tasks/batch-plan", plan)
    for t in res["created_tasks"]:
        print(fmt_task(t))


def cmd_task(a):
    t = get(f"/tasks/{a.task_id}")
    print(json.dumps({k: v for k, v in t.items() if k != "logs"}, indent=2, ensure_ascii=False))
    print("\n--- logs ---")
    for l in t["logs"][-a.n:]:
        print(f"{time.strftime('%H:%M:%S', time.localtime(l['timestamp']))} {l['agent']}: {l['message']}")
    kb = get(f"/knowledge?task_id={a.task_id}")
    if kb:
        print("\n--- knowledge ---")
        for k in kb:
            print(f"({k['kind']}) {k['content']}")


def cmd_context(a):
    print(json.dumps(get(f"/tasks/{a.task_id}/context"), indent=2, ensure_ascii=False))


def cmd_kb(a):
    from urllib.parse import urlencode
    params = {k: v for k, v in {"q": a.query, "project": a.project, "task_id": a.task,
                                 "kind": a.kind, "limit": a.limit}.items() if v}
    for k in get(f"/knowledge?{urlencode(params)}"):
        print(f"#{k['id']} ({k['kind']}) {k.get('task_id') or '-'} · {k.get('project') or '-'} · {k['agent']}\n"
              f"   {k['title']}\n   {k['content'][:800]}\n")


def cmd_note(a):
    print(post("/knowledge", {"agent": ORCH_NAME, "kind": a.kind, "title": a.title, "content": a.text,
                              "task_id": a.task, "project": a.project, "tags": a.tags or []}))


def cmd_activity(a):
    post("/activity", {"actor": ORCH_NAME, "message": a.message, "level": a.level, "task_id": a.task})
    print("ok")


def cmd_requeue(a):
    desc = open(a.description_file).read() if a.description_file else None
    res = post(f"/tasks/{a.task_id}/requeue", {"actor": ORCH_NAME, "reason": a.reason, "description": desc})
    print(fmt_task(res["task"]))


def cmd_complete(a):
    res = post(f"/tasks/{a.task_id}/complete", {"agent": ORCH_NAME, "summary": a.summary, "status": "done"})
    print(fmt_task(res["task"]))


def cmd_msg(a):
    m = post("/messages", {"sender": ORCH_NAME, "recipient": a.recipient, "body": a.body,
                           "kind": a.kind, "task_id": a.task})
    print(f"#{m['id']} → {m['recipient']}")


def cmd_inbox(a):
    msgs = get(f"/messages?recipient=orchestrator,Orchestrator,{ORCH_NAME}&unread={'false' if a.all else 'true'}")
    for m in reversed(msgs):
        print(f"#{m['id']} [{m['kind']}] {m['sender']} → {m['recipient']} ({m.get('task_id') or '-'}): {m['body']}")
    if msgs and not a.all:
        post("/messages/read", {"ids": [m["id"] for m in msgs]})


def cmd_reset(_a):
    print(post("/board/reset?reseed=false"))


def cmd_watch(a):
    """Block until the orchestrator must act, then print a report and exit.
    Reasons: complete | review | message | orchestrator_task | orphaned | fleet_down | timeout."""
    start = time.time()
    seen_done = {t["id"] for t in get("/overview")["tasks_by_column"]["done"]}
    orchestrator_heartbeat("working", "Monitoreando la flota…")
    reason, details = "timeout", []
    while True:
        ov = get("/overview")
        agents = {x["name"]: x for x in get("/agents/live") if x["kind"] != "orchestrator"}
        cols = ov["tasks_by_column"]
        pending = ov["todo_count"] + ov["in_progress_count"]

        if ov["is_completed"]:
            reason = "complete"
            break
        if cols["review"]:
            reason = "review"
            details = [fmt_task(t) + f"\n      motivo: {t['logs'][-1]['message'][:400] if t.get('logs') else '-'}"
                       for t in cols["review"]]
            break
        questions = get(f"/messages?recipient=orchestrator,Orchestrator,{ORCH_NAME}&unread=true&limit=20")
        if questions:
            reason = "message"
            details = [f"#{m['id']} [{m['kind']}] {m['sender']} ({m.get('task_id') or '-'}): {m['body'][:400]}"
                       for m in reversed(questions)]
            break
        ready_orch = [t for t in cols["todo"] if t["agent"].lower() == "orchestrator" and not t["is_blocked"]]
        if ready_orch:
            reason = "orchestrator_task"
            details = [fmt_task(t) for t in ready_orch]
            break
        orphaned = [t for t in cols["in_progress"]
                    if t.get("assignee") in agents and agents[t["assignee"]]["status"] not in ALIVE_STATES]
        if orphaned:
            reason = "orphaned"
            details = [fmt_task(t) for t in orphaned]
            break
        if pending and not any(x["status"] in ALIVE_STATES for x in agents.values()):
            reason = "fleet_down"
            details = [f"{n}: {x['status']}" for n, x in agents.items()] or ["no hay workers registrados"]
            break
        if time.time() - start > a.timeout:
            break
        orchestrator_heartbeat("working", f"Monitoreando: {ov['done_count']}/{ov['total_tasks']} hechas, "
                                          f"{ov['in_progress_count']} en ejecución")
        time.sleep(a.interval)

    newly_done = [t for t in get("/overview")["tasks_by_column"]["done"] if t["id"] not in seen_done]
    orchestrator_heartbeat("idle" if reason != "complete" else "finished", f"Atención requerida: {reason}")
    print(f"REASON: {reason}")
    for d in details:
        print(d)
    if newly_done:
        print("\nNEWLY DONE (revisar conclusiones):")
        for t in newly_done:
            print(fmt_task(t))
            for k in get(f"/knowledge?task_id={t['id']}&kind=summary"):
                print(f"      resumen: {k['content'][:500]}")
    print()
    cmd_status(a)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    s = sub.add_parser("plan"); s.add_argument("file")
    s = sub.add_parser("task"); s.add_argument("task_id"); s.add_argument("-n", type=int, default=30)
    s = sub.add_parser("context"); s.add_argument("task_id")
    s = sub.add_parser("kb"); s.add_argument("query", nargs="?")
    s.add_argument("--project"); s.add_argument("--task"); s.add_argument("--kind"); s.add_argument("--limit", type=int, default=30)
    s = sub.add_parser("note"); s.add_argument("text"); s.add_argument("--title", required=True)
    s.add_argument("--kind", default="note"); s.add_argument("--task"); s.add_argument("--project"); s.add_argument("--tags", nargs="*")
    s = sub.add_parser("activity"); s.add_argument("message"); s.add_argument("--level", default="info"); s.add_argument("--task")
    s = sub.add_parser("requeue"); s.add_argument("task_id"); s.add_argument("--reason", required=True); s.add_argument("--description-file")
    s = sub.add_parser("complete"); s.add_argument("task_id"); s.add_argument("--summary", required=True)
    s = sub.add_parser("msg"); s.add_argument("recipient"); s.add_argument("body")
    s.add_argument("--kind", default="info"); s.add_argument("--task")
    s = sub.add_parser("inbox"); s.add_argument("--all", action="store_true")
    sub.add_parser("reset")
    s = sub.add_parser("watch"); s.add_argument("--timeout", type=int, default=1500); s.add_argument("--interval", type=int, default=10)
    a = p.parse_args()
    globals()[f"cmd_{a.cmd}"](a)


if __name__ == "__main__":
    main()
