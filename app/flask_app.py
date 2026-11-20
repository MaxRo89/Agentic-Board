from flask import Flask, render_template, jsonify, request
from pathlib import Path
from app.config import DEFAULT_AGENTS, BASE_DIR
from app.database import db

flask_app = Flask(
    __name__,
    template_folder=str(BASE_DIR / "app" / "templates"),
    static_folder=str(BASE_DIR / "app" / "static")
)

@flask_app.route("/")
def index():
    """Render the Operator Kanban Dashboard."""
    overview = db.get_overview()
    return render_template(
        "index.html", 
        overview=overview,
        agents=DEFAULT_AGENTS
    )

@flask_app.route("/operator/quick-add", methods=["POST"])
def quick_add():
    """Operator quick task creation from web form."""
    data = request.form or request.json or {}
    title = data.get("title", "").strip()
    description = data.get("description", "").strip()
    agent = data.get("agent", "Orchestrator")
    priority = data.get("priority", "medium")
    dependencies_str = data.get("dependencies", "")

    deps = [d.strip() for d in dependencies_str.split(",") if d.strip()] if dependencies_str else []

    if not title:
        return jsonify({"error": "Title is required"}), 400

    task = db.create_task(
        title=title,
        description=description,
        agent=agent,
        priority=priority,
        dependencies=deps
    )
    return jsonify(task)

@flask_app.route("/operator/health")
def health():
    return jsonify({
        "status": "healthy",
        "service": "agent_board_operator_flask",
        "tasks_count": db.count_tasks()
    })
