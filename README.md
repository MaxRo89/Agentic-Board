# ⚡ Agentic Board & Orchestration Hub (Trello-like AI Coordination)

Un tablero Kanban interactivo diseñado como **punto de referencia único (Single Source of Truth)** para la orquestación de agentes de IA y supervisión en tiempo real por el operador humano.

Las tareas no se asignan a personas, sino a **roles de agentes especializados de IA** (`@Orchestrator`, `@Architect`, `@Coder`, `@Tester`, `@Reviewer`, `@DevOps`), coordinados mediante un servicio **MCP (Model Context Protocol)** y una API de alta concurrencia construida con **Flask y FastAPI corriendo unificados en el puerto 1000**.

---

## 🌟 Características Principales

1. **Interfaz Gráfica del Operador (Web UI en Puerto 1000)**:
   - Diseño moderno con **Dark Mode y Glassmorphism**.
   - 4 Columnas Kanban: **Por Hacer**, **En Ejecución**, **Revisión / Bloqueadas**, **Completadas**.
   - **Drag & Drop** nativo HTML5 para mover tarjetas entre columnas.
   - **Server-Sent Events (SSE)** en tiempo real: el tablero se actualiza instantáneamente cuando un agente reclama, reporta avances o finaliza una tarea sin recargar la página.
   - **Terminal de Ejecución**: Cada tarjeta cuenta con una consola interactiva con timestamps y logs emitidos por los agentes.
   - **Gestión de Dependencias**: Tareas bloqueadas visualmente hasta que sus predecesoras pasen a `done`.
   - **Simulador de Agente Integrado**: Botón para probar la autonomía de los agentes desde la UI.

2. **Arquitectura Híbrida Flask + FastAPI**:
   - **FastAPI**: Maneja el enrutamiento ASGI de alto rendimiento, los endpoints REST `/api/...`, el streaming SSE `/api/events`, la serialización Pydantic y la documentación Swagger OpenAPI en `/docs`.
   - **Flask**: Montado como WSGI Middleware (`a2wsgi`), gestiona la interfaz gráfica del operador, plantillas Jinja2 y rutas de operador.
   - Todo servido de forma unificada bajo un único proceso en el puerto `1000`.

3. **Servicio MCP Completo (Model Context Protocol)**:
   - Servidor ejecutable por stdio: `mcp_server.py`.
   - Pasarela HTTP MCP: `http://localhost:1000/api/mcp/tools` y `http://localhost:1000/api/mcp/call`.
   - Herramientas para que el **Orquestador** diseñe planes y para que los **Agentes Trabajadores** ejecuten su bucle autónomo sin detenerse hasta completar el tablero.

4. **Skill de Antigravity Incluida**:
   - Plantillas en `skills/agy/agentic-board/` (workers agy) y `skills/claude/board-orchestrator/` (Claude Code); `scripts/install.sh` las instala.

---

## 🚀 Instalación local

Requisitos: Python 3.11+, y para la flota [Antigravity CLI](https://antigravity.google) (`agy`) y Claude Code.

```bash
git clone git@github.com:MaxRo89/Agentic-Board.git
cd Agentic-Board
scripts/install.sh                 # dependencias, skills y MCP de agy
python3 run.py                     # o: nohup python3 run.py > data/logs/server.log 2>&1 &
```

- Tablero: [http://localhost:1000](http://localhost:1000) · Swagger: [http://localhost:1000/docs](http://localhost:1000/docs)
- Si lo abres desde otra máquina, instala las skills con su URL: `scripts/install.sh http://IP:1000`.
- Configuración por variables de entorno: ver `.env.example` (`PORT`, `BOARD_API`, `BOARD_DATA_DIR`, `BOARD_WORKDIR`, `AUTO_AUDIT`).
- `data/` (SQLite, logs, PIDs) y `plans/` son locales y no se versionan; hay un plan de ejemplo en `plans/examples/`.

### Usarlo en otro proyecto

1. Escribe un plan (ver `plans/examples/ejemplo-plan.json`) con `workdir` apuntando a tu proyecto.
2. `python3 agents/boardctl.py plan mi-plan.json`
3. `python3 agents/fleet.py start --team` (un worker agy por rol) y sigue el avance en el navegador.
4. O pídele a Claude Code "orquesta esto con el tablero" para que use la skill `board-orchestrator`.

> `agy -p` en modo no interactivo rechaza las confirmaciones de herramientas; para que la flota avance sin intervención usa `fleet.py start --skip-permissions` solo en entornos de confianza.

---

## 🛠️ Herramientas MCP Disponibles

1. `board_get_overview`: Resumen global del tablero (% de progreso, tareas por columna, agentes activos, estado de finalización).
2. `board_list_tasks`: Lista tareas con filtros por columna (`status`) o por agente (`agent_name`).
3. `board_get_task_details`: Detalle completo, dependencias, artefactos y logs de terminal.
4. `board_create_task`: Agrega una tarea asignada a un agente con prioridad y dependencias.
5. `board_create_project_plan`: Crea un plan estructurado de múltiples tareas en lote.
6. `board_claim_next_task`: Reclama automáticamente la siguiente tarea disponible cuyas dependencias ya estén terminadas.
7. `board_update_task_progress`: Emite logs de avance en vivo a la terminal de la tarjeta.
8. `board_complete_task`: Finaliza la tarea, adjunta artefactos/entregables y desbloquea tareas sucesoras.
9. `board_report_blocker`: Reporta errores o bloqueos y envía la tarjeta a revisión para el operador.
10. `board_is_workflow_complete`: Chequeo booleano que permite a los agentes saber si deben seguir o finalizar su bucle.

---

## 🤖 El Bucle Autónomo de los Agentes ("Non-Stop Loop")

Los agentes de IA utilizan este patrón para no detenerse hasta completar todo el tablero:

```python
import time
import requests

AGENT = "Coder"
API = "http://localhost:1000/api"

while not requests.get(f"{API}/is-completed").json()["is_completed"]:
    claim = requests.post(f"{API}/tasks/claim-next?agent_name={AGENT}").json()
    if claim["claimed"]:
        task = claim["task"]
        # 1. Registrar inicio
        requests.post(f"{API}/tasks/{task['id']}/log", json={"agent": AGENT, "message": "Iniciando tarea..."})
        
        # 2. Ejecutar trabajo
        # ...
        
        # 3. Completar y entregar artefactos
        requests.post(f"{API}/tasks/{task['id']}/complete", json={
            "agent": AGENT,
            "summary": "Tarea finalizada con éxito.",
            "status": "done"
        })
    else:
        time.sleep(2) # Espera breve si depende de tareas de otros agentes
```

---

## 🧠 Modo orquestado: Claude (cerebro) + flota agy (manos)

A partir de la v2 el tablero funciona así:

| Componente | Rol |
|---|---|
| **Claude Code** con la skill `board-orchestrator` (`~/.claude/skills/board-orchestrator/SKILL.md`) | Planifica, crea tareas con dependencias, lanza la flota, revisa, desbloquea y reencola hasta que el tablero llega al 100 % |
| `agents/agy_worker.py` | Supervisor de un agente `agy`: reclama, inyecta contexto y conocimiento, retransmite cada paso al tablero y guarda conclusiones |
| `agents/fleet.py` | `start --workers N`, `status`, `stop`, `logs agy-1` |
| `agents/boardctl.py` | CLI del orquestador: `status`, `plan`, `task`, `kb`, `note`, `activity`, `requeue`, `complete`, `watch` |

### Novedades en la UI
- **Agentes en ejecución:** una tarjeta por worker con su estado (trabajando, esperando, sin señal…), la tarea actual y la última acción en vivo.
- **Feed del orquestador:** planificación, bloqueos, reencolados y cierres.
- **📚 Conocimiento:** buscador de conclusiones, decisiones, gotchas y memorias. Se conserva al vaciar el tablero.
- En cada tarjeta se ven la instancia que la ejecuta (`⚙ agy-2`) y cuántos aprendizajes dejó (`📚 n`).

### Nuevos endpoints
`GET /api/tasks/{id}/context`, `POST /api/tasks/{id}/requeue`, `POST /api/tasks/{id}/block` (body JSON),
`GET|POST /api/knowledge`, `POST /api/agents/heartbeat`, `GET /api/agents/live`, `GET|POST /api/activity`.
`claim-next` acepta `roles=` y guarda la instancia en `assignee`; `complete` acepta `knowledge[]`;
`batch-plan` acepta `workdir` y dependencias `"#n"`. `reset` ya no vuelve a sembrar el ejemplo por defecto.
