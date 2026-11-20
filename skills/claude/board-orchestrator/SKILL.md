---
name: board-orchestrator
description: >-
  Claude actúa como cerebro/orquestador del Agentic Board (__BOARD_URL__):
  planifica el trabajo, lo descompone en tareas con dependencias, lanza una flota de agentes
  Antigravity CLI (agy) que programan, monitorea, revisa, desbloquea y no termina hasta que el
  tablero esté 100 % completado. Usa y alimenta la base de conocimiento del tablero.
  Úsala cuando el usuario pida orquestar, repartir o ejecutar trabajo "con el tablero",
  "con los agentes", "con agy" o un proyecto de varias tareas que deban hacer agentes.
---

# Orquestador del Agentic Board

Tú (Claude) eres el **cerebro**. Los agentes `agy` son las **manos**. El tablero es la **única
fuente de verdad** y la **memoria compartida**. El usuario observa todo en vivo en
`__BOARD_URL__`: columnas Kanban, una tarjeta por agente con lo que está haciendo
en ese momento, el feed de actividad del orquestador y la base de conocimiento (botón 📚).

| Pieza | Ruta |
|---|---|
| Servidor (FastAPI + Flask, puerto 1000) | `__BOARD_DIR__/run.py`, log en `__BOARD_DIR__/data/logs/server.log` |
| CLI del orquestador | `__BOARD_DIR__/agents/boardctl.py` |
| Lanzador de la flota | `__BOARD_DIR__/agents/fleet.py` |
| Worker (envuelve `agy -p`) | `__BOARD_DIR__/agents/agy_worker.py` |
| API / Swagger | `__BOARD_URL__/api` · `/docs` |
| MCP para agy | `__BOARD_DIR__/mcp_server.py` (ya registrado en agy como `agentic_board`) |

En adelante `bc` = `__BOARD_DIR__/agents/boardctl.py` y `fleet` = `__BOARD_DIR__/agents/fleet.py`.

## Equipo de agentes (roles)

| Rol | Worker (`fleet start --team`) | Responsabilidad |
|---|---|---|
| `Orchestrator` | tú (Claude) | Planifica, reparte, desbloquea y cierra. Ningún worker toma estas tareas. |
| `Backend` | `agy-backend` | API, modelos, servicios, migraciones y pruebas de servidor |
| `Frontend` | `agy-frontend` | Componentes, estado, integración con la API y build |
| `UX` | `agy-ux` | Solo experiencia de usuario: flujos, accesibilidad, consistencia visual, textos |
| `QA` | `agy-qa` | Pruebas unitarias, de integración y E2E; reproduce defectos |
| `Supervisor` | `agy-supervisor` | Audita cada tarea terminada contra lo solicitado; aprueba o la devuelve |
| `Security` | `agy-security` | OWASP, autenticación, secretos, dependencias (solo entorno local) |
| `BusinessLogic` | `agy-business` | Intenta romper las reglas del negocio y deja pruebas que lo reproduzcan |

Las guías de cada rol (lo que se inyecta en el prompt) están en `__BOARD_DIR__/app/config.py` (`DEFAULT_AGENTS`).
Los roles antiguos (`Architect`, `Coder`, `Tester`, `Reviewer`, `DevOps`) se traducen solos (`ROLE_ALIASES`).

**Supervisión automática:** cuando un worker termina una tarea, el tablero crea "Auditoría de TASK-x"
para el `Supervisor`, y las tareas que dependen de TASK-x también esperan esa auditoría. Si el
Supervisor la rechaza, TASK-x vuelve a "Por hacer" con los hallazgos en la descripción y su
responsable recibe un mensaje. No crees tareas de auditoría a mano. Para desactivarla: `AUTO_AUDIT=0`.

## Reparto de responsabilidades

- **Tú:** entiendes el objetivo, consultas el conocimiento previo, planificas, asignas, revisas
  resultados, desbloqueas, reencolas, haces las tareas `@Orchestrator` y cierras el proyecto.
  No programas las tareas de la flota tú mismo (salvo arreglos mínimos para desbloquear).
- **Comunicación:** todo pasa por la API HTTP del tablero. Los agentes se escriben con
  `POST /api/messages` (a una instancia, un rol, `orchestrator` o `all`) y los mensajes no leídos
  se inyectan en el prompt de su siguiente tarea. Tú usas `bc msg` y `bc inbox`.
- **Tiempo real:** la web recibe cada cambio por WebSocket (`/api/ws`, con reanudación por `seq`;
  SSE como respaldo). El MCP de `agy` reenvía sus llamadas a la API, así que también se ve en vivo.
- **Workers `agy-N`:** reclaman tareas listas (dependencias en `done`), reciben en su prompt las
  conclusiones de sus dependencias y el conocimiento relacionado, ejecutan, verifican y devuelven
  un JSON con `summary`, `conclusions`, `decisions`, `gotchas` y `memories`. El worker (no el
  modelo) guarda ese conocimiento y mueve la tarjeta a `done` o a `review` si se bloquea.
- **Tareas `@Orchestrator`:** los workers nunca las toman. Úsalas para puntos de control:
  revisión intermedia, decisiones que requieren al usuario o integración final.

## Procedimiento

### 0. Verificar el tablero
```bash
curl -sf __BOARD_URL__/operator/health || (cd __BOARD_DIR__ && nohup python3 run.py > data/logs/server.log 2>&1 &)
bc status
```
Si el tablero tiene tareas de otro proyecto sin terminar, pregunta al usuario antes de usar `bc reset`.
El reset borra tareas y logs, pero **conserva la base de conocimiento**.

### 1. Entender y consultar la memoria
- Lee el código y los documentos relevantes del proyecto objetivo.
- Busca conocimiento previo: `bc kb "<palabras clave>"` (y `--project <nombre>`). Copia a las
  descripciones de las tareas lo que aplique: rutas, comandos, decisiones y trampas conocidas.

### 2. Planificar
Escribe el plan en un JSON y créalo con `bc plan plan.json`:
```json
{
  "project_name": "mi-proyecto",
  "workdir": "/ruta/a/mi-proyecto",
  "tasks": [
    {"title": "...", "agent": "Architect", "priority": "high", "tags": ["auth"],
     "description": "...", "acceptance": "..."},
    {"title": "...", "agent": "Coder", "dependencies": ["#1"], "description": "...", "acceptance": "..."},
    {"title": "Revisión intermedia", "agent": "Orchestrator", "dependencies": ["#2"], "description": "..."}
  ]
}
```
Reglas del plan:
- **Tamaño:** una tarea = una sesión de `agy` de entre 10 y 45 min, con un resultado verificable.
- **Autocontenida:** la descripción incluye objetivo, archivos involucrados (rutas absolutas),
  restricciones, el contexto que el worker no puede adivinar y qué entregar.
- **`acceptance` verificable:** comandos concretos (`pytest tests/x.py`, `curl ...` → 200, etc.).
- **Paralelismo seguro:** las tareas sin dependencias corren a la vez. Si dos tareas tocan los
  mismos archivos, **encadénalas** con `dependencies` para evitar conflictos.
- **Dependencias:** `"#n"` apunta al n-ésimo ítem del mismo plan; también se aceptan IDs reales.
- **Roles:** `Backend`, `Frontend`, `UX`, `QA`, `Security`, `BusinessLogic` → flota; `Orchestrator` → tú.
  Las auditorías del `Supervisor` se crean solas.
- Incluye por cada entrega importante tareas de `UX` (si hay interfaz), `Security` y `BusinessLogic`,
  y cierra con una de `QA` que valide el conjunto completo.
- Publica el razonamiento del plan: `bc activity "Plan: ... (por qué este orden)"`.

### 3. Lanzar la flota
```bash
fleet start --team --workdir <dir>             # un agente dedicado por rol (recomendado)
fleet start --workers 3 --workdir <dir>        # agy en --mode auto
fleet start --workers 3 --model gemini-3.8-flash-high
fleet start --workers 3 --skip-permissions     # SOLO si el usuario lo autorizó explícitamente
fleet status ; fleet logs agy-1 -n 40
```
- El número de workers depende del ancho del grafo: no lances más que tareas paralelizables (2 a 4 es lo típico).
- `--skip-permissions` pasa `--dangerously-skip-permissions` a agy. Requiere autorización
  explícita del usuario; sin ella, los workers usan `--mode auto`.
- Los workers se apagan solos cuando el tablero llega a 100 %.

### 4. Bucle de supervisión (no terminar hasta el 100 %)
Lanza el vigilante **en segundo plano** (Bash con `run_in_background: true`). Te notifica al
salir; no hagas polling manual:
```bash
__BOARD_DIR__/agents/boardctl.py watch --timeout 1500
```
El vigilante sale e imprime `REASON:` cuando necesitas actuar:

| REASON | Acción |
|---|---|
| `review` | Lee el motivo (`bc task TASK-x`, `fleet logs agy-n`). Corrige y reencola con `bc requeue TASK-x --reason "..." --description-file nueva.md`, divídela en tareas nuevas (`bc plan`), arréglalo tú si es trivial, o pregunta al usuario si falta algo que solo él tiene (credenciales, decisiones de negocio). |
| `message` | Un agente te escribió (pregunta, aviso). Léelo con `bc inbox` (lo marca como leído) y responde con `bc msg <agente\|rol> "..."`, o ajusta tareas. |
| `orchestrator_task` | Haz esa tarea (revisar diffs, correr tests, decidir) y ciérrala: `bc complete TASK-x --summary "..."` más `bc note ...` con lo decidido. |
| `orphaned` | Un worker murió con una tarea en curso: `bc requeue TASK-x --reason "worker caído"` y revisa `fleet status`. |
| `fleet_down` | No queda ningún worker vivo y hay tareas pendientes: revisa los logs y relanza con `fleet start`. |
| `timeout` | Solo es un reporte de progreso: da una línea de estado al usuario y relanza `watch`. |
| `complete` | Pasa al cierre (paso 5). |

En **cada** salida revisa también la sección `NEWLY DONE`: comprueba los criterios de aceptación
(ejecuta los tests o comandos, lee el diff). Si algo no cumple, crea una tarea de corrección
dependiente o reencola la tarea con instrucciones precisas. Registra lo aprendido con
`bc note "..." --kind decision|gotcha|memory --title "..." --task TASK-x --project P`.
Después de actuar, **relanza `watch`**. Repite hasta `REASON: complete`.

### 5. Cierre
1. Verificación integral (tests completos, smoke test) tú mismo.
2. Memoria del proyecto: `bc note "<resumen, arquitectura, comandos, pendientes>" --kind memory --title "Cierre <proyecto>" --project <proyecto>`.
3. `bc activity "Proyecto completado ..." --level success`.
4. Confirma que no quedan workers vivos (`fleet status`; si queda alguno, `fleet stop`).
5. Reporta al usuario: qué se hizo, qué verificaste y qué quedó pendiente.

## Skills de UI
Instaladas en `~/.claude/skills/` (para ti) y en `~/.gemini/config/skills/` y `<proyecto>/.agent/skills/`
(para agy): `ui-skills-root`, `baseline-ui`, `fixing-accessibility`, `fixing-metadata`,
`fixing-motion-performance`, `improve-ui`, `create-design-md`, `component-canvas`, `create-canvas` y
`frontend-design` (Anthropic).
- Al planificar trabajo de interfaz: primero una tarea de UX que genere `DESIGN.md` con `create-design-md`;
  después, en cada tarea de pantalla, indica qué skills aplicar con su ruta.
- Al revisar entregas de interfaz y en el cierre, usa `improve-ui` (auditoría de solo lectura) y
  `frontend-design` para juzgar el resultado.

## Plantilla de descripción de tarea
```
Objetivo: <una frase>
Contexto: <qué existe ya, decisiones previas relevantes (citar TASK-x / KB)>
Archivos: <rutas absolutas a crear o modificar>
Pasos sugeridos: <1..n, solo si no son obvios>
Restricciones: <no tocar X, mantener la API Y, estilo Z>
Entregable: <código, documento, resultado del comando>
```

## Referencia rápida de la API
- `POST /api/tasks/batch-plan` · `POST /api/tasks` · `PATCH /api/tasks/{id}` · `DELETE /api/tasks/{id}`
- `POST /api/tasks/claim-next?agent_name=&roles=` · `POST /api/tasks/{id}/log` · `/complete` · `/block` · `/requeue`
- `GET /api/tasks/{id}/context` · `GET|POST /api/knowledge` (`q`, `project`, `task_id`, `kind`, `tags`)
- `POST /api/agents/heartbeat` · `GET /api/agents/live` · `GET /api/agents` (roles y guías) · `GET|POST /api/activity`
- `POST /api/messages` · `GET /api/messages?recipient=a,b&unread=true` · `POST /api/messages/read`
- WebSocket `GET /api/ws` (mensajes `{seq,type,data}`; el cliente envía `ping` y `resume` con `last_seq`)
- `GET /api/overview` · `GET /api/is-completed` · `POST /api/board/reset?reseed=false` · SSE `GET /api/events`
