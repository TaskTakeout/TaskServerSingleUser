"""FastAPI server for single-user task management API."""
from datetime import datetime, timezone
from typing import Optional, List
from uuid import uuid4
import json
import yaml
import os

from fastapi import FastAPI, Depends, HTTPException, Header, Query, Response, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from sqlalchemy import or_, and_

from database import get_db, init_db
from models import (
    TaskDB, Task, TaskInput, TaskUpdate, TaskList, TaskImport,
    Error, ValidationError, ImportResponse, ConflictError
)

# Load configuration
config_path = "config.yaml" if os.path.exists("config.yaml") else "config.yaml.example"
with open(config_path, "r") as f:
    config = yaml.safe_load(f)

VALID_TOKENS = set(config['auth']['tokens'])

# Initialize FastAPI app
app = FastAPI(
    title="Task API",
    version="1.0.0",
    description="A general purpose Task list API for single user",
    servers=[{"url": "/task/v1", "description": "Task API v1"}]
)

security = HTTPBearer()


# Authentication
def verify_token(credentials: HTTPAuthorizationCredentials = Depends(security)):
    """Verify bearer token against configured tokens."""
    if credentials.credentials not in VALID_TOKENS:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return credentials.credentials


# Helper functions
def get_utc_now() -> str:
    """Get current UTC time in ISO 8601 format."""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_datetime(dt_str: Optional[str]) -> Optional[datetime]:
    """Parse ISO 8601 datetime string to datetime object."""
    if not dt_str:
        return None
    # Handle both with and without timezone
    if dt_str.endswith('Z'):
        dt_str = dt_str[:-1] + '+00:00'
    return datetime.fromisoformat(dt_str)


def db_to_task(db_task: TaskDB) -> Task:
    """Convert database model to Pydantic schema."""
    return Task(
        id=db_task.id,
        title=db_task.title,
        description=db_task.description,
        completed=db_task.completed,
        archived=db_task.archived,
        priority=db_task.priority,
        due_date=db_task.due_date,
        completion_date=db_task.completion_date,
        parent_id=db_task.parent_id,
        tags=db_task.get_tags(),
        metadata=db_task.get_metadata(),
        created_at=db_task.created_at,
        updated_at=db_task.updated_at
    )


def calculate_etag(task: TaskDB) -> str:
    """Calculate ETag from task's updated_at timestamp."""
    return f'"{task.updated_at}"'


# Startup event
@app.on_event("startup")
def startup_event():
    """Initialize database on startup."""
    init_db()


# Routes
@app.post("/task/v1/tasks", response_model=Task, status_code=status.HTTP_201_CREATED)
def create_task(
    task_input: TaskInput,
    response: Response,
    db: Session = Depends(get_db),
    token: str = Depends(verify_token)
):
    """Create a new task."""
    now = get_utc_now()

    # Create new task
    db_task = TaskDB(
        id=str(uuid4()),
        title=task_input.title,
        description=task_input.description,
        completed=task_input.completed if task_input.completed is not None else False,
        archived=task_input.archived if task_input.archived is not None else False,
        priority=task_input.priority if task_input.priority is not None else 0,
        due_date=task_input.due_date,
        parent_id=task_input.parent_id,
        created_at=now,
        updated_at=now
    )

    # Handle tags
    if task_input.tags is not None:
        db_task.set_tags(task_input.tags)

    # Handle metadata
    if task_input.metadata is not None:
        db_task.set_metadata(task_input.metadata)

    # Set completion_date if task is created as completed
    if db_task.completed:
        db_task.completion_date = now

    # Validate parent exists if parent_id is set
    if db_task.parent_id:
        parent = db.query(TaskDB).filter(TaskDB.id == db_task.parent_id).first()
        if not parent:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Parent task not found"
            )

    db.add(db_task)
    db.commit()
    db.refresh(db_task)

    # Set Location header
    response.headers["Location"] = f"/task/v1/tasks/{db_task.id}"

    return db_to_task(db_task)


@app.get("/task/v1/tasks", response_model=TaskList)
def list_tasks(
    response: Response,
    completed: Optional[bool] = None,
    archived: Optional[bool] = None,
    priority: Optional[int] = Query(None, ge=0, le=99),
    tag: Optional[List[str]] = Query(None),
    parent_id: Optional[str] = Query(None),
    search: Optional[str] = Query(None, min_length=1, max_length=500),
    due_before: Optional[str] = None,
    due_after: Optional[str] = None,
    overdue: Optional[bool] = None,
    sort_by: str = Query("created_at", pattern="^(created_at|updated_at|title|priority|due_date|completed)$"),
    order: str = Query("desc", pattern="^(asc|desc)$"),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    token: str = Depends(verify_token)
):
    """List and filter tasks."""
    query = db.query(TaskDB)

    # Apply filters
    if completed is not None:
        query = query.filter(TaskDB.completed == completed)

    if archived is not None:
        query = query.filter(TaskDB.archived == archived)

    if priority is not None:
        query = query.filter(TaskDB.priority == priority)

    # Parent filtering - default to root level (null) if not specified
    if parent_id is None or parent_id == "null":
        query = query.filter(TaskDB.parent_id.is_(None))
    else:
        query = query.filter(TaskDB.parent_id == parent_id)

    # Tag filtering - all tags must be present (case-insensitive)
    if tag:
        for tag_filter in tag:
            tag_normalized = tag_filter.strip().lower()
            # SQLite doesn't have array operators, so we search in JSON string
            query = query.filter(TaskDB.tags.like(f'%"{tag_normalized}"%'))

    # Search in title and description
    if search:
        search_pattern = f"%{search}%"
        query = query.filter(
            or_(
                TaskDB.title.like(search_pattern),
                TaskDB.description.like(search_pattern)
            )
        )

    # Due date filters
    if due_before:
        query = query.filter(TaskDB.due_date < due_before)

    if due_after:
        query = query.filter(TaskDB.due_date > due_after)

    # Overdue filter
    if overdue:
        now = get_utc_now()
        query = query.filter(
            and_(
                TaskDB.due_date < now,
                TaskDB.completed == False
            )
        )

    # Get total count before pagination
    total = query.count()

    # Sorting
    if sort_by == "created_at":
        sort_col = TaskDB.created_at
    elif sort_by == "updated_at":
        sort_col = TaskDB.updated_at
    elif sort_by == "title":
        sort_col = TaskDB.title
    elif sort_by == "priority":
        sort_col = TaskDB.priority
    elif sort_by == "due_date":
        sort_col = TaskDB.due_date
    elif sort_by == "completed":
        sort_col = TaskDB.completed
    else:
        sort_col = TaskDB.created_at

    # Apply sorting with nulls last for title and due_date
    if sort_by in ["title", "due_date"]:
        if order == "asc":
            query = query.order_by(sort_col.asc().nullslast(), TaskDB.created_at.desc())
        else:
            query = query.order_by(sort_col.desc().nullslast(), TaskDB.created_at.desc())
    else:
        if order == "asc":
            query = query.order_by(sort_col.asc(), TaskDB.created_at.desc())
        else:
            query = query.order_by(sort_col.desc(), TaskDB.created_at.desc())

    # Pagination
    tasks = query.offset(offset).limit(limit).all()

    return TaskList(
        data=[db_to_task(task) for task in tasks],
        total=total,
        limit=limit,
        offset=offset
    )


@app.get("/task/v1/tasks/export", response_model=List[Task])
def export_tasks(
    db: Session = Depends(get_db),
    token: str = Depends(verify_token)
):
    """Export all tasks ordered with parents before children."""
    # Get all tasks ordered by parent relationship
    # First get root tasks, then tasks with parents
    root_tasks = db.query(TaskDB).filter(TaskDB.parent_id.is_(None)).order_by(TaskDB.created_at).all()
    child_tasks = db.query(TaskDB).filter(TaskDB.parent_id.isnot(None)).order_by(TaskDB.created_at).all()

    all_tasks = root_tasks + child_tasks

    return [db_to_task(task) for task in all_tasks]


@app.post("/task/v1/tasks/import", response_model=ImportResponse)
def import_tasks(
    tasks: List[TaskImport],
    response: Response,
    validate_only: bool = Query(False),
    on_conflict: str = Query("fail", pattern="^(fail|skip|upsert)$"),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
    db: Session = Depends(get_db),
    token: str = Depends(verify_token)
):
    """Import tasks in bulk, preserving IDs and timestamps."""

    # Validate all tasks first
    task_ids = [task.id for task in tasks]

    # Check for duplicate IDs in the import payload
    if len(task_ids) != len(set(task_ids)):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Duplicate IDs in import payload"
        )

    # Check for existing IDs
    existing_tasks = db.query(TaskDB.id).filter(TaskDB.id.in_(task_ids)).all()
    existing_ids = [task.id for task in existing_tasks]

    if existing_ids:
        if on_conflict == "fail":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="One or more task IDs already exist",
                headers={"X-Conflicting-IDs": json.dumps(existing_ids)}
            )
        elif on_conflict == "skip":
            # Filter out existing IDs
            tasks = [task for task in tasks if task.id not in existing_ids]
        elif on_conflict == "upsert":
            # Will update existing tasks
            pass

    if validate_only:
        response.status_code = status.HTTP_200_OK
        return ImportResponse(imported_count=len(tasks))

    # Sort tasks to ensure parents are created before children
    tasks_by_id = {task.id: task for task in tasks}
    sorted_tasks = []
    processed = set()

    def add_task_with_parents(task: TaskImport):
        """Recursively add task and its parents."""
        if task.id in processed:
            return

        # Add parent first if it exists
        if task.parent_id and task.parent_id in tasks_by_id:
            add_task_with_parents(tasks_by_id[task.parent_id])

        sorted_tasks.append(task)
        processed.add(task.id)

    for task in tasks:
        add_task_with_parents(task)

    # Import tasks
    imported_count = 0

    for task in sorted_tasks:
        if on_conflict == "upsert" and task.id in existing_ids:
            # Update existing task
            db_task = db.query(TaskDB).filter(TaskDB.id == task.id).first()
            if db_task:
                db_task.title = task.title
                db_task.description = task.description
                db_task.completed = task.completed
                db_task.archived = task.archived
                db_task.priority = task.priority
                db_task.due_date = task.due_date
                db_task.completion_date = task.completion_date
                db_task.parent_id = task.parent_id
                db_task.set_tags(task.tags if task.tags else [])
                db_task.set_metadata(task.metadata)
                db_task.created_at = task.created_at
                db_task.updated_at = task.updated_at
                imported_count += 1
        else:
            # Create new task
            db_task = TaskDB(
                id=task.id,
                title=task.title,
                description=task.description,
                completed=task.completed,
                archived=task.archived,
                priority=task.priority,
                due_date=task.due_date,
                completion_date=task.completion_date,
                parent_id=task.parent_id,
                created_at=task.created_at,
                updated_at=task.updated_at
            )
            db_task.set_tags(task.tags if task.tags else [])
            db_task.set_metadata(task.metadata)
            db.add(db_task)
            imported_count += 1

    db.commit()

    response.status_code = status.HTTP_201_CREATED
    return ImportResponse(imported_count=imported_count)


@app.get("/task/v1/tasks/{id}", response_model=Task)
def get_task(
    id: str,
    response: Response,
    db: Session = Depends(get_db),
    token: str = Depends(verify_token)
):
    """Get a single task by ID."""
    task = db.query(TaskDB).filter(TaskDB.id == id).first()

    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Task not found"
        )

    # Set ETag header
    response.headers["ETag"] = calculate_etag(task)

    return db_to_task(task)


@app.patch("/task/v1/tasks/{id}", response_model=Task)
def update_task(
    id: str,
    task_update: TaskUpdate,
    response: Response,
    if_unmodified_since: Optional[str] = Header(None),
    if_match: Optional[str] = Header(None),
    db: Session = Depends(get_db),
    token: str = Depends(verify_token)
):
    """Partially update a task."""
    task = db.query(TaskDB).filter(TaskDB.id == id).first()

    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Task not found"
        )

    # Check preconditions
    if if_unmodified_since:
        task_updated = parse_datetime(task.updated_at)
        client_time = parse_datetime(if_unmodified_since)
        if task_updated and client_time and task_updated > client_time:
            raise HTTPException(
                status_code=status.HTTP_412_PRECONDITION_FAILED,
                detail="Task has been modified since specified time"
            )

    if if_match:
        current_etag = calculate_etag(task)
        if if_match != current_etag:
            raise HTTPException(
                status_code=status.HTTP_412_PRECONDITION_FAILED,
                detail="ETag does not match current resource"
            )

    # Track if completed status changed
    old_completed = task.completed

    # Apply updates
    update_data = task_update.model_dump(exclude_unset=True)

    for field, value in update_data.items():
        if field == "tags":
            task.set_tags(value)
        elif field == "metadata":
            task.set_metadata(value)
        elif field == "parent_id":
            # Validate parent exists if being set
            if value:
                parent = db.query(TaskDB).filter(TaskDB.id == value).first()
                if not parent:
                    raise HTTPException(
                        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                        detail="Parent task not found"
                    )
                # Prevent circular references
                if value == task.id:
                    raise HTTPException(
                        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                        detail="Task cannot be its own parent"
                    )
            setattr(task, field, value)
        else:
            setattr(task, field, value)

    # Handle completion_date based on completed status
    if "completed" in update_data:
        if task.completed and not old_completed:
            # Task is being marked as completed
            task.completion_date = get_utc_now()
        elif not task.completed and old_completed:
            # Task is being marked as incomplete
            task.completion_date = None

    # Update timestamp
    task.updated_at = get_utc_now()

    db.commit()
    db.refresh(task)

    # Set new ETag
    response.headers["ETag"] = calculate_etag(task)

    return db_to_task(task)


@app.delete("/task/v1/tasks/{id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_task(
    id: str,
    db: Session = Depends(get_db),
    token: str = Depends(verify_token)
):
    """Delete a task and all its subtasks (cascading delete)."""
    task = db.query(TaskDB).filter(TaskDB.id == id).first()

    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Task not found"
        )

    # SQLite with CASCADE on delete will handle subtasks automatically
    db.delete(task)
    db.commit()

    return Response(status_code=status.HTTP_204_NO_CONTENT)


# Main entry point
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "server:app",
        host=config['server']['host'],
        port=config['server']['port'],
        reload=True
    )
