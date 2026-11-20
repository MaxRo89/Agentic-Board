import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("BOARD_DATA_DIR", BASE_DIR / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = Path(os.environ.get("BOARD_DB", DATA_DIR / "board.db"))
PORT = int(os.environ.get("PORT", 1000))
HOST = os.environ.get("HOST", "0.0.0.0")

# A worker that has not sent a heartbeat for this long is shown as offline.
AGENT_STALE_SECONDS = int(os.environ.get("AGENT_STALE_SECONDS", 90))

# Create an audit task for the Supervisor every time a worker finishes a task.
AUTO_AUDIT = os.environ.get("AUTO_AUDIT", "1") == "1"
# Roles whose completed tasks are not audited again.
AUDIT_EXEMPT_ROLES = {"Orchestrator", "Supervisor"}

# Agent roles: single source of truth for the UI, the worker prompts and the fleet presets.
# `guide` is injected into the prompt of every agy worker that takes a task of that role.
DEFAULT_AGENTS = {
    "Orchestrator": {
        "role": "Orquestador (Claude)",
        "description": "Planifica, reparte tareas, resuelve bloqueos y cierra el proyecto. Sus tareas no las toma ningún worker.",
        "color": "#6d5bd0",
        "icon": "bi-diagram-3",
        "guide": ""
    },
    "Backend": {
        "role": "Desarrollo backend",
        "description": "APIs, modelos de datos, servicios, migraciones e integraciones del lado servidor.",
        "color": "#2f6fb5",
        "icon": "bi-server",
        "guide": (
            "Eres el agente de BACKEND. Trabajas en API, modelos, servicios, migraciones y pruebas del servidor. "
            "No modifiques el frontend salvo el contrato (tipos/OpenAPI) que la tarea pida. Respeta la arquitectura "
            "existente (router → servicio → repositorio), tipa todo y deja pruebas automáticas de lo que implementes."
        )
    },
    "Frontend": {
        "role": "Desarrollo frontend",
        "description": "Componentes, estado, integración con la API y build del cliente.",
        "color": "#2e8a63",
        "icon": "bi-window-sidebar",
        "guide": (
            "Eres el agente de FRONTEND. Implementas componentes, páginas, estado e integración con la API. "
            "No cambies el backend: si el contrato no alcanza, envía un mensaje 'question' a Backend u 'orchestrator'. "
            "Sigue el sistema de diseño existente (tokens, componentes) y deja el build y el lint sin errores."
        )
    },
    "UX": {
        "role": "Experiencia de usuario",
        "description": "Usabilidad, flujos, accesibilidad, consistencia visual y textos de la interfaz.",
        "color": "#b4528a",
        "icon": "bi-palette",
        "guide": (
            "Eres el agente de UX, dedicado únicamente a la experiencia de usuario. Evalúas y mejoras flujos, jerarquía "
            "visual, consistencia, estados vacíos/errores/carga, accesibilidad (WCAG 2.1 AA: contraste, foco, teclado, "
            "aria) y microcopy en español. Cambia solo capa de presentación (estilos, marcado, textos, componentes de UI); "
            "nunca lógica de negocio ni backend. Documenta cada hallazgo con pantalla, problema, impacto y solución aplicada."
        )
    },
    "QA": {
        "role": "Calidad y pruebas",
        "description": "Pruebas unitarias, de integración y E2E; reproduce y documenta defectos.",
        "color": "#b7791f",
        "icon": "bi-clipboard-check",
        "guide": (
            "Eres el agente de QA. Diseñas y ejecutas pruebas (unitarias, API, E2E) contra los criterios de aceptación. "
            "Reproduce cada defecto con pasos exactos. Corriges solo las pruebas; si encuentras un bug en el código, "
            "envía un mensaje al rol responsable (Backend/Frontend) con la reproducción y devuelve en conclusions la lista de defectos."
        )
    },
    "Supervisor": {
        "role": "Supervisor de cumplimiento",
        "description": "Audita que cada tarea terminada cumpla exactamente lo solicitado; aprueba o devuelve el trabajo.",
        "color": "#5b6573",
        "icon": "bi-eye",
        "guide": (
            "Eres el SUPERVISOR. Tu trabajo es auditar, no implementar. Para la tarea auditada: compara lo entregado con su "
            "descripción y criterios de aceptación, revisa los archivos cambiados, ejecuta la verificación indicada y comprueba "
            "que no se hizo de más ni de menos y que se respetaron restricciones y reglas de código limpio. "
            "Devuelve verdict='approved' si cumple, o verdict='rejected' con findings concretos (qué falta, dónde, cómo corregirlo). "
            "Si es rechazada, el sistema reencola la tarea original con tus hallazgos. No modifiques código."
        )
    },
    "Security": {
        "role": "Ciberseguridad",
        "description": "Revisión de seguridad: OWASP, autenticación, secretos, dependencias y configuración.",
        "color": "#c0453b",
        "icon": "bi-shield-lock",
        "guide": (
            "Eres el agente de CIBERSEGURIDAD. Revisas el código y la configuración contra OWASP Top 10 y ASVS: inyección, "
            "control de acceso/IDOR, autenticación y sesiones, CSRF/CORS, manejo de secretos, cabeceras, subida de archivos y "
            "dependencias vulnerables (pip-audit, npm audit, bandit si están disponibles). Trabajas solo sobre el sistema de este "
            "proyecto y en el entorno local; no ataques sistemas externos ni exfiltres datos. Clasifica cada hallazgo por severidad "
            "(crítica/alta/media/baja) con evidencia y remediación; corrige directamente los de bajo riesgo de cambio y reporta el resto."
        )
    },
    "BusinessLogic": {
        "role": "Abuso de lógica de negocio",
        "description": "Intenta romper las reglas de negocio: estados, permisos, cuotas, concurrencia y casos límite.",
        "color": "#2a8c9e",
        "icon": "bi-puzzle",
        "guide": (
            "Eres el agente de LÓGICA DE NEGOCIO: piensas como un usuario malicioso o descuidado dentro de la aplicación. "
            "Busca formas de saltarte las reglas del negocio en el entorno local: transiciones de estado inválidas, saltos de "
            "flujo, condiciones de carrera (peticiones concurrentes), doble gasto de créditos o cuotas, manipulación de IDs o "
            "parámetros, valores límite y negativos, repetición de peticiones, dependencias circulares. Para cada abuso encontrado "
            "deja una prueba automática que lo reproduzca (que falle mientras exista el problema), su impacto y la regla que "
            "debería impedirlo. No corrijas el código de negocio: avisa con un mensaje al rol responsable."
        )
    },
}

# Older task roles that still appear in existing plans → current role.
ROLE_ALIASES = {"Architect": "Backend", "Coder": "Backend", "Tester": "QA", "Reviewer": "Supervisor",
                "DevOps": "Backend"}
