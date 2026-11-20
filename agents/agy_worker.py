#!/usr/bin/env python3
"""
Worker supervisor for Antigravity CLI (agy).

Loop until the board is 100 % done:
  heartbeat -> claim next ready task -> fetch context (dependency conclusions + related
  knowledge) -> run `agy -p` with stream-json -> mirror every tool call / message to the
  task terminal on the board -> store the structured result (summary, conclusions,
  decisions, gotchas, memories) in the knowledge base -> complete or block the task.

The worker (not the LLM) is responsible for claiming and completing tasks, so a
model that forgets to call a tool can never leave a card in a wrong column.
"""
import argparse
import json
import os
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import re
from pathlib import Path

DEFAULT_ROLES = "Backend,Frontend,UX,QA,Supervisor,Security,BusinessLogic"

RESULT_SCHEMA = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["done", "blocked"]},
        "summary": {"type": "string", "description": "Qué se hizo y cómo se verificó (3-6 frases)."},
        "conclusions": {"type": "array", "items": {"type": "string"},
                        "description": "Hechos concretos y reutilizables por otros agentes."},
        "decisions": {"type": "array", "items": {"type": "string"},
                      "description": "Decisiones técnicas tomadas y su porqué."},
        "gotchas": {"type": "array", "items": {"type": "string"},
                    "description": "Trampas, errores encontrados y cómo se resolvieron."},
        "memories": {"type": "array", "items": {"type": "string"},
                     "description": "Datos que conviene recordar en tareas futuras (rutas, comandos, puertos)."},
        "files_changed": {"type": "array", "items": {"type": "string"}},
        "commands_run": {"type": "array", "items": {"type": "string"}},
        "verification": {"type": "string", "description": "Cómo se comprobó que funciona."},
        "blocker_reason": {"type": "string", "description": "Solo si status=blocked."},
        "verdict": {"type": "string", "enum": ["approved", "rejected"],
                    "description": "Solo en auditorías del Supervisor."},
        "findings": {"type": "array", "items": {"type": "string"},
                     "description": "Auditorías: incumplimientos concretos y cómo corregirlos."}
    },
    "required": ["status", "summary", "conclusions"]
}

AGY_LOG_DIR = Path.home() / ".gemini" / "antigravity-cli" / "log"
MODEL_LABEL_RE = re.compile(r'label="([^"]+)"')


def model_label_for_conversation(conversation_id):
    """agy logs which model it propagates for each conversation; return its human label."""
    if not conversation_id or not AGY_LOG_DIR.exists():
        return None
    logs = sorted(AGY_LOG_DIR.glob("*.log"), key=lambda f: f.stat().st_mtime, reverse=True)[:15]
    for log in logs:
        text = log.read_text(errors="ignore")
        if conversation_id in text:
            labels = MODEL_LABEL_RE.findall(text)
            return labels[-1] if labels else None
    return None


def model_labels_by_id(agy_bin):
    """Map model ids (gemini-4-argon-medium) to labels (Gemini 4 Argon (Medium)) via `agy models`."""
    try:
        out = subprocess.run([agy_bin, "models"], capture_output=True, text=True, timeout=60).stdout
    except Exception:
        return {}
    return dict(line.split("\t", 1) for line in out.splitlines() if "\t" in line)


KNOWLEDGE_FIELDS = (("conclusions", "conclusion"), ("decisions", "decision"),
                    ("gotchas", "gotcha"), ("memories", "memory"))


class BoardClient:
    def __init__(self, api: str):
        self.api = api.rstrip("/")

    def _req(self, method: str, path: str, body=None, timeout=20):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(f"{self.api}{path}", data=data, method=method,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode()
            return json.loads(raw) if raw else None

    def get(self, path):
        return self._req("GET", path)

    def post(self, path, body=None):
        return self._req("POST", path, body if body is not None else {})


def short(value, limit=160):
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


class Worker:
    def __init__(self, args):
        self.args = args
        self.name = args.name
        self.roles = [r.strip() for r in args.roles.split(",") if r.strip()]
        self.board = BoardClient(args.api)
        self.status = "idle"
        self.current_task = None
        self.last_message = "Iniciando…"
        self.proc = None
        self.stopping = False
        self._lock = threading.Lock()
        self.role_catalog = {}
        self.model_label = model_labels_by_id(args.agy_bin).get(args.model) if args.model else None
        self._code_mtime = Path(__file__).stat().st_mtime

    # -- board helpers ------------------------------------------------
    def heartbeat(self, task_done=False):
        try:
            self.board.post("/agents/heartbeat", {
                "name": self.name, "kind": "agy", "status": self.status, "roles": self.roles,
                "model": self.model_label or self.args.model or "agy (default)", "current_task": self.current_task,
                "last_message": short(self.last_message, 240), "pid": os.getpid(),
                "task_done": task_done,
            })
        except Exception as exc:
            print(f"[{self.name}] heartbeat failed: {exc}", flush=True)

    def set_state(self, status=None, message=None, task=...):
        with self._lock:
            if status:
                self.status = status
            if message:
                self.last_message = message
            if task is not ...:
                self.current_task = task
        self.heartbeat()

    def log(self, task_id, message, level="info"):
        self.last_message = message
        try:
            self.board.post(f"/tasks/{task_id}/log", {"agent": self.name, "message": short(message, 600),
                                                      "level": level})
        except Exception as exc:
            print(f"[{self.name}] log failed: {exc}", flush=True)

    def _heartbeat_loop(self):
        while not self.stopping:
            time.sleep(self.args.heartbeat)
            self.heartbeat()

    # -- main loop ----------------------------------------------------
    def run(self):
        threading.Thread(target=self._heartbeat_loop, daemon=True).start()
        self.set_state("idle", f"Conectado. Roles: {', '.join(self.roles)}")
        while not self.stopping:
            self.reload_if_code_changed()
            try:
                state = self.board.get("/is-completed")
            except Exception as exc:
                self.set_state("error", f"Tablero inaccesible: {exc}")
                time.sleep(self.args.poll)
                continue

            if state["is_completed"]:
                self.set_state("finished", "Tablero 100 % completado. Fin del bucle.", None)
                print(f"[{self.name}] board complete, exiting", flush=True)
                return 0
            if state["total_tasks"] == 0:
                self.set_state("waiting", "Tablero vacío: esperando el plan del orquestador.", None)
                time.sleep(self.args.poll)
                continue

            try:
                claim = self.board.post(f"/tasks/claim-next?agent_name={self.name}&roles={','.join(self.roles)}")
            except Exception as exc:
                # The board may be restarting; never let a transient error kill the worker.
                self.set_state("error", f"No se pudo reclamar tarea: {exc}")
                time.sleep(self.args.poll)
                continue
            if not claim.get("claimed"):
                if state.get("review") and not state.get("todo") and not state.get("in_progress"):
                    msg = "Esperando al orquestador: hay tareas en revisión/bloqueadas."
                elif state.get("in_progress"):
                    msg = "Esperando que otros agentes terminen dependencias."
                else:
                    msg = "Sin tareas listas para mis roles."
                self.set_state("waiting", msg, None)
                time.sleep(self.args.poll)
                continue

            self.execute(claim["task"])
        return 0

    def reload_if_code_changed(self):
        """Between tasks only: if agy_worker.py was updated, restart this process with the same arguments."""
        if Path(__file__).stat().st_mtime == self._code_mtime:
            return
        self.set_state("idle", "Actualizando el código del worker…", None)
        self.stopping = True
        os.execv(sys.executable, [sys.executable, os.path.abspath(__file__), *sys.argv[1:]])

    def execute(self, task):
        task_id = task["id"]
        self.set_state("working", f"Reclamé {task_id}: {task['title']}", task_id)
        try:
            ctx = self.board.get(f"/tasks/{task_id}/context")
        except Exception:
            ctx = {"task": task, "dependency_knowledge": [], "related_knowledge": []}

        workdir = Path(task.get("workdir") or self.args.default_workdir).expanduser()
        workdir.mkdir(parents=True, exist_ok=True)
        dep_count = sum(len(d["knowledge"]) for d in ctx["dependency_knowledge"])
        self.log(task_id, f"Contexto cargado: {dep_count} entradas de dependencias, "
                          f"{len(ctx['related_knowledge'])} relacionadas. Directorio: {workdir}")

        inbox = self.fetch_inbox(task_id)
        if inbox:
            self.log(task_id, f"{len(inbox)} mensaje(s) recibidos de otros agentes.")
        try:
            self.role_catalog = self.board.get("/agents")
        except Exception:
            pass
        prompt = self.build_prompt(ctx, workdir, inbox)
        started = time.time()
        result, error = self.run_agy(task_id, prompt, workdir)
        duration = round(time.time() - started, 1)

        if self.stopping:
            return

        if result and result.get("status") == "done":
            knowledge = self.knowledge_from(result, task)
            artifacts = {
                "worker": self.name, "model": self.task_model or self.model_label, "duration_seconds": duration,
                "files_changed": result.get("files_changed", []),
                "commands_run": result.get("commands_run", []),
                "verification": result.get("verification", ""),
            }
            if result.get("verdict"):
                artifacts["verdict"] = result["verdict"]
                artifacts["findings"] = result.get("findings", [])
            # Requeue a rejected task BEFORE closing its audit, so dependents never see both 'done'.
            if "audit" in (task.get("tags") or []) and result.get("verdict") == "rejected":
                self.return_audited_task(task, result)
            self.board.post(f"/tasks/{task_id}/complete", {
                "agent": self.name, "summary": result.get("summary", ""), "status": "done",
                "artifacts": artifacts, "knowledge": knowledge,
            })
            self.last_message = f"{task_id} completada en {duration}s ({len(knowledge)} aprendizajes)."
            self.status = "idle"
            self.current_task = None
            self.heartbeat(task_done=True)
            return

        reason = (result or {}).get("blocker_reason") or error or "El agente no devolvió un resultado estructurado."
        if result:
            # Even a blocked attempt may have learned something useful.
            for entry in self.knowledge_from(result, task):
                try:
                    self.board.post("/knowledge", {**entry, "agent": self.name, "task_id": task_id,
                                                   "project": task.get("project")})
                except Exception:
                    pass
        self.board.post(f"/tasks/{task_id}/block", {"agent": self.name, "reason": short(reason, 800)})
        self.set_state("idle", f"{task_id} bloqueada: {short(reason, 120)}", None)

    def return_audited_task(self, audit_task, result):
        """Supervisor rejected the work: requeue the audited task with the findings appended
        and notify whoever did it."""
        target = next((t.split(":", 1)[1] for t in audit_task.get("tags", []) if t.startswith("audit-of:")), None)
        if not target:
            return
        original = self.board.get(f"/tasks/{target}")
        findings = "\n".join(f"- {f}" for f in result.get("findings") or [result.get("summary", "")])
        description = (f"{original['description']}\n\n## Correcciones solicitadas por el Supervisor "
                       f"({audit_task['id']})\n{findings}")
        self.board.post(f"/tasks/{target}/requeue", {
            "actor": self.name, "reason": f"Rechazada en auditoría {audit_task['id']}.", "description": description})
        for recipient in filter(None, {original.get("assignee"), original["agent"]}):
            self.board.post("/messages", {"sender": self.name, "recipient": recipient, "kind": "warning",
                                          "task_id": target, "body": f"Auditoría {audit_task['id']} rechazó {target}:\n{findings}"})
        self.log(audit_task["id"], f"{target} devuelta a Por hacer con {len(result.get('findings') or [])} hallazgos.",
                 "warning")

    def fetch_inbox(self, task_id):
        """Unread messages for this instance, its roles or everyone; marked read once delivered."""
        recipients = ",".join([self.name, *self.roles, "all"])
        try:
            msgs = self.board.get(f"/messages?recipient={recipients}&task_id={task_id}&unread=true&limit=30")
            if msgs:
                self.board.post("/messages/read", {"ids": [m["id"] for m in msgs]})
            return list(reversed(msgs))
        except Exception:
            return []

    @staticmethod
    def knowledge_from(result, task):
        entries = []
        for field, kind in KNOWLEDGE_FIELDS:
            for item in result.get(field) or []:
                if item and item.strip():
                    entries.append({"kind": kind, "title": f"[{task['id']}] {task['title']}",
                                    "content": item.strip(), "tags": task.get("tags", [])})
        return entries

    def build_prompt(self, ctx, workdir, inbox=None):
        t = ctx["task"]
        guide = (self.role_catalog.get(t["agent"]) or {}).get("guide", "")
        lines = [
            f"Eres {self.name}, un agente programador de una flota coordinada por un orquestador a través "
            f"del Agentic Board ({self.board.api}). Rol para esta tarea: {t['agent']}.",
            f"Trabaja ÚNICAMENTE en la tarea {t['id']}.",
            "",
            "## Tu rol",
            guide or f"Rol: {t['agent']}.",
            "",
            f"## Tarea {t['id']}: {t['title']}",
            f"Proyecto: {t.get('project') or '-'}",
            f"Directorio de trabajo: {workdir}",
            f"Etiquetas: {', '.join(t.get('tags') or []) or '-'}",
            "",
            "### Descripción",
            t["description"],
        ]
        if t.get("acceptance"):
            lines += ["", "### Criterios de aceptación", t["acceptance"]]

        if ctx["dependency_knowledge"]:
            lines += ["", "## Lo que concluyeron las tareas de las que dependes"]
            for dep in ctx["dependency_knowledge"]:
                lines.append(f"### [{dep['task_id']}] {dep['title']} ({dep['status']})")
                if dep.get("artifacts"):
                    lines.append(f"Artefactos: {short(dep['artifacts'], 600)}")
                for k in dep["knowledge"][:15]:
                    lines.append(f"- ({k['kind']}) {short(k['content'], 700)}")
        if ctx["related_knowledge"]:
            lines += ["", "## Conocimiento relacionado (de tareas o proyectos anteriores)"]
            for k in ctx["related_knowledge"]:
                lines.append(f"- ({k['kind']}, {k.get('task_id') or '-'}) {k['title']}: {short(k['content'], 500)}")

        if inbox:
            lines += ["", "## Mensajes recibidos (de otros agentes u orquestador)"]
            for m in inbox:
                lines.append(f"- [{m['kind']}] de {m['sender']}: {short(m['body'], 600)}")

        api = self.board.api
        lines += [
            "",
            "## Comunicación con el tablero y otros agentes (API HTTP)",
            f"- Avance visible para el operador: curl -s -X POST {api}/tasks/{t['id']}/log "
            f"-H 'Content-Type: application/json' -d '{{\"agent\":\"{self.name}\",\"message\":\"...\"}}'",
            f"- Enviar mensaje a otro agente, rol, 'orchestrator' o 'all': curl -s -X POST {api}/messages "
            f"-H 'Content-Type: application/json' -d '{{\"sender\":\"{self.name}\",\"recipient\":\"Tester\","
            f"\"kind\":\"handoff\",\"task_id\":\"{t['id']}\",\"body\":\"...\"}}'",
            f"- Leer tus mensajes: curl -s '{api}/messages?recipient={self.name},{','.join(self.roles)},all&unread=true'",
            f"- Consultar conocimiento: curl -s '{api}/knowledge?q=<palabras>'  · estado: curl -s {api}/overview",
            "- Usa mensajes 'handoff' para avisar a quien continúe tu trabajo de algo que no cabe en las conclusiones, "
            "y 'question' al orquestador si una decisión no te corresponde.",
        ]

        lines += [
            "",
            "## Reglas",
            "1. Completa la tarea de punta a punta: implementa, ejecuta y VERIFICA (tests, lint, curl, "
            "ejecución real) antes de declarar status=done.",
            f"2. Para hitos importantes puedes usar la herramienta MCP agentic_board → "
            f"board_update_task_progress (task_id={t['id']}, agent_name={self.name}). "
            "NO llames board_claim_next_task ni board_complete_task: el supervisor lo hace por ti.",
            f"3. No modifiques archivos fuera de {workdir} salvo que la tarea lo pida explícitamente.",
            "4. Si no puedes terminar (falta información, error externo, credenciales, dependencia rota), "
            "devuelve status=\"blocked\" con un blocker_reason concreto y accionable.",
            "5. Tu respuesta final debe cumplir el esquema JSON pedido. conclusions, decisions, gotchas y "
            "memories se guardan en la base de conocimiento que leerán otros agentes: escribe hechos "
            "concretos y reutilizables (rutas de archivos, comandos, endpoints, puertos, decisiones y su "
            "porqué, errores encontrados y su solución). No repitas la descripción de la tarea.",
        ]
        return "\n".join(lines)

    def run_agy(self, task_id, prompt, workdir):
        cmd = [self.args.agy_bin, "-p", prompt, "--output-format", "stream-json",
               "--json-schema", json.dumps(RESULT_SCHEMA)]
        if self.args.model:
            cmd += ["--model", self.args.model]
        if self.args.effort:
            cmd += ["--effort", self.args.effort]
        if self.args.skip_permissions:
            cmd.append("--dangerously-skip-permissions")
        elif self.args.mode:
            cmd += ["--mode", self.args.mode]

        self.task_model = None
        self.log(task_id, f"Lanzando agy en {workdir}")
        try:
            self.proc = subprocess.Popen(cmd, cwd=str(workdir), stdout=subprocess.PIPE,
                                         stderr=subprocess.STDOUT, text=True, bufsize=1)
        except FileNotFoundError:
            return None, f"No se encontró el ejecutable '{self.args.agy_bin}'."

        timer = threading.Timer(self.args.task_timeout, self._kill_proc)
        timer.start()
        structured, final_status, seen_tools, text_buf = None, None, set(), {}
        conversation_id, model_checked_at = None, 0.0
        tail = []
        try:
            for line in self.proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    evt = json.loads(line)
                except json.JSONDecodeError:
                    tail = (tail + [line])[-20:]
                    continue

                conversation_id = conversation_id or evt.get("conversation_id") or \
                    (evt.get("step_update") or {}).get("conversation_id")
                if conversation_id and not self.task_model and time.time() - model_checked_at > 3:
                    model_checked_at = time.time()
                    label = model_label_for_conversation(conversation_id)
                    if label:
                        self.task_model = self.model_label = label
                        self.log(task_id, f"Modelo: {label}")
                        self.heartbeat()

                if evt.get("event") == "step_update":
                    st = evt["step_update"]
                    idx = st.get("step_index")
                    if st.get("step_type") == "tool" and st.get("tool_name") and idx not in seen_tools:
                        seen_tools.add(idx)
                        if st["tool_name"] != "finish":
                            params = (st.get("tool_info") or {}).get("parameters") or {}
                            self.log(task_id, f"Herramienta {st['tool_name']} {short(params, 220)}")
                    elif st.get("step_type") == "agent_response":
                        text_buf[idx] = text_buf.get(idx, "") + (st.get("text_delta") or "")
                        if st.get("state") == "DONE":
                            text = text_buf.pop(idx, "").strip()
                            if text and not text.startswith("{"):
                                self.log(task_id, f"Agente: {short(text, 400)}")
                elif evt.get("event") == "result":
                    res = evt.get("result") or {}
                    structured = res.get("structured_output")
                    final_status = res.get("status")
        finally:
            timer.cancel()
            self.proc.wait()
            code = self.proc.returncode
            self.proc = None

        if structured:
            return structured, None
        if code != 0:
            return None, f"agy terminó con código {code} (estado {final_status}). Últimas líneas: {short(' | '.join(tail), 500)}"
        return None, f"agy terminó (estado {final_status}) sin salida estructurada."

    def _kill_proc(self):
        if self.proc and self.proc.poll() is None:
            if self.current_task:
                self.log(self.current_task, f"Tiempo límite ({self.args.task_timeout}s) excedido; se detiene agy.",
                         "error")
            self.proc.terminate()

    def shutdown(self, *_):
        """SIGTERM/SIGINT: stop agy and give the task back so another worker can retry it."""
        if self.stopping:
            return
        self.stopping = True
        task_id = self.current_task
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
        if task_id:
            try:
                self.board.post(f"/tasks/{task_id}/requeue",
                                {"actor": self.name, "reason": f"{self.name} se detuvo antes de terminar."})
            except Exception:
                pass
        self.status, self.current_task, self.last_message = "stopped", None, "Detenido."
        self.heartbeat()
        sys.exit(0)


def main():
    p = argparse.ArgumentParser(description="Agentic Board worker for Antigravity CLI (agy)")
    p.add_argument("--name", required=True, help="Nombre único del worker, ej. agy-1")
    p.add_argument("--roles", default=DEFAULT_ROLES, help="Roles que acepta (coma)")
    p.add_argument("--api", default=os.environ.get("BOARD_API", "http://localhost:1000/api"))
    p.add_argument("--model", default=os.environ.get("AGY_MODEL"), help="Modelo de agy (ver `agy models`)")
    p.add_argument("--effort", default=None, choices=[None, "low", "medium", "high", "xhigh", "max"])
    p.add_argument("--mode", default="auto", help="Modo de agy si no se omiten permisos (auto, accept-edits)")
    p.add_argument("--skip-permissions", action="store_true",
                   help="Pasa --dangerously-skip-permissions a agy (autonomía total; úsalo solo si lo autorizas)")
    p.add_argument("--default-workdir", default=os.environ.get("BOARD_WORKDIR", str(Path.home() / "agent_workspace")))
    p.add_argument("--agy-bin", default="agy")
    p.add_argument("--poll", type=float, default=5.0)
    p.add_argument("--heartbeat", type=float, default=10.0)
    p.add_argument("--task-timeout", type=int, default=3600)
    args = p.parse_args()

    worker = Worker(args)
    signal.signal(signal.SIGTERM, worker.shutdown)
    signal.signal(signal.SIGINT, worker.shutdown)
    sys.exit(worker.run())


if __name__ == "__main__":
    main()
