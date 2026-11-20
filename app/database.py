import sqlite3
import json
import re
import time
from typing import List, Dict, Any, Optional
from pathlib import Path
from app.config import (DB_PATH, DEFAULT_AGENTS, AGENT_STALE_SECONDS, AUTO_AUDIT,
                        AUDIT_EXEMPT_ROLES, ROLE_ALIASES)

from app.realtime import broadcast_event

KNOWLEDGE_KINDS = ("conclusion", "note", "memory", "decision", "gotcha", "summary")

class DatabaseManager:
    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path
        self.init_db()

    def get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=15.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        return conn

    def init_db(self):
        with self.get_connection() as conn:
            conn.executescript("""
            CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                description TEXT NOT NULL,
                agent TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'todo',
                priority TEXT NOT NULL DEFAULT 'medium',
                dependencies_json TEXT NOT NULL DEFAULT '[]',
                tags_json TEXT NOT NULL DEFAULT '[]',
                artifacts_json TEXT NOT NULL DEFAULT '{}',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                started_at REAL,
                completed_at REAL
            );

            CREATE TABLE IF NOT EXISTS task_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT NOT NULL,
                agent TEXT NOT NULL,
                message TEXT NOT NULL,
                level TEXT NOT NULL DEFAULT 'info',
                timestamp REAL NOT NULL,
                FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE
            );

            -- Knowledge base: survives board resets so later projects can reuse it.
            CREATE TABLE IF NOT EXISTS knowledge (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT,
                project TEXT,
                agent TEXT NOT NULL,
                kind TEXT NOT NULL DEFAULT 'note',
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                tags_json TEXT NOT NULL DEFAULT '[]',
                created_at REAL NOT NULL
            );

            -- Live state of each worker process (agy-1, agy-2, ...) and the orchestrator.
            CREATE TABLE IF NOT EXISTS agents_runtime (
                name TEXT PRIMARY KEY,
                kind TEXT NOT NULL DEFAULT 'agy',
                roles_json TEXT NOT NULL DEFAULT '[]',
                model TEXT,
                status TEXT NOT NULL DEFAULT 'idle',
                current_task TEXT,
                last_message TEXT,
                pid INTEGER,
                tasks_done INTEGER NOT NULL DEFAULT 0,
                started_at REAL,
                last_heartbeat REAL
            );

            -- Orchestrator / fleet activity feed (planning, assignment, reviews).
            CREATE TABLE IF NOT EXISTS activity (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                actor TEXT NOT NULL,
                message TEXT NOT NULL,
                level TEXT NOT NULL DEFAULT 'info',
                task_id TEXT,
                timestamp REAL NOT NULL
            );

            -- Agent-to-agent / orchestrator messages exchanged through the API.
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sender TEXT NOT NULL,
                recipient TEXT NOT NULL,
                task_id TEXT,
                kind TEXT NOT NULL DEFAULT 'info',
                body TEXT NOT NULL,
                created_at REAL NOT NULL,
                read_at REAL
            );

            CREATE INDEX IF NOT EXISTS idx_messages_recipient ON messages(recipient, read_at);
            CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
            CREATE INDEX IF NOT EXISTS idx_tasks_agent ON tasks(agent);
            CREATE INDEX IF NOT EXISTS idx_logs_task_id ON task_logs(task_id);
            CREATE INDEX IF NOT EXISTS idx_knowledge_task ON knowledge(task_id);
            CREATE INDEX IF NOT EXISTS idx_knowledge_project ON knowledge(project);
            CREATE INDEX IF NOT EXISTS idx_activity_ts ON activity(timestamp);
            """)
            existing = {r["name"] for r in conn.execute("PRAGMA table_info(tasks)")}
            for col, ddl in (
                ("project", "ALTER TABLE tasks ADD COLUMN project TEXT"),
                ("workdir", "ALTER TABLE tasks ADD COLUMN workdir TEXT"),
                ("assignee", "ALTER TABLE tasks ADD COLUMN assignee TEXT"),
                ("acceptance", "ALTER TABLE tasks ADD COLUMN acceptance TEXT"),
                ("attempts", "ALTER TABLE tasks ADD COLUMN attempts INTEGER NOT NULL DEFAULT 0"),
            ):
                if col not in existing:
                    conn.execute(ddl)
            conn.commit()

    def count_tasks(self) -> int:
        with self.get_connection() as conn:
            return conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]

    def _next_task_id(self, conn: sqlite3.Connection) -> str:
        max_num = 0
        for r in conn.execute("SELECT id FROM tasks WHERE id LIKE 'TASK-%'"):
            try:
                max_num = max(max_num, int(r["id"].split("-")[1]))
            except Exception:
                pass
        return f"TASK-{max_num + 1:02d}"

    # ------------------------------------------------------------------
    # Tasks
    # ------------------------------------------------------------------
    def create_task(self, title: str, description: str, agent: str,
                    priority: str = "medium", dependencies: List[str] = None,
                    tags: List[str] = None, task_id: str = None,
                    project: str = None, workdir: str = None,
                    acceptance: str = None) -> Dict[str, Any]:
        now = time.time()
        dependencies = dependencies or []
        tags = tags or []

        with self.get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            task_id = task_id or self._next_task_id(conn)
            conn.execute("""
                INSERT INTO tasks (id, title, description, agent, status, priority,
                                  dependencies_json, tags_json, artifacts_json, created_at, updated_at,
                                  project, workdir, acceptance)
                VALUES (?, ?, ?, ?, 'todo', ?, ?, ?, '{}', ?, ?, ?, ?, ?)
            """, (task_id, title, description, agent, priority,
                  json.dumps(dependencies), json.dumps(tags), now, now,
                  project, workdir, acceptance))
            conn.execute("""
                INSERT INTO task_logs (task_id, agent, message, level, timestamp)
                VALUES (?, 'Sistema', 'Tarea registrada en el tablero.', 'info', ?)
            """, (task_id, now))
            conn.commit()

        task = self.get_task(task_id)
        broadcast_event("task_created", self._card(task))
        return task

    def create_plan(self, project: str, tasks: List[Dict[str, Any]], workdir: Optional[str] = None,
                    actor: str = "Orchestrator") -> List[Dict[str, Any]]:
        """Create several tasks at once. Dependencies may point to real IDs ('TASK-03')
        or to earlier items of the same plan by 1-based position ('#1')."""
        created: List[Dict[str, Any]] = []
        for item in tasks:
            deps = []
            for dep in item.get("dependencies", []) or []:
                dep = str(dep).strip()
                if dep.startswith("#") and dep[1:].isdigit() and 0 < int(dep[1:]) <= len(created):
                    deps.append(created[int(dep[1:]) - 1]["id"])
                else:
                    deps.append(dep)
            created.append(self.create_task(
                title=item["title"], description=item["description"],
                agent=item.get("agent") or item.get("agent_name") or "any",
                priority=item.get("priority", "medium"), dependencies=deps,
                tags=item.get("tags", []), project=item.get("project") or project,
                workdir=item.get("workdir") or workdir, acceptance=item.get("acceptance")))
        self.add_activity(actor, f"Planificó '{project}' con {len(created)} tareas: "
                          + ", ".join(t["id"] for t in created), "info")
        return created

    @staticmethod
    def _card(task: Dict[str, Any]) -> Dict[str, Any]:
        """Lightweight task for real-time events: the board only shows the log tail."""
        return {**task, "logs": task.get("logs", [])[-5:]}

    def _row_to_task(self, row: sqlite3.Row) -> Dict[str, Any]:
        task = dict(row)
        task["dependencies"] = json.loads(task.pop("dependencies_json"))
        task["tags"] = json.loads(task.pop("tags_json"))
        task["artifacts"] = json.loads(task.pop("artifacts_json"))
        return task

    def get_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        with self.get_connection() as conn:
            row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
            if not row:
                return None
            task = self._row_to_task(row)
            task["logs"] = [dict(r) for r in conn.execute(
                "SELECT agent, message, level, timestamp FROM task_logs WHERE task_id = ? ORDER BY timestamp ASC",
                (task_id,))]

            dep_statuses = {}
            if task["dependencies"]:
                placeholders = ",".join("?" for _ in task["dependencies"])
                dep_statuses = {r["id"]: r["status"] for r in conn.execute(
                    f"SELECT id, status FROM tasks WHERE id IN ({placeholders})", task["dependencies"])}
            task["dependencies_status"] = {d: dep_statuses.get(d, "missing") for d in task["dependencies"]}
            task["is_blocked"] = any(dep_statuses.get(dep) != "done" for dep in task["dependencies"])
            task["knowledge_count"] = conn.execute(
                "SELECT COUNT(*) FROM knowledge WHERE task_id = ?", (task_id,)).fetchone()[0]
            return task

    def list_tasks(self, status: Optional[str] = None, agent: Optional[str] = None,
                   project: Optional[str] = None, include_logs: bool = True) -> List[Dict[str, Any]]:
        with self.get_connection() as conn:
            query = "SELECT * FROM tasks WHERE 1=1"
            params = []
            if status:
                query += " AND status = ?"
                params.append(status)
            if agent and agent.lower() != "all":
                query += " AND (LOWER(agent) = LOWER(?) OR LOWER(COALESCE(assignee,'')) = LOWER(?))"
                params.extend([agent, agent])
            if project:
                query += " AND project = ?"
                params.append(project)
            query += (" ORDER BY CASE priority WHEN 'critical' THEN 1 WHEN 'high' THEN 2 "
                      "WHEN 'medium' THEN 3 WHEN 'low' THEN 4 ELSE 5 END, created_at ASC")
            rows = conn.execute(query, params).fetchall()

            all_logs: Dict[str, List[Dict[str, Any]]] = {}
            if include_logs:
                for log_row in conn.execute(
                        "SELECT task_id, agent, message, level, timestamp FROM task_logs ORDER BY timestamp ASC"):
                    all_logs.setdefault(log_row["task_id"], []).append({
                        "agent": log_row["agent"], "message": log_row["message"],
                        "level": log_row["level"], "timestamp": log_row["timestamp"]})

            status_map = {r["id"]: r["status"] for r in conn.execute("SELECT id, status FROM tasks")}
            kb_counts = {r["task_id"]: r["c"] for r in conn.execute(
                "SELECT task_id, COUNT(*) AS c FROM knowledge WHERE task_id IS NOT NULL GROUP BY task_id")}

            tasks = []
            for r in rows:
                t = self._row_to_task(r)
                # Cards only need the tail of the log; the modal fetches the full task.
                t["logs"] = all_logs.get(t["id"], [])[-5:]
                t["dependencies_status"] = {dep: status_map.get(dep, "missing") for dep in t["dependencies"]}
                t["is_blocked"] = any(status_map.get(dep) != "done" for dep in t["dependencies"])
                t["knowledge_count"] = kb_counts.get(t["id"], 0)
                tasks.append(t)
            return tasks

    def update_task(self, task_id: str, fields: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        valid_columns = {"title", "description", "agent", "status", "priority", "started_at",
                         "completed_at", "project", "workdir", "assignee", "acceptance", "attempts"}
        updates = []
        params = []

        for k, v in fields.items():
            if k in valid_columns:
                updates.append(f"{k} = ?")
                params.append(v)
            elif k == "dependencies":
                updates.append("dependencies_json = ?")
                params.append(json.dumps(v))
            elif k == "tags":
                updates.append("tags_json = ?")
                params.append(json.dumps(v))
            elif k == "artifacts":
                updates.append("artifacts_json = ?")
                params.append(json.dumps(v))

        if not updates:
            return self.get_task(task_id)

        updates.append("updated_at = ?")
        params.append(time.time())
        params.append(task_id)

        with self.get_connection() as conn:
            conn.execute(f"UPDATE tasks SET {', '.join(updates)} WHERE id = ?", params)
            conn.commit()

        updated_task = self.get_task(task_id)
        if updated_task:
            broadcast_event("task_updated", self._card(updated_task))
        return updated_task

    def add_log(self, task_id: str, agent: str, message: str, level: str = "info") -> bool:
        now = time.time()
        with self.get_connection() as conn:
            if not conn.execute("SELECT id FROM tasks WHERE id = ?", (task_id,)).fetchone():
                return False
            conn.execute("""
                INSERT INTO task_logs (task_id, agent, message, level, timestamp)
                VALUES (?, ?, ?, ?, ?)
            """, (task_id, agent, message, level, now))
            conn.execute("UPDATE tasks SET updated_at = ? WHERE id = ?", (now, task_id))
            conn.commit()

        broadcast_event("log_added", {"task_id": task_id, "agent": agent, "message": message,
                                      "level": level, "timestamp": now})
        return True

    def claim_next_task(self, agent_name: str, roles: Optional[List[str]] = None) -> Optional[Dict[str, Any]]:
        """
        Atomically claim the next ready task for a worker.
        A task matches when its `agent` equals the worker name, one of the worker's roles,
        or 'any'/'unassigned'; all dependencies must be 'done'.
        The role stays in `agent`; the worker instance is stored in `assignee`.
        """
        accepted = {agent_name.lower(), "any", "unassigned"}
        accepted.update(r.lower() for r in (roles or []))

        with self.get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            status_map = {r["id"]: r["status"] for r in conn.execute("SELECT id, status FROM tasks")}
            rows = conn.execute("""
                SELECT id, agent, dependencies_json FROM tasks WHERE status = 'todo'
                ORDER BY CASE priority WHEN 'critical' THEN 1 WHEN 'high' THEN 2
                         WHEN 'medium' THEN 3 WHEN 'low' THEN 4 ELSE 5 END, created_at ASC
            """).fetchall()
            candidate_id = None
            for r in rows:
                role = ROLE_ALIASES.get(r["agent"], r["agent"])
                if role.lower() not in accepted and r["agent"].lower() not in accepted:
                    continue
                deps = json.loads(r["dependencies_json"])
                if all(status_map.get(d) == "done" for d in deps):
                    candidate_id = r["id"]
                    break
            if not candidate_id:
                conn.rollback()
                return None
            now = time.time()
            conn.execute("""
                UPDATE tasks SET status = 'in_progress', assignee = ?, started_at = ?,
                       updated_at = ?, attempts = attempts + 1
                WHERE id = ? AND status = 'todo'
            """, (agent_name, now, now, candidate_id))
            conn.commit()

        self.add_log(candidate_id, agent_name, f"Tarea reclamada por {agent_name}.", "info")
        task = self.get_task(candidate_id)
        broadcast_event("task_updated", self._card(task))
        return task

    def complete_task(self, task_id: str, agent: str, summary: str,
                      artifacts: Optional[Dict[str, Any]] = None, status: str = "done",
                      knowledge: Optional[List[Dict[str, Any]]] = None) -> Optional[Dict[str, Any]]:
        task = self.get_task(task_id)
        if not task:
            return None

        existing_artifacts = task.get("artifacts", {})
        if artifacts:
            existing_artifacts.update(artifacts)

        self.update_task(task_id, {
            "status": status,
            "completed_at": time.time() if status == "done" else None,
            "artifacts": existing_artifacts
        })

        # The summary is always recorded as the task's conclusion so the KB never misses a task.
        if summary:
            self.add_knowledge(agent=agent, title=f"[{task_id}] {task['title']}", content=summary,
                               kind="summary", task_id=task_id, project=task.get("project"),
                               tags=task.get("tags", []))
        for entry in knowledge or []:
            if not entry.get("content"):
                continue
            self.add_knowledge(agent=agent, title=entry.get("title") or task["title"],
                               content=entry["content"], kind=entry.get("kind", "note"),
                               task_id=task_id, project=task.get("project"),
                               tags=entry.get("tags") or task.get("tags", []))

        if status == "done":
            self._create_audit_task(task, summary, existing_artifacts)

        self.add_log(task_id, agent, f"Tarea marcada como '{status}'. Resumen: {summary}",
                     "success" if status == "done" else "warning")
        return self.get_task(task_id)

    def _create_audit_task(self, task: Dict[str, Any], summary: str, artifacts: Dict[str, Any]):
        """Every finished task gets a Supervisor audit. Tasks that depend on it also wait
        for the audit, so nobody builds on work the Supervisor has not approved."""
        role = ROLE_ALIASES.get(task["agent"], task["agent"])
        if not AUTO_AUDIT or role in AUDIT_EXEMPT_ROLES or "audit" in task.get("tags", []):
            return
        description = (
            f"Audita la tarea {task['id']} ({role}) terminada por {task.get('assignee') or '-'}.\n\n"
            f"### Lo que se pidió\n{task['description']}\n\n"
            f"### Criterios de aceptación\n{task.get('acceptance') or '(no definidos: evalúa contra la descripción)'}\n\n"
            f"### Lo que el agente dice que hizo\n{summary}\n\n"
            f"### Artefactos reportados\n{json.dumps(artifacts, ensure_ascii=False)[:3000]}"
        )
        audit = self.create_task(
            title=f"Auditoría de {task['id']}: {task['title']}", description=description, agent="Supervisor",
            priority="high", dependencies=[task["id"]], tags=["audit", f"audit-of:{task['id']}"],
            project=task.get("project"), workdir=task.get("workdir"),
            acceptance="Veredicto approved o rejected con evidencia verificable (comandos ejecutados y resultados).")
        with self.get_connection() as conn:
            rows = conn.execute("SELECT id, dependencies_json FROM tasks WHERE status = 'todo'").fetchall()
        for r in rows:
            deps = json.loads(r["dependencies_json"])
            if task["id"] in deps and audit["id"] not in deps and r["id"] != audit["id"]:
                self.update_task(r["id"], {"dependencies": deps + [audit["id"]]})
        self.add_activity("Supervisor", f"Auditoría {audit['id']} creada para {task['id']}.", "info", task["id"])

    def block_task(self, task_id: str, agent: str, reason: str) -> Optional[Dict[str, Any]]:
        if not self.get_task(task_id):
            return None
        self.update_task(task_id, {"status": "review"})
        self.add_log(task_id, agent, f"Bloqueo: {reason}", "error")
        self.add_activity(agent, f"Bloqueo en {task_id}: {reason}", "error", task_id)
        return self.get_task(task_id)

    def requeue_task(self, task_id: str, actor: str, reason: str = "",
                     description: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Send a task back to 'todo' (optionally with corrected instructions)."""
        if not self.get_task(task_id):
            return None
        fields: Dict[str, Any] = {"status": "todo", "assignee": None, "started_at": None, "completed_at": None}
        if description:
            fields["description"] = description
        self.update_task(task_id, fields)
        self.add_log(task_id, actor, f"Reencolada. {reason}".strip(), "warning")
        self.add_activity(actor, f"Reencoló {task_id}. {reason}".strip(), "warning", task_id)
        return self.get_task(task_id)

    def delete_task(self, task_id: str) -> bool:
        with self.get_connection() as conn:
            cursor = conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
            conn.commit()
            deleted = cursor.rowcount > 0
        if deleted:
            broadcast_event("task_deleted", {"task_id": task_id})
        return deleted

    def reset_board(self, reseed: bool = False):
        """Clear tasks and logs. The knowledge base and activity history are kept."""
        with self.get_connection() as conn:
            conn.executescript("""
                DELETE FROM task_logs;
                DELETE FROM tasks;
                UPDATE agents_runtime SET current_task = NULL;
            """)
            conn.commit()
        if reseed:
            self.seed_sample_workflow()
        self.add_activity("System", "Tablero restablecido (la base de conocimiento se conserva).", "warning")
        broadcast_event("board_reset", {"message": "Board was reset."})

    def get_overview(self) -> Dict[str, Any]:
        tasks = self.list_tasks()
        total = len(tasks)
        columns = {"todo": [], "in_progress": [], "review": [], "done": []}
        active_agents = set()

        for t in tasks:
            col = t["status"]
            if col in columns:
                columns[col].append(t)
            if t["status"] == "in_progress":
                active_agents.add(t.get("assignee") or t["agent"])

        done_count = len(columns["done"])
        progress = (done_count / total * 100.0) if total > 0 else 100.0
        is_completed = total > 0 and (done_count == total)

        return {
            "total_tasks": total,
            "todo_count": len(columns["todo"]),
            "in_progress_count": len(columns["in_progress"]),
            "review_count": len(columns["review"]),
            "done_count": done_count,
            "progress_percentage": round(progress, 1),
            "is_completed": is_completed,
            "active_agents": sorted(active_agents),
            "tasks_by_column": columns,
            "projects": sorted({t["project"] for t in tasks if t.get("project")}),
        }

    # ------------------------------------------------------------------
    # Knowledge base
    # ------------------------------------------------------------------
    def add_knowledge(self, agent: str, title: str, content: str, kind: str = "note",
                      task_id: Optional[str] = None, project: Optional[str] = None,
                      tags: Optional[List[str]] = None) -> Dict[str, Any]:
        kind = kind if kind in KNOWLEDGE_KINDS else "note"
        now = time.time()
        with self.get_connection() as conn:
            cur = conn.execute("""
                INSERT INTO knowledge (task_id, project, agent, kind, title, content, tags_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (task_id, project, agent, kind, title, content, json.dumps(tags or []), now))
            conn.commit()
            entry_id = cur.lastrowid
        entry = {"id": entry_id, "task_id": task_id, "project": project, "agent": agent, "kind": kind,
                 "title": title, "content": content, "tags": tags or [], "created_at": now}
        broadcast_event("knowledge_added", entry)
        return entry

    def search_knowledge(self, query: Optional[str] = None, project: Optional[str] = None,
                         task_id: Optional[str] = None, kind: Optional[str] = None,
                         tags: Optional[List[str]] = None, limit: int = 50) -> List[Dict[str, Any]]:
        sql = "SELECT * FROM knowledge WHERE 1=1"
        params: List[Any] = []
        if project:
            sql += " AND project = ?"
            params.append(project)
        if task_id:
            sql += " AND task_id = ?"
            params.append(task_id)
        if kind:
            sql += " AND kind = ?"
            params.append(kind)
        sql += " ORDER BY created_at DESC"
        with self.get_connection() as conn:
            rows = [dict(r) for r in conn.execute(sql, params)]
        for r in rows:
            r["tags"] = json.loads(r.pop("tags_json"))

        words = [w for w in re.findall(r"\w+", (query or "").lower()) if len(w) > 2]
        wanted_tags = {t.lower() for t in (tags or [])}
        if not words and not wanted_tags:
            return rows[:limit]

        scored = []
        for r in rows:
            haystack = f"{r['title']} {r['content']} {' '.join(r['tags'])}".lower()
            score = sum(haystack.count(w) for w in words)
            score += 3 * len(wanted_tags & {t.lower() for t in r["tags"]})
            if score:
                scored.append((score, r))
        scored.sort(key=lambda x: (-x[0], -x[1]["created_at"]))
        return [r for _, r in scored[:limit]]

    def get_task_context(self, task_id: str) -> Optional[Dict[str, Any]]:
        """Everything a worker needs before starting: the task, what its dependencies
        concluded, and related knowledge from earlier tasks/projects."""
        task = self.get_task(task_id)
        if not task:
            return None
        dependency_knowledge = []
        for dep in task["dependencies"]:
            dep_task = self.get_task(dep)
            if dep_task:
                dependency_knowledge.append({
                    "task_id": dep, "title": dep_task["title"], "status": dep_task["status"],
                    "artifacts": dep_task["artifacts"],
                    "knowledge": self.search_knowledge(task_id=dep, limit=20),
                })
        seen = {k["id"] for d in dependency_knowledge for k in d["knowledge"]}
        related = [k for k in self.search_knowledge(query=f"{task['title']} {task['description']}",
                                                    tags=task["tags"], limit=25)
                   if k["id"] not in seen and k.get("task_id") != task_id][:12]
        task_view = {k: v for k, v in task.items() if k != "logs"}
        return {"task": task_view, "dependency_knowledge": dependency_knowledge, "related_knowledge": related}

    # ------------------------------------------------------------------
    # Agents runtime & activity feed
    # ------------------------------------------------------------------
    def agent_heartbeat(self, name: str, status: str, kind: str = "agy",
                        roles: Optional[List[str]] = None, model: Optional[str] = None,
                        current_task: Optional[str] = None, last_message: Optional[str] = None,
                        pid: Optional[int] = None, task_done: bool = False) -> Dict[str, Any]:
        now = time.time()
        with self.get_connection() as conn:
            row = conn.execute("SELECT * FROM agents_runtime WHERE name = ?", (name,)).fetchone()
            if row is None:
                conn.execute("""
                    INSERT INTO agents_runtime (name, kind, roles_json, model, status, current_task,
                                                last_message, pid, tasks_done, started_at, last_heartbeat)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
                """, (name, kind, json.dumps(roles or []), model, status, current_task,
                      last_message, pid, now, now))
            else:
                conn.execute("""
                    UPDATE agents_runtime SET kind = ?, roles_json = COALESCE(?, roles_json),
                           model = COALESCE(?, model), status = ?, current_task = ?,
                           last_message = COALESCE(?, last_message), pid = COALESCE(?, pid),
                           tasks_done = tasks_done + ?, last_heartbeat = ?
                    WHERE name = ?
                """, (kind, json.dumps(roles) if roles is not None else None, model, status,
                      current_task, last_message, pid, 1 if task_done else 0, now, name))
            conn.commit()
        agent = self.get_agent(name)
        broadcast_event("agent_updated", agent)
        return agent

    def _agent_row(self, row: sqlite3.Row) -> Dict[str, Any]:
        a = dict(row)
        a["roles"] = json.loads(a.pop("roles_json"))
        stale = (time.time() - (a["last_heartbeat"] or 0)) > AGENT_STALE_SECONDS
        if stale and a["status"] not in ("stopped", "finished"):
            a["status"] = "offline"
        if a["kind"] == "agy" and a.get("model") in (None, "", "agy (default)"):
            a["model"] = self._agy_default_model() or a.get("model")
        return a

    def get_agent(self, name: str) -> Optional[Dict[str, Any]]:
        with self.get_connection() as conn:
            row = conn.execute("SELECT * FROM agents_runtime WHERE name = ?", (name,)).fetchone()
        return self._agent_row(row) if row else None

    _default_model_cache: Dict[str, Any] = {"label": None, "at": 0.0}

    @classmethod
    def _agy_default_model(cls) -> Optional[str]:
        """Model agy uses when no --model is given, read from its newest log (cached 60 s)."""
        cache = cls._default_model_cache
        if time.time() - cache["at"] < 60:
            return cache["label"]
        cache["at"] = time.time()
        log_dir = Path.home() / ".gemini" / "antigravity-cli" / "log"
        try:
            logs = sorted(log_dir.glob("*.log"), key=lambda f: f.stat().st_mtime, reverse=True)[:5]
            for log in logs:
                labels = re.findall(r'label="([^"]+)"', log.read_text(errors="ignore"))
                if labels:
                    cache["label"] = labels[-1]
                    break
        except OSError:
            pass
        return cache["label"]

    def list_live_agents(self) -> List[Dict[str, Any]]:
        with self.get_connection() as conn:
            rows = conn.execute("SELECT * FROM agents_runtime ORDER BY kind DESC, name ASC").fetchall()
        agents = [self._agent_row(r) for r in rows]
        titles = {t["id"]: t["title"] for t in self.list_tasks(include_logs=False)}
        for a in agents:
            a["current_task_title"] = titles.get(a["current_task"]) if a["current_task"] else None

        return agents

    def remove_agent(self, name: str) -> bool:
        with self.get_connection() as conn:
            cur = conn.execute("DELETE FROM agents_runtime WHERE name = ?", (name,))
            conn.commit()
        broadcast_event("agent_removed", {"name": name})
        return cur.rowcount > 0

    def add_activity(self, actor: str, message: str, level: str = "info",
                     task_id: Optional[str] = None) -> Dict[str, Any]:
        now = time.time()
        with self.get_connection() as conn:
            cur = conn.execute(
                "INSERT INTO activity (actor, message, level, task_id, timestamp) VALUES (?, ?, ?, ?, ?)",
                (actor, message, level, task_id, now))
            conn.commit()
        entry = {"id": cur.lastrowid, "actor": actor, "message": message, "level": level,
                 "task_id": task_id, "timestamp": now}
        broadcast_event("activity_added", entry)
        return entry

    def list_activity(self, limit: int = 100) -> List[Dict[str, Any]]:
        with self.get_connection() as conn:
            rows = conn.execute("SELECT * FROM activity ORDER BY timestamp DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Messages between agents
    # ------------------------------------------------------------------
    def send_message(self, sender: str, recipient: str, body: str, kind: str = "info",
                     task_id: Optional[str] = None) -> Dict[str, Any]:
        """recipient: an instance name (agy-2), a role (Tester), 'orchestrator' or 'all'."""
        now = time.time()
        with self.get_connection() as conn:
            cur = conn.execute(
                "INSERT INTO messages (sender, recipient, task_id, kind, body, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (sender, recipient, task_id, kind, body, now))
            conn.commit()
        msg = {"id": cur.lastrowid, "sender": sender, "recipient": recipient, "task_id": task_id,
               "kind": kind, "body": body, "created_at": now, "read_at": None}
        broadcast_event("message_added", msg)
        return msg

    def list_messages(self, recipients: Optional[List[str]] = None, task_id: Optional[str] = None,
                      unread_only: bool = False, since_id: int = 0, limit: int = 100) -> List[Dict[str, Any]]:
        sql = "SELECT * FROM messages WHERE id > ?"
        params: List[Any] = [since_id]
        if recipients:
            names = [r.lower() for r in recipients]
            sql += f" AND LOWER(recipient) IN ({','.join('?' for _ in names)})"
            params.extend(names)
        if task_id:
            sql += " AND (task_id = ? OR task_id IS NULL)"
            params.append(task_id)
        if unread_only:
            sql += " AND read_at IS NULL"
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        with self.get_connection() as conn:
            return [dict(r) for r in conn.execute(sql, params)]

    def mark_messages_read(self, ids: List[int]) -> int:
        if not ids:
            return 0
        with self.get_connection() as conn:
            cur = conn.execute(
                f"UPDATE messages SET read_at = ? WHERE id IN ({','.join('?' for _ in ids)}) AND read_at IS NULL",
                [time.time(), *ids])
            conn.commit()
        return cur.rowcount

    def seed_sample_workflow(self):
        """Seed a small demo workflow (only used on explicit reset with reseed=true)."""
        self.create_task(
            title="Arquitectura y Especificación de Sistema",
            description="Definir el modelo de datos, endpoints y estructura modular.",
            agent="Architect", priority="critical", tags=["architecture", "demo"], task_id="TASK-01")
        self.create_task(
            title="Implementación del backend",
            description="Implementar los endpoints definidos en TASK-01.",
            agent="Coder", priority="high", dependencies=["TASK-01"], tags=["backend", "demo"], task_id="TASK-02")
        self.create_task(
            title="Pruebas de integración",
            description="Escribir y ejecutar pruebas de los endpoints de TASK-02.",
            agent="Tester", priority="medium", dependencies=["TASK-02"], tags=["qa", "demo"], task_id="TASK-03")

# Global singleton
db = DatabaseManager()
