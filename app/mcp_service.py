import json
from typing import Dict, Any, List
from app.database import db

MCP_TOOLS = [
    {
        "name": "board_get_overview",
        "description": "Obtiene un resumen completo del tablero: total de tareas, tareas por columna (todo, in_progress, review, done), porcentaje de progreso, agentes activos y si el flujo de trabajo está 100% completado.",
        "inputSchema": {
            "type": "object",
            "properties": {}
        }
    },
    {
        "name": "board_list_tasks",
        "description": "Lista las tareas del tablero con filtros opcionales por estado ('todo', 'in_progress', 'review', 'done') o por agente asignado.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "enum": ["todo", "in_progress", "review", "done"],
                    "description": "Filtrar por columna/estado"
                },
                "agent_name": {
                    "type": "string",
                    "description": "Filtrar por agente (ej: 'Coder', 'Architect', 'Tester', 'Reviewer')"
                }
            }
        }
    },
    {
        "name": "board_get_task_details",
        "description": "Obtiene el detalle completo de una tarea específica: descripción, agente, dependencias, estado de cada dependencia, artefactos y el registro de logs paso a paso.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": "ID único de la tarea (ej: 'TASK-01')"
                }
            },
            "required": ["task_id"]
        }
    },
    {
        "name": "board_create_task",
        "description": "Crea una nueva tarea en el tablero asignada a un rol de agente de IA. Usado por el Orquestador para planificar pasos de ejecución con dependencias.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "Título conciso y descriptivo de la tarea"
                },
                "description": {
                    "type": "string",
                    "description": "Instrucciones detalladas, contexto, requisitos y entregables esperados"
                },
                "agent_name": {
                    "type": "string",
                    "description": "Agente asignado (ej: 'Orchestrator', 'Architect', 'Coder', 'Tester', 'Reviewer', 'DevOps')"
                },
                "priority": {
                    "type": "string",
                    "enum": ["low", "medium", "high", "critical"],
                    "default": "medium",
                    "description": "Nivel de prioridad de la tarea"
                },
                "dependencies": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Lista de IDs de tareas que deben estar 'done' antes de que esta tarea pueda comenzar"
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Etiquetas temáticas (ej: ['frontend', 'auth'])"
                },
                "project": {"type": "string", "description": "Proyecto al que pertenece"},
                "workdir": {"type": "string", "description": "Directorio de trabajo donde el agente ejecuta la tarea"},
                "acceptance": {"type": "string", "description": "Criterios de aceptación verificables"}
            },
            "required": ["title", "description", "agent_name"]
        }
    },
    {
        "name": "board_create_project_plan",
        "description": "Crea un plan de proyecto completo con múltiples tareas en lote y encadenamiento automático de dependencias. Herramienta principal para que el Orquestador configure todo el tablero de una sola vez.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_name": {
                    "type": "string",
                    "description": "Nombre del proyecto o sprint"
                },
                "workdir": {"type": "string", "description": "Directorio de trabajo por defecto para todas las tareas"},
                "tasks": {
                    "type": "array",
                    "description": "Lista de especificaciones de tareas a crear",
                    "items": {
                        "type": "object",
                        "properties": {
                            "title": {"type": "string"},
                            "description": {"type": "string"},
                            "agent_name": {"type": "string"},
                            "priority": {"type": "string", "enum": ["low", "medium", "high", "critical"]},
                            "dependencies": {"type": "array", "items": {"type": "string"},
                                             "description": "IDs reales ('TASK-03') o posición en este plan ('#1')"},
                            "tags": {"type": "array", "items": {"type": "string"}},
                            "workdir": {"type": "string"},
                            "acceptance": {"type": "string"}
                        },
                        "required": ["title", "description", "agent_name"]
                    }
                }
            },
            "required": ["project_name", "tasks"]
        }
    },
    {
        "name": "board_claim_next_task",
        "description": "Herramienta CRÍTICA para agentes trabajadores: Busca y reclama automáticamente la siguiente tarea en 'todo' asignada al agente cuyas dependencias ya estén todas 'done'. Mueve la tarea a 'in_progress' y la bloquea para el agente.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent_name": {
                    "type": "string",
                    "description": "Nombre o rol del agente solicitante (ej: 'Coder', 'Tester', 'Architect')"
                }
            },
            "required": ["agent_name"]
        }
    },
    {
        "name": "board_update_task_progress",
        "description": "Registra una entrada de progreso o notas intermedias en la tarea activa. Permite al operador y a otros agentes ver en tiempo real qué está ejecutando el agente.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": "ID de la tarea"
                },
                "agent_name": {
                    "type": "string",
                    "description": "Nombre del agente que reporta el progreso"
                },
                "message": {
                    "type": "string",
                    "description": "Mensaje de log describiendo la acción realizada o avance alcanzado"
                },
                "partial_artifacts": {
                    "type": "object",
                    "description": "Diccionario opcional con artefactos parciales (ej: rutas de archivos, código generado)"
                }
            },
            "required": ["task_id", "agent_name", "message"]
        }
    },
    {
        "name": "board_complete_task",
        "description": "Marca una tarea como completada ('done') o la envía a revisión ('review'). Adjunta los entregables finales (artefactos). Automáticamente desbloquea las tareas dependientes en el tablero.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": "ID de la tarea completada"
                },
                "agent_name": {
                    "type": "string",
                    "description": "Nombre del agente que finaliza la tarea"
                },
                "summary": {
                    "type": "string",
                    "description": "Resumen ejecutivo de lo realizado y resultados obtenidos"
                },
                "artifacts": {
                    "type": "object",
                    "description": "Entregables generados: { 'files': [...], 'endpoints': [...], 'report': '...' }"
                },
                "status": {
                    "type": "string",
                    "enum": ["done", "review"],
                    "default": "done",
                    "description": "'done' si está 100% finalizada, o 'review' si requiere validación humana/QA"
                },
                "knowledge": {
                    "type": "array",
                    "description": "OBLIGATORIO en la práctica: conclusiones, notas y memorias reutilizables por otros agentes.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "kind": {"type": "string", "enum": ["conclusion", "note", "memory", "decision", "gotcha"]},
                            "title": {"type": "string"},
                            "content": {"type": "string"},
                            "tags": {"type": "array", "items": {"type": "string"}}
                        },
                        "required": ["kind", "content"]
                    }
                }
            },
            "required": ["task_id", "agent_name", "summary"]
        }
    },
    {
        "name": "board_report_blocker",
        "description": "Notifica que el agente no puede continuar con una tarea debido a un error o bloqueo externo. Mueve la tarea a 'review' y alerta al operador en el tablero.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": "ID de la tarea bloqueada"
                },
                "agent_name": {
                    "type": "string",
                    "description": "Nombre del agente"
                },
                "reason": {
                    "type": "string",
                    "description": "Causa detallada del bloqueo o error encontrado"
                }
            },
            "required": ["task_id", "agent_name", "reason"]
        }
    },
    {
        "name": "board_is_workflow_complete",
        "description": "Verifica si todas las tareas del tablero están terminadas ('done'). Los agentes de IA utilizan este chequeo para no detenerse hasta que el tablero esté 100% finalizado.",
        "inputSchema": {
            "type": "object",
            "properties": {}
        }
    },
    {
        "name": "board_get_task_context",
        "description": "Antes de empezar una tarea: devuelve la tarea, las conclusiones/artefactos de sus dependencias y conocimiento relacionado de la base de conocimiento.",
        "inputSchema": {"type": "object", "properties": {"task_id": {"type": "string"}}, "required": ["task_id"]}
    },
    {
        "name": "board_search_knowledge",
        "description": "Busca en la base de conocimiento (conclusiones, notas, memorias, decisiones, gotchas) que los agentes registraron al terminar tareas, incluso de proyectos anteriores.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Palabras clave"},
                "project": {"type": "string"},
                "task_id": {"type": "string"},
                "kind": {"type": "string"},
                "tags": {"type": "array", "items": {"type": "string"}},
                "limit": {"type": "integer", "default": 20}
            }
        }
    },
    {
        "name": "board_add_knowledge",
        "description": "Registra una entrada en la base de conocimiento (conclusion, note, memory, decision, gotcha).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent_name": {"type": "string"},
                "kind": {"type": "string", "enum": ["conclusion", "note", "memory", "decision", "gotcha"]},
                "title": {"type": "string"},
                "content": {"type": "string"},
                "task_id": {"type": "string"},
                "project": {"type": "string"},
                "tags": {"type": "array", "items": {"type": "string"}}
            },
            "required": ["agent_name", "kind", "title", "content"]
        }
    },
    {
        "name": "board_requeue_task",
        "description": "(Orquestador) Devuelve una tarea a 'todo' para reintentarla, opcionalmente con instrucciones corregidas.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "reason": {"type": "string"},
                "description": {"type": "string", "description": "Nueva descripción/instrucciones (opcional)"}
            },
            "required": ["task_id", "reason"]
        }
    },
    {
        "name": "board_post_activity",
        "description": "(Orquestador) Publica una decisión o evento en el feed de actividad visible para el operador.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "message": {"type": "string"},
                "level": {"type": "string", "enum": ["info", "warning", "error", "success"]},
                "task_id": {"type": "string"}
            },
            "required": ["message"]
        }
    },
    {
        "name": "board_list_live_agents",
        "description": "Lista los agentes trabajadores en ejecución (agy-1, agy-2...) con su estado, tarea actual y último heartbeat.",
        "inputSchema": {"type": "object", "properties": {}}
    },
    {
        "name": "board_send_message",
        "description": "Envía un mensaje a otro agente a través del tablero: a una instancia (agy-2), a un rol (Tester), a 'orchestrator' o a 'all'. Úsalo para preguntas, avisos o traspasos (handoff).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "sender": {"type": "string", "description": "Tu nombre de instancia (ej. agy-1)"},
                "recipient": {"type": "string"},
                "body": {"type": "string"},
                "kind": {"type": "string", "enum": ["info", "question", "answer", "handoff", "warning"]},
                "task_id": {"type": "string"}
            },
            "required": ["sender", "recipient", "body"]
        }
    },
    {
        "name": "board_get_messages",
        "description": "Lee los mensajes dirigidos a ti (por instancia, rol o 'all'). Opcionalmente solo los no leídos y los márcalos como leídos.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "recipients": {"type": "array", "items": {"type": "string"}, "description": "ej. ['agy-1', 'Coder', 'all']"},
                "task_id": {"type": "string"},
                "unread_only": {"type": "boolean", "default": True},
                "mark_read": {"type": "boolean", "default": True}
            },
            "required": ["recipients"]
        }
    }
]

def execute_mcp_tool(name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Execute an MCP tool request and return standardized response."""
    try:
        if name == "board_get_overview":
            overview = db.get_overview()
            return {
                "success": True,
                "overview": overview,
                "message": f"Tablero con {overview['total_tasks']} tareas. Progreso: {overview['progress_percentage']}%. Completado: {overview['is_completed']}."
            }

        elif name == "board_list_tasks":
            status = arguments.get("status")
            agent_name = arguments.get("agent_name")
            tasks = db.list_tasks(status=status, agent=agent_name)
            return {
                "success": True,
                "count": len(tasks),
                "tasks": tasks
            }

        elif name == "board_get_task_details":
            task_id = arguments["task_id"]
            task = db.get_task(task_id)
            if not task:
                return {"success": False, "error": f"Tarea '{task_id}' no encontrada."}
            return {"success": True, "task": task}

        elif name == "board_create_task":
            title = arguments["title"]
            description = arguments["description"]
            agent_name = arguments["agent_name"]
            priority = arguments.get("priority", "medium")
            dependencies = arguments.get("dependencies", [])
            tags = arguments.get("tags", [])

            task = db.create_task(
                title=title,
                description=description,
                agent=agent_name,
                priority=priority,
                dependencies=dependencies,
                tags=tags,
                project=arguments.get("project"),
                workdir=arguments.get("workdir"),
                acceptance=arguments.get("acceptance")
            )
            return {
                "success": True,
                "task_id": task["id"],
                "task": task,
                "message": f"Tarea {task['id']} asignada a @{agent_name} creada en el tablero."
            }

        elif name == "board_create_project_plan":
            project_name = arguments["project_name"]
            task_defs = arguments["tasks"]
            created = db.create_plan(project_name, task_defs, workdir=arguments.get("workdir"))
            return {
                "success": True,
                "project_name": project_name,
                "created_count": len(created),
                "tasks": created,
                "message": f"Plan '{project_name}' creado con {len(created)} tareas en el tablero."
            }

        elif name == "board_claim_next_task":
            agent_name = arguments["agent_name"]
            task = db.claim_next_task(agent_name, arguments.get("roles"))
            if not task:
                # Give helpful context: is the board complete or are tasks waiting on deps?
                overview = db.get_overview()
                if overview["is_completed"]:
                    return {
                        "success": True,
                        "claimed": False,
                        "workflow_finished": True,
                        "message": "¡El tablero está 100% completado! No quedan tareas pendientes."
                    }
                else:
                    return {
                        "success": True,
                        "claimed": False,
                        "workflow_finished": False,
                        "message": f"No hay tareas 'todo' listas para @{agent_name} en este momento (posiblemente esperando que se completen dependencias previas)."
                    }

            return {
                "success": True,
                "claimed": True,
                "task_id": task["id"],
                "task": task,
                "message": f"@{agent_name} reclamó exitosamente la tarea {task['id']}: '{task['title']}'."
            }

        elif name == "board_update_task_progress":
            task_id = arguments["task_id"]
            agent_name = arguments["agent_name"]
            message = arguments["message"]
            partial = arguments.get("partial_artifacts")

            success = db.add_log(task_id, agent_name, message, "info")
            if not success:
                return {"success": False, "error": f"Tarea '{task_id}' no encontrada."}

            if partial:
                t = db.get_task(task_id)
                if t:
                    art = t.get("artifacts", {})
                    art.update(partial)
                    db.update_task(task_id, {"artifacts": art})

            return {
                "success": True,
                "task_id": task_id,
                "message": f"Progreso registrado en {task_id} por @{agent_name}."
            }

        elif name == "board_complete_task":
            task_id = arguments["task_id"]
            agent_name = arguments["agent_name"]
            summary = arguments["summary"]
            artifacts = arguments.get("artifacts", {})
            status = arguments.get("status", "done")

            task = db.complete_task(
                task_id=task_id,
                agent=agent_name,
                summary=summary,
                artifacts=artifacts,
                status=status,
                knowledge=arguments.get("knowledge", [])
            )
            if not task:
                return {"success": False, "error": f"Tarea '{task_id}' no encontrada."}

            overview = db.get_overview()
            return {
                "success": True,
                "task_id": task_id,
                "status": status,
                "task": task,
                "workflow_complete": overview["is_completed"],
                "remaining_tasks": overview["total_tasks"] - overview["done_count"],
                "message": f"Tarea {task_id} marcada como '{status}' por @{agent_name}. Quedan {overview['total_tasks'] - overview['done_count']} tareas pendientes."
            }

        elif name == "board_report_blocker":
            task_id = arguments["task_id"]
            agent_name = arguments["agent_name"]
            reason = arguments["reason"]

            if not db.block_task(task_id, agent_name, reason):
                return {"success": False, "error": f"Tarea '{task_id}' no encontrada."}
            return {
                "success": True,
                "task_id": task_id,
                "message": f"Bloqueo reportado en {task_id}. Movida a revisión para asistencia del operador."
            }

        elif name == "board_is_workflow_complete":
            overview = db.get_overview()
            return {
                "success": True,
                "is_completed": overview["is_completed"],
                "total_tasks": overview["total_tasks"],
                "done_count": overview["done_count"],
                "todo_count": overview["todo_count"],
                "in_progress_count": overview["in_progress_count"],
                "review_count": overview["review_count"],
                "message": "¡Tablero completado al 100%!" if overview["is_completed"] else f"Flujo en progreso: {overview['done_count']}/{overview['total_tasks']} tareas completadas."
            }

        elif name == "board_get_task_context":
            ctx = db.get_task_context(arguments["task_id"])
            if not ctx:
                return {"success": False, "error": f"Tarea '{arguments['task_id']}' no encontrada."}
            return {"success": True, **ctx}

        elif name == "board_search_knowledge":
            entries = db.search_knowledge(query=arguments.get("query"), project=arguments.get("project"),
                                          task_id=arguments.get("task_id"), kind=arguments.get("kind"),
                                          tags=arguments.get("tags"), limit=arguments.get("limit", 20))
            return {"success": True, "count": len(entries), "entries": entries}

        elif name == "board_add_knowledge":
            entry = db.add_knowledge(agent=arguments["agent_name"], title=arguments["title"],
                                     content=arguments["content"], kind=arguments.get("kind", "note"),
                                     task_id=arguments.get("task_id"), project=arguments.get("project"),
                                     tags=arguments.get("tags"))
            return {"success": True, "entry": entry}

        elif name == "board_requeue_task":
            task = db.requeue_task(arguments["task_id"], "Orchestrator", arguments.get("reason", ""),
                                   arguments.get("description"))
            if not task:
                return {"success": False, "error": f"Tarea '{arguments['task_id']}' no encontrada."}
            return {"success": True, "task": task}

        elif name == "board_post_activity":
            entry = db.add_activity("Orchestrator", arguments["message"], arguments.get("level", "info"),
                                    arguments.get("task_id"))
            return {"success": True, "entry": entry}

        elif name == "board_send_message":
            msg = db.send_message(arguments["sender"], arguments["recipient"], arguments["body"],
                                  arguments.get("kind", "info"), arguments.get("task_id"))
            return {"success": True, "message": msg}

        elif name == "board_get_messages":
            msgs = db.list_messages(arguments["recipients"], arguments.get("task_id"),
                                    arguments.get("unread_only", True))
            if arguments.get("mark_read", True):
                db.mark_messages_read([m["id"] for m in msgs])
            return {"success": True, "count": len(msgs), "messages": msgs}

        elif name == "board_list_live_agents":
            return {"success": True, "agents": db.list_live_agents()}

        else:
            return {"success": False, "error": f"Herramienta desconocida: '{name}'"}

    except Exception as e:
        return {"success": False, "error": str(e)}
