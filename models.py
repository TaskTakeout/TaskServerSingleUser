"""Database models and Pydantic schemas."""
from datetime import datetime
from typing import Optional, List, Any
from uuid import uuid4
import json

from sqlalchemy import Column, String, Boolean, Integer, Text, ForeignKey, Index
from sqlalchemy.orm import relationship
from pydantic import BaseModel, Field, field_validator, ConfigDict

from database import Base


# SQLAlchemy ORM Model
class TaskDB(Base):
    """SQLAlchemy model for tasks table."""
    __tablename__ = "tasks"

    id = Column(String, primary_key=True, default=lambda: str(uuid4()))
    title = Column(String(500), nullable=False)
    description = Column(Text, nullable=True)
    completed = Column(Boolean, default=False, nullable=False)
    archived = Column(Boolean, default=False, nullable=False)
    priority = Column(Integer, default=0, nullable=False)
    due_date = Column(String, nullable=True)  # ISO 8601 datetime string
    completion_date = Column(String, nullable=True)  # ISO 8601 datetime string
    parent_id = Column(String, ForeignKey("tasks.id", ondelete="CASCADE"), nullable=True)
    tags = Column(Text, nullable=True)  # JSON array of strings
    task_metadata = Column(Text, nullable=True)  # JSON object (named task_metadata to avoid SQLAlchemy conflict)
    created_at = Column(String, nullable=False)
    updated_at = Column(String, nullable=False)

    # Create indexes for common queries
    __table_args__ = (
        Index('idx_parent_id', 'parent_id'),
        Index('idx_completed', 'completed'),
        Index('idx_archived', 'archived'),
        Index('idx_priority', 'priority'),
        Index('idx_due_date', 'due_date'),
        Index('idx_created_at', 'created_at'),
    )

    def get_tags(self) -> List[str]:
        """Parse tags JSON string to list."""
        if self.tags:
            return json.loads(self.tags)
        return []

    def set_tags(self, tags: List[str]):
        """Convert tags list to JSON string."""
        if tags:
            # Normalize tags: strip whitespace and convert to lowercase for storage
            normalized = [tag.strip() for tag in tags]
            self.tags = json.dumps(normalized)
        else:
            self.tags = None

    def get_metadata(self) -> Optional[dict]:
        """Parse metadata JSON string to dict."""
        if self.task_metadata:
            return json.loads(self.task_metadata)
        return None

    def set_metadata(self, metadata: Optional[dict]):
        """Convert metadata dict to JSON string."""
        if metadata:
            self.task_metadata = json.dumps(metadata)
        else:
            self.task_metadata = None


# Pydantic Schemas
class TaskBaseProperties(BaseModel):
    """Base properties for task input/update."""
    title: Optional[str] = Field(None, min_length=1, max_length=500)
    description: Optional[str] = Field(None, max_length=5000)
    completed: Optional[bool] = None
    archived: Optional[bool] = None
    priority: Optional[int] = Field(None, ge=0, le=99)
    due_date: Optional[str] = None
    tags: Optional[List[str]] = Field(None, max_length=100)
    metadata: Optional[dict[str, Any]] = None
    parent_id: Optional[str] = None

    @field_validator('tags')
    @classmethod
    def validate_tags(cls, v):
        if v is not None:
            for tag in v:
                if not tag or len(tag.strip()) == 0:
                    raise ValueError("Tags cannot be empty")
                if len(tag) > 64:
                    raise ValueError("Tags cannot exceed 64 characters")
        return v


class TaskInput(TaskBaseProperties):
    """Schema for creating a new task."""
    title: str = Field(..., min_length=1, max_length=500)

    model_config = ConfigDict(extra='forbid')


class TaskUpdate(TaskBaseProperties):
    """Schema for updating a task (partial update)."""
    model_config = ConfigDict(extra='forbid')


class Task(TaskBaseProperties):
    """Schema for task response."""
    id: str
    title: str
    completed: bool
    archived: bool
    priority: int
    completion_date: Optional[str] = None
    created_at: str
    updated_at: str

    model_config = ConfigDict(from_attributes=True, extra='forbid')


class TaskImport(Task):
    """Schema for importing tasks with all fields."""
    pass


class TaskList(BaseModel):
    """Schema for paginated task list response."""
    data: List[Task]
    total: int
    limit: int
    offset: int


class Error(BaseModel):
    """Schema for error responses."""
    code: str
    message: str


class FieldError(BaseModel):
    """Schema for field validation errors."""
    field: str
    message: str


class ValidationError(Error):
    """Schema for validation error responses."""
    field_errors: Optional[List[FieldError]] = None


class ImportResponse(BaseModel):
    """Schema for import response."""
    imported_count: int


class ConflictError(Error):
    """Schema for conflict error with conflicting IDs."""
    conflicting_ids: Optional[List[str]] = None
