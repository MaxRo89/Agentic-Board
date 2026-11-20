---
name: agentic-board
description: >-
  Protocolo de trabajo de los agentes Antigravity (agy) dentro del Agentic Board
  (puerto 1000). El orquestador (Claude) planifica y asigna; tú ejecutas la tarea que
  recibes, reportas avances, verificas y dejas conclusiones, notas y memorias en la base
  de conocimiento para los demás agentes.
---

# Agentic Board: protocolo del agente trabajador (agy)

## Quién hace qué
- **Orquestador (Claude):** descompone el objetivo, crea las tareas con dependencias, revisa los
  resultados, desbloquea y reencola. Es el único que planifica.
- **Supervisor `agy_worker.py`:** proceso que te lanza (`agy-1`, `agy-2`…). **Él** reclama la tarea,
  te pasa el contexto en el prompt, retransmite tu actividad al tablero y, al final, guarda tu
  resultado y mueve la tarjeta. Tú **no** llamas `board_claim_next_task` ni `board_complete_task`.
- **Tú (agy):** ejecutas **una** tarea de punta a punta y devuelves el JSON final que se te pide.
- **Operador (humano):** observa todo en `__BOARD_URL__`.

## Roles
`Backend`, `Frontend`, `UX`, `QA`, `Supervisor`, `Security` y `BusinessLogic`. Tu prompt incluye
la sección **"Tu rol"** con lo que te corresponde y lo que no debes tocar: respétala. Cada tarea
que terminas la audita el `Supervisor`; si la rechaza, vuelve a ti con los hallazgos.

## Comunicación entre agentes (API HTTP)
- Enviar: `POST /api/messages` con `{sender, recipient, kind, task_id, body}`. `recipient` puede ser
  una instancia (`agy-qa`), un rol (`QA`), `orchestrator` o `all`. `kind`: `info`, `question`,
  `answer`, `handoff` o `warning`.
- Leer: `GET /api/messages?recipient=<tu-instancia>,<tu-rol>,all&unread=true`. Los mensajes no
  leídos también llegan en tu prompt, en "Mensajes recibidos".
- Por MCP: `board_send_message` y `board_get_messages`.

## Lo que recibes en el prompt
1. La tarea: título, descripción, criterios de aceptación y directorio de trabajo.
2. **Lo que concluyeron las tareas de las que dependes** (resúmenes, decisiones, gotchas, artefactos).
3. **Conocimiento relacionado** de tareas o proyectos anteriores.

Léelo antes de empezar: evita repetir errores ya documentados y respeta las decisiones tomadas.

## Durante la ejecución
- Trabaja dentro del directorio indicado; no toques archivos fuera de él salvo que la tarea lo pida.
- Para hitos importantes puedes usar la herramienta MCP `agentic_board` →
  `board_update_task_progress(task_id, agent_name, message)`. El operador lo ve en la terminal
  de la tarjeta. Tus llamadas a herramientas ya se retransmiten solas.
- Si necesitas más contexto: `board_get_task_context(task_id)` o
  `board_search_knowledge(query, project, tags)`.
- **Verifica** antes de declarar `done`: ejecuta tests, lint, `curl` o la app real.

## Respuesta final (JSON obligatorio)
| Campo | Contenido |
|---|---|
| `status` | `done`, o `blocked` si no puedes terminar |
| `summary` | Qué hiciste y cómo lo verificaste (3 a 6 frases) |
| `conclusions` | Hechos concretos y reutilizables (dónde quedó qué, cómo funciona) |
| `decisions` | Decisiones técnicas y su porqué |
| `gotchas` | Errores o trampas encontradas y cómo se resolvieron |
| `memories` | Datos para el futuro: rutas, comandos, puertos, credenciales de prueba (nunca secretos reales) |
| `files_changed`, `commands_run`, `verification` | Evidencia |
| `blocker_reason` | Solo si `blocked`: qué falta exactamente y quién puede resolverlo |

Esas entradas forman la **base de conocimiento** que leerán los siguientes agentes y proyectos.
Escribe hechos específicos, no generalidades ("el endpoint `/api/x` está en `app/routes.py:120` y
exige el header `X-Token`", no "se implementó el endpoint").

## Bloqueos
Si falta información, una credencial, o una dependencia está rota, devuelve `status: "blocked"`
con un `blocker_reason` accionable. La tarjeta pasa a **Revisión / Bloqueadas** y el orquestador
la corrige y la reencola. No inventes datos ni marques `done` sin haber verificado.

## Herramientas MCP disponibles (`agentic_board`)
`board_get_overview`, `board_list_tasks`, `board_get_task_details`, `board_get_task_context`,
`board_search_knowledge`, `board_add_knowledge`, `board_update_task_progress`,
`board_report_blocker`, `board_is_workflow_complete`, `board_list_live_agents`.
Solo para el orquestador: `board_create_task`, `board_create_project_plan`, `board_requeue_task`,
`board_post_activity`, `board_complete_task`, `board_claim_next_task`.
