from typing import List, Dict, Any, Optional, Literal
from pydantic import BaseModel, Field
import time

TaskStatus = Literal["todo", "in_progress", "review", "done"]
TaskPriority = Literal["low", "medium", "high", "critical"]

class TaskLogEntry(BaseModel):
    timestamp: float = Field(default_factory=time.time)
    agent: str
    message: str
    level: str = "info"  # info, warning, error, success

class TaskCreate(BaseModel):
    title: str
    description: str
    agent: str
    priority: TaskPriority = "medium"
    dependencies: List[str] = Field(default_factory=list)
    tags: List[str] = Field(default_factory=list)
    project: Optional[str] = None
    workdir: Optional[str] = None
    acceptance: Optional[str] = None

class TaskUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    agent: Optional[str] = None
    status: Optional[TaskStatus] = None
    priority: Optional[TaskPriority] = None
    dependencies: Optional[List[str]] = None
    tags: Optional[List[str]] = None
    project: Optional[str] = None
    workdir: Optional[str] = None
    acceptance: Optional[str] = None
    assignee: Optional[str] = None

class TaskProgressUpdate(BaseModel):
    agent: str
    message: str
    percent_complete: Optional[int] = None
    partial_artifacts: Optional[Dict[str, Any]] = None
    level: str = "info"

class KnowledgeEntry(BaseModel):
    kind: str = "note"  # conclusion, note, memory, decision, gotcha
    title: Optional[str] = None
    content: str
    tags: List[str] = Field(default_factory=list)

class KnowledgeCreate(KnowledgeEntry):
    agent: str
    task_id: Optional[str] = None
    project: Optional[str] = None

class TaskCompleteRequest(BaseModel):
    agent: str
    summary: str
    artifacts: Optional[Dict[str, Any]] = Field(default_factory=dict)
    status: TaskStatus = "done"
    knowledge: List[KnowledgeEntry] = Field(default_factory=list)

class BlockRequest(BaseModel):
    agent: str
    reason: str

class RequeueRequest(BaseModel):
    actor: str = "Orchestrator"
    reason: str = ""
    description: Optional[str] = None

class AgentHeartbeat(BaseModel):
    name: str
    status: str  # idle, working, waiting, stopped, finished, error
    kind: str = "agy"
    roles: Optional[List[str]] = None
    model: Optional[str] = None
    current_task: Optional[str] = None
    last_message: Optional[str] = None
    pid: Optional[int] = None
    task_done: bool = False

class ActivityCreate(BaseModel):
    actor: str = "Orchestrator"
    message: str
    level: str = "info"
    task_id: Optional[str] = None

class Task(BaseModel):
    id: str
    title: str
    description: str
    agent: str
    status: TaskStatus = "todo"
    priority: TaskPriority = "medium"
    dependencies: List[str] = Field(default_factory=list)
    tags: List[str] = Field(default_factory=list)
    artifacts: Dict[str, Any] = Field(default_factory=dict)
    logs: List[Dict[str, Any]] = Field(default_factory=list)
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)
    started_at: Optional[float] = None
    completed_at: Optional[float] = None

class BoardOverview(BaseModel):
    total_tasks: int
    todo_count: int
    in_progress_count: int
    review_count: int
    done_count: int
    progress_percentage: float
    is_completed: bool
    active_agents: List[str]
    tasks_by_column: Dict[str, List[Task]]

class BatchPlanRequest(BaseModel):
    project_name: str
    description: Optional[str] = None
    workdir: Optional[str] = None
    tasks: List[TaskCreate]

class MessageCreate(BaseModel):
    sender: str
    recipient: str  # instance (agy-2), role (Tester), 'orchestrator' or 'all'
    body: str
    kind: str = "info"  # info, question, answer, handoff, warning
    task_id: Optional[str] = None

class MessagesRead(BaseModel):
    ids: List[int]
