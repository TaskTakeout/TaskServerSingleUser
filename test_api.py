"""Comprehensive integration test suite for Task API."""
import os
import pytest
from datetime import datetime, timezone, timedelta
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from server import app, verify_token
from database import Base, get_db
from models import TaskDB

# Test database setup
TEST_DATABASE_PATH = "/tmp/test_tasks.db"
TEST_DATABASE_URL = f"sqlite:///{TEST_DATABASE_PATH}"
test_engine = create_engine(TEST_DATABASE_URL, connect_args={"check_same_thread": False})
TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)

# Enable foreign key constraints in test database
from sqlalchemy import event as sqlalchemy_event

@sqlalchemy_event.listens_for(test_engine, "connect")
def set_sqlite_pragma(dbapi_conn, connection_record):
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def override_get_db():
    """Override database dependency for testing."""
    db = TestSessionLocal()
    try:
        yield db
    finally:
        db.close()


def override_verify_token():
    """Override token verification for testing."""
    return "test-token"


# Override dependencies
app.dependency_overrides[get_db] = override_get_db
app.dependency_overrides[verify_token] = override_verify_token

client = TestClient(app)


@pytest.fixture(autouse=True)
def setup_database():
    """Create fresh database for each test."""
    # Remove old database file if it exists
    if os.path.exists(TEST_DATABASE_PATH):
        try:
            os.remove(TEST_DATABASE_PATH)
        except:
            pass

    # Create tables
    Base.metadata.create_all(bind=test_engine)
    yield

    # Clean up after test
    Base.metadata.drop_all(bind=test_engine)
    test_engine.dispose()  # Close all connections

    # Remove database file
    if os.path.exists(TEST_DATABASE_PATH):
        try:
            os.remove(TEST_DATABASE_PATH)
        except:
            pass


def get_utc_now():
    """Get current UTC time in ISO 8601 format."""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class TestAuthentication:
    """Test authentication and authorization."""

    def test_missing_auth_header(self):
        """Test request without Authorization header fails."""
        # Temporarily remove auth override
        saved_override = app.dependency_overrides.pop(verify_token, None)
        try:
            response = client.get("/task/v1/tasks")
            assert response.status_code == 403
        finally:
            # Restore override
            if saved_override:
                app.dependency_overrides[verify_token] = saved_override

    def test_valid_auth_token(self):
        """Test request with valid token succeeds."""
        response = client.get(
            "/task/v1/tasks",
            headers={"Authorization": "Bearer test-token"}
        )
        assert response.status_code == 200


class TestCreateTask:
    """Test POST /tasks endpoint."""

    def test_create_minimal_task(self):
        """Test creating a task with only required fields."""
        response = client.post(
            "/task/v1/tasks",
            json={"title": "Buy milk"},
            headers={"Authorization": "Bearer test-token"}
        )
        assert response.status_code == 201
        data = response.json()
        assert data["title"] == "Buy milk"
        assert data["completed"] == False
        assert data["archived"] == False
        assert data["priority"] == 0
        assert "id" in data
        assert "created_at" in data
        assert "updated_at" in data
        assert data["completion_date"] is None
        assert "Location" in response.headers

    def test_create_full_task(self):
        """Test creating a task with all fields."""
        task_data = {
            "title": "Draft Q4 plan",
            "description": "Outline scope and owners",
            "completed": False,
            "archived": False,
            "priority": 50,
            "due_date": "2025-12-01T17:00:00Z",
            "tags": ["work", "planning"],
            "metadata": {"estimated_hours": 4, "assignee": "alice"}
        }
        response = client.post(
            "/task/v1/tasks",
            json=task_data,
            headers={"Authorization": "Bearer test-token"}
        )
        assert response.status_code == 201
        data = response.json()
        assert data["title"] == task_data["title"]
        assert data["description"] == task_data["description"]
        assert data["priority"] == 50
        assert data["due_date"] == task_data["due_date"]
        assert data["tags"] == task_data["tags"]
        assert data["metadata"] == task_data["metadata"]

    def test_create_completed_task(self):
        """Test creating a task that is already completed sets completion_date."""
        response = client.post(
            "/task/v1/tasks",
            json={"title": "Done task", "completed": True},
            headers={"Authorization": "Bearer test-token"}
        )
        assert response.status_code == 201
        data = response.json()
        assert data["completed"] == True
        assert data["completion_date"] is not None

    def test_create_task_missing_title(self):
        """Test creating a task without title fails."""
        response = client.post(
            "/task/v1/tasks",
            json={"description": "No title"},
            headers={"Authorization": "Bearer test-token"}
        )
        assert response.status_code == 422

    def test_create_task_with_invalid_parent(self):
        """Test creating a task with non-existent parent fails."""
        response = client.post(
            "/task/v1/tasks",
            json={"title": "Subtask", "parent_id": "00000000-0000-0000-0000-000000000000"},
            headers={"Authorization": "Bearer test-token"}
        )
        assert response.status_code == 422


class TestGetTask:
    """Test GET /tasks/{id} endpoint."""

    def test_get_existing_task(self):
        """Test retrieving an existing task."""
        # Create a task first
        create_response = client.post(
            "/task/v1/tasks",
            json={"title": "Test task"},
            headers={"Authorization": "Bearer test-token"}
        )
        task_id = create_response.json()["id"]

        # Get the task
        response = client.get(
            f"/task/v1/tasks/{task_id}",
            headers={"Authorization": "Bearer test-token"}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == task_id
        assert data["title"] == "Test task"
        assert "ETag" in response.headers

    def test_get_nonexistent_task(self):
        """Test retrieving a non-existent task returns 404."""
        response = client.get(
            "/task/v1/tasks/00000000-0000-0000-0000-000000000000",
            headers={"Authorization": "Bearer test-token"}
        )
        assert response.status_code == 404


class TestListTasks:
    """Test GET /tasks endpoint."""

    def test_list_empty_tasks(self):
        """Test listing tasks when none exist."""
        response = client.get(
            "/task/v1/tasks",
            headers={"Authorization": "Bearer test-token"}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["data"] == []
        assert data["total"] == 0
        assert data["limit"] == 100
        assert data["offset"] == 0

    def test_list_multiple_tasks(self):
        """Test listing multiple tasks."""
        # Create some tasks
        for i in range(5):
            client.post(
                "/task/v1/tasks",
                json={"title": f"Task {i}"},
                headers={"Authorization": "Bearer test-token"}
            )

        response = client.get(
            "/task/v1/tasks",
            headers={"Authorization": "Bearer test-token"}
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data["data"]) == 5
        assert data["total"] == 5

    def test_filter_by_completed(self):
        """Test filtering tasks by completed status."""
        client.post("/task/v1/tasks", json={"title": "Todo", "completed": False}, headers={"Authorization": "Bearer test-token"})
        client.post("/task/v1/tasks", json={"title": "Done", "completed": True}, headers={"Authorization": "Bearer test-token"})

        response = client.get(
            "/task/v1/tasks?completed=true",
            headers={"Authorization": "Bearer test-token"}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert data["data"][0]["title"] == "Done"

    def test_filter_by_archived(self):
        """Test filtering tasks by archived status."""
        client.post("/task/v1/tasks", json={"title": "Active", "archived": False}, headers={"Authorization": "Bearer test-token"})
        client.post("/task/v1/tasks", json={"title": "Archived", "archived": True}, headers={"Authorization": "Bearer test-token"})

        response = client.get(
            "/task/v1/tasks?archived=true",
            headers={"Authorization": "Bearer test-token"}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert data["data"][0]["title"] == "Archived"

    def test_filter_by_priority(self):
        """Test filtering tasks by priority."""
        client.post("/task/v1/tasks", json={"title": "Low", "priority": 10}, headers={"Authorization": "Bearer test-token"})
        client.post("/task/v1/tasks", json={"title": "High", "priority": 90}, headers={"Authorization": "Bearer test-token"})

        response = client.get(
            "/task/v1/tasks?priority=90",
            headers={"Authorization": "Bearer test-token"}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert data["data"][0]["title"] == "High"

    def test_filter_by_tags(self):
        """Test filtering tasks by tags."""
        client.post("/task/v1/tasks", json={"title": "Work task", "tags": ["work"]}, headers={"Authorization": "Bearer test-token"})
        client.post("/task/v1/tasks", json={"title": "Personal task", "tags": ["personal"]}, headers={"Authorization": "Bearer test-token"})
        client.post("/task/v1/tasks", json={"title": "Work+Urgent", "tags": ["work", "urgent"]}, headers={"Authorization": "Bearer test-token"})

        # Filter by single tag
        response = client.get(
            "/task/v1/tasks?tag=work",
            headers={"Authorization": "Bearer test-token"}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 2

        # Filter by multiple tags (all must match)
        response = client.get(
            "/task/v1/tasks?tag=work&tag=urgent",
            headers={"Authorization": "Bearer test-token"}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert data["data"][0]["title"] == "Work+Urgent"

    def test_filter_by_parent_id(self):
        """Test filtering tasks by parent_id."""
        # Create parent task
        parent_response = client.post(
            "/task/v1/tasks",
            json={"title": "Parent"},
            headers={"Authorization": "Bearer test-token"}
        )
        parent_id = parent_response.json()["id"]

        # Create child task
        client.post(
            "/task/v1/tasks",
            json={"title": "Child", "parent_id": parent_id},
            headers={"Authorization": "Bearer test-token"}
        )

        # List root tasks (default behavior)
        response = client.get(
            "/task/v1/tasks",
            headers={"Authorization": "Bearer test-token"}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert data["data"][0]["title"] == "Parent"

        # List children of parent
        response = client.get(
            f"/task/v1/tasks?parent_id={parent_id}",
            headers={"Authorization": "Bearer test-token"}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert data["data"][0]["title"] == "Child"

    def test_search_in_title_and_description(self):
        """Test full-text search in title and description."""
        client.post("/task/v1/tasks", json={"title": "Buy groceries", "description": "Milk and eggs"}, headers={"Authorization": "Bearer test-token"})
        client.post("/task/v1/tasks", json={"title": "Clean house", "description": "Vacuum and dust"}, headers={"Authorization": "Bearer test-token"})

        response = client.get(
            "/task/v1/tasks?search=milk",
            headers={"Authorization": "Bearer test-token"}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert data["data"][0]["title"] == "Buy groceries"

    def test_pagination(self):
        """Test pagination with limit and offset."""
        # Create 10 tasks
        for i in range(10):
            client.post(
                "/task/v1/tasks",
                json={"title": f"Task {i}"},
                headers={"Authorization": "Bearer test-token"}
            )

        # Get first page
        response = client.get(
            "/task/v1/tasks?limit=5&offset=0",
            headers={"Authorization": "Bearer test-token"}
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data["data"]) == 5
        assert data["total"] == 10
        assert data["limit"] == 5
        assert data["offset"] == 0

        # Get second page
        response = client.get(
            "/task/v1/tasks?limit=5&offset=5",
            headers={"Authorization": "Bearer test-token"}
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data["data"]) == 5
        assert data["total"] == 10

    def test_sort_by_priority(self):
        """Test sorting by priority."""
        client.post("/task/v1/tasks", json={"title": "Low", "priority": 10}, headers={"Authorization": "Bearer test-token"})
        client.post("/task/v1/tasks", json={"title": "High", "priority": 90}, headers={"Authorization": "Bearer test-token"})
        client.post("/task/v1/tasks", json={"title": "Med", "priority": 50}, headers={"Authorization": "Bearer test-token"})

        response = client.get(
            "/task/v1/tasks?sort_by=priority&order=desc",
            headers={"Authorization": "Bearer test-token"}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["data"][0]["title"] == "High"
        assert data["data"][1]["title"] == "Med"
        assert data["data"][2]["title"] == "Low"

    def test_sort_by_due_date_nulls_last(self):
        """Test sorting by due_date with nulls last."""
        client.post("/task/v1/tasks", json={"title": "No due date"}, headers={"Authorization": "Bearer test-token"})
        client.post("/task/v1/tasks", json={"title": "Due soon", "due_date": "2025-11-15T00:00:00Z"}, headers={"Authorization": "Bearer test-token"})
        client.post("/task/v1/tasks", json={"title": "Due later", "due_date": "2025-12-15T00:00:00Z"}, headers={"Authorization": "Bearer test-token"})

        response = client.get(
            "/task/v1/tasks?sort_by=due_date&order=asc",
            headers={"Authorization": "Bearer test-token"}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["data"][0]["title"] == "Due soon"
        assert data["data"][1]["title"] == "Due later"
        assert data["data"][2]["title"] == "No due date"


class TestUpdateTask:
    """Test PATCH /tasks/{id} endpoint."""

    def test_update_task_title(self):
        """Test updating a task's title."""
        # Create task
        create_response = client.post(
            "/task/v1/tasks",
            json={"title": "Old title"},
            headers={"Authorization": "Bearer test-token"}
        )
        task_id = create_response.json()["id"]

        # Update title
        response = client.patch(
            f"/task/v1/tasks/{task_id}",
            json={"title": "New title"},
            headers={"Authorization": "Bearer test-token"}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["title"] == "New title"
        assert "ETag" in response.headers

    def test_update_task_to_completed(self):
        """Test marking a task as completed sets completion_date."""
        create_response = client.post(
            "/task/v1/tasks",
            json={"title": "Todo", "completed": False},
            headers={"Authorization": "Bearer test-token"}
        )
        task_id = create_response.json()["id"]

        response = client.patch(
            f"/task/v1/tasks/{task_id}",
            json={"completed": True},
            headers={"Authorization": "Bearer test-token"}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["completed"] == True
        assert data["completion_date"] is not None

    def test_update_task_to_incomplete(self):
        """Test marking a task as incomplete clears completion_date."""
        create_response = client.post(
            "/task/v1/tasks",
            json={"title": "Done", "completed": True},
            headers={"Authorization": "Bearer test-token"}
        )
        task_id = create_response.json()["id"]

        response = client.patch(
            f"/task/v1/tasks/{task_id}",
            json={"completed": False},
            headers={"Authorization": "Bearer test-token"}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["completed"] == False
        assert data["completion_date"] is None

    def test_update_clear_nullable_fields(self):
        """Test clearing nullable fields by sending null."""
        create_response = client.post(
            "/task/v1/tasks",
            json={
                "title": "Task",
                "description": "Description",
                "due_date": "2025-12-01T00:00:00Z"
            },
            headers={"Authorization": "Bearer test-token"}
        )
        task_id = create_response.json()["id"]

        response = client.patch(
            f"/task/v1/tasks/{task_id}",
            json={"description": None, "due_date": None},
            headers={"Authorization": "Bearer test-token"}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["description"] is None
        assert data["due_date"] is None

    def test_update_with_if_match_success(self):
        """Test conditional update with matching ETag."""
        create_response = client.post(
            "/task/v1/tasks",
            json={"title": "Task"},
            headers={"Authorization": "Bearer test-token"}
        )
        task_id = create_response.json()["id"]

        # Get current ETag
        get_response = client.get(
            f"/task/v1/tasks/{task_id}",
            headers={"Authorization": "Bearer test-token"}
        )
        etag = get_response.headers["ETag"]

        # Update with matching ETag
        response = client.patch(
            f"/task/v1/tasks/{task_id}",
            json={"title": "Updated"},
            headers={
                "Authorization": "Bearer test-token",
                "If-Match": etag
            }
        )
        assert response.status_code == 200

    def test_update_with_if_match_failure(self):
        """Test conditional update with non-matching ETag fails."""
        create_response = client.post(
            "/task/v1/tasks",
            json={"title": "Task"},
            headers={"Authorization": "Bearer test-token"}
        )
        task_id = create_response.json()["id"]

        # Update with wrong ETag
        response = client.patch(
            f"/task/v1/tasks/{task_id}",
            json={"title": "Updated"},
            headers={
                "Authorization": "Bearer test-token",
                "If-Match": '"wrong-etag"'
            }
        )
        assert response.status_code == 412

    def test_update_nonexistent_task(self):
        """Test updating a non-existent task returns 404."""
        response = client.patch(
            "/task/v1/tasks/00000000-0000-0000-0000-000000000000",
            json={"title": "Updated"},
            headers={"Authorization": "Bearer test-token"}
        )
        assert response.status_code == 404


class TestDeleteTask:
    """Test DELETE /tasks/{id} endpoint."""

    def test_delete_existing_task(self):
        """Test deleting an existing task."""
        create_response = client.post(
            "/task/v1/tasks",
            json={"title": "To delete"},
            headers={"Authorization": "Bearer test-token"}
        )
        task_id = create_response.json()["id"]

        response = client.delete(
            f"/task/v1/tasks/{task_id}",
            headers={"Authorization": "Bearer test-token"}
        )
        assert response.status_code == 204

        # Verify task is gone
        get_response = client.get(
            f"/task/v1/tasks/{task_id}",
            headers={"Authorization": "Bearer test-token"}
        )
        assert get_response.status_code == 404

    def test_delete_cascades_to_children(self):
        """Test deleting a parent task also deletes its children."""
        # Create parent
        parent_response = client.post(
            "/task/v1/tasks",
            json={"title": "Parent"},
            headers={"Authorization": "Bearer test-token"}
        )
        parent_id = parent_response.json()["id"]

        # Create child
        child_response = client.post(
            "/task/v1/tasks",
            json={"title": "Child", "parent_id": parent_id},
            headers={"Authorization": "Bearer test-token"}
        )
        child_id = child_response.json()["id"]

        # Delete parent
        delete_response = client.delete(
            f"/task/v1/tasks/{parent_id}",
            headers={"Authorization": "Bearer test-token"}
        )
        assert delete_response.status_code == 204

        # Verify child is also gone
        get_child_response = client.get(
            f"/task/v1/tasks/{child_id}",
            headers={"Authorization": "Bearer test-token"}
        )
        assert get_child_response.status_code == 404

    def test_delete_nonexistent_task(self):
        """Test deleting a non-existent task returns 404."""
        response = client.delete(
            "/task/v1/tasks/00000000-0000-0000-0000-000000000000",
            headers={"Authorization": "Bearer test-token"}
        )
        assert response.status_code == 404


class TestExportTasks:
    """Test GET /tasks/export endpoint."""

    def test_export_empty_database(self):
        """Test exporting when no tasks exist."""
        response = client.get(
            "/task/v1/tasks/export",
            headers={"Authorization": "Bearer test-token"}
        )
        assert response.status_code == 200
        data = response.json()
        assert data == []

    def test_export_all_tasks(self):
        """Test exporting all tasks."""
        # Create some tasks
        for i in range(3):
            client.post(
                "/task/v1/tasks",
                json={"title": f"Task {i}"},
                headers={"Authorization": "Bearer test-token"}
            )

        response = client.get(
            "/task/v1/tasks/export",
            headers={"Authorization": "Bearer test-token"}
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 3

    def test_export_orders_parents_before_children(self):
        """Test export orders parent tasks before child tasks."""
        # Create parent
        parent_response = client.post(
            "/task/v1/tasks",
            json={"title": "Parent"},
            headers={"Authorization": "Bearer test-token"}
        )
        parent_id = parent_response.json()["id"]

        # Create child
        client.post(
            "/task/v1/tasks",
            json={"title": "Child", "parent_id": parent_id},
            headers={"Authorization": "Bearer test-token"}
        )

        response = client.get(
            "/task/v1/tasks/export",
            headers={"Authorization": "Bearer test-token"}
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2
        assert data[0]["title"] == "Parent"
        assert data[1]["title"] == "Child"
        assert data[1]["parent_id"] == parent_id


class TestImportTasks:
    """Test POST /tasks/import endpoint."""

    def test_import_single_task(self):
        """Test importing a single task."""
        import_data = [{
            "id": "c9a1cbd1-4e89-4e2a-8a1f-8f9a6f7d6b1b",
            "title": "Imported task",
            "description": "Test import",
            "completed": False,
            "archived": False,
            "priority": 50,
            "due_date": None,
            "completion_date": None,
            "parent_id": None,
            "tags": ["imported"],
            "metadata": {"source": "test"},
            "created_at": "2025-11-01T12:00:00Z",
            "updated_at": "2025-11-01T12:00:00Z"
        }]

        response = client.post(
            "/task/v1/tasks/import",
            json=import_data,
            headers={"Authorization": "Bearer test-token"}
        )
        assert response.status_code == 201
        data = response.json()
        assert data["imported_count"] == 1

        # Verify task was imported with correct ID
        get_response = client.get(
            f"/task/v1/tasks/{import_data[0]['id']}",
            headers={"Authorization": "Bearer test-token"}
        )
        assert get_response.status_code == 200
        task = get_response.json()
        assert task["title"] == "Imported task"
        assert task["created_at"] == "2025-11-01T12:00:00Z"

    def test_import_preserves_timestamps(self):
        """Test import preserves created_at and updated_at timestamps."""
        import_data = [{
            "id": "d1b2c3d4-e5f6-7890-1234-567890abcdef",
            "title": "Old task",
            "completed": False,
            "archived": False,
            "priority": 0,
            "parent_id": None,
            "created_at": "2020-01-01T00:00:00Z",
            "updated_at": "2020-01-02T00:00:00Z"
        }]

        response = client.post(
            "/task/v1/tasks/import",
            json=import_data,
            headers={"Authorization": "Bearer test-token"}
        )
        assert response.status_code == 201

        # Verify timestamps were preserved
        get_response = client.get(
            f"/task/v1/tasks/{import_data[0]['id']}",
            headers={"Authorization": "Bearer test-token"}
        )
        task = get_response.json()
        assert task["created_at"] == "2020-01-01T00:00:00Z"
        assert task["updated_at"] == "2020-01-02T00:00:00Z"

    def test_import_parent_child_relationship(self):
        """Test importing tasks with parent-child relationships."""
        import_data = [
            {
                "id": "parent-id-1234",
                "title": "Parent",
                "completed": False,
                "archived": False,
                "priority": 0,
                "parent_id": None,
                "created_at": "2025-11-01T00:00:00Z",
                "updated_at": "2025-11-01T00:00:00Z"
            },
            {
                "id": "child-id-5678",
                "title": "Child",
                "completed": False,
                "archived": False,
                "priority": 0,
                "parent_id": "parent-id-1234",
                "created_at": "2025-11-01T00:00:00Z",
                "updated_at": "2025-11-01T00:00:00Z"
            }
        ]

        response = client.post(
            "/task/v1/tasks/import",
            json=import_data,
            headers={"Authorization": "Bearer test-token"}
        )
        assert response.status_code == 201
        data = response.json()
        assert data["imported_count"] == 2

    def test_import_conflict_fail(self):
        """Test import with existing ID fails when on_conflict=fail."""
        # Create a task
        client.post(
            "/task/v1/tasks",
            json={"title": "Existing"},
            headers={"Authorization": "Bearer test-token"}
        )

        # Export to get the task
        export_response = client.get(
            "/task/v1/tasks/export",
            headers={"Authorization": "Bearer test-token"}
        )
        existing_task = export_response.json()[0]

        # Try to import with same ID
        response = client.post(
            "/task/v1/tasks/import?on_conflict=fail",
            json=[existing_task],
            headers={"Authorization": "Bearer test-token"}
        )
        assert response.status_code == 409

    def test_import_conflict_skip(self):
        """Test import skips existing IDs when on_conflict=skip."""
        # Create a task
        create_response = client.post(
            "/task/v1/tasks",
            json={"title": "Existing"},
            headers={"Authorization": "Bearer test-token"}
        )
        existing_id = create_response.json()["id"]

        # Try to import existing and new task
        import_data = [
            {
                "id": existing_id,
                "title": "Should be skipped",
                "completed": False,
                "archived": False,
                "priority": 0,
                "parent_id": None,
                "created_at": "2025-11-01T00:00:00Z",
                "updated_at": "2025-11-01T00:00:00Z"
            },
            {
                "id": "new-task-id",
                "title": "New task",
                "completed": False,
                "archived": False,
                "priority": 0,
                "parent_id": None,
                "created_at": "2025-11-01T00:00:00Z",
                "updated_at": "2025-11-01T00:00:00Z"
            }
        ]

        response = client.post(
            "/task/v1/tasks/import?on_conflict=skip",
            json=import_data,
            headers={"Authorization": "Bearer test-token"}
        )
        assert response.status_code == 201
        data = response.json()
        assert data["imported_count"] == 1

    def test_import_conflict_upsert(self):
        """Test import updates existing tasks when on_conflict=upsert."""
        # Create a task
        create_response = client.post(
            "/task/v1/tasks",
            json={"title": "Original", "priority": 10},
            headers={"Authorization": "Bearer test-token"}
        )
        task_id = create_response.json()["id"]

        # Import with same ID but different data
        import_data = [{
            "id": task_id,
            "title": "Updated via import",
            "completed": False,
            "archived": False,
            "priority": 90,
            "parent_id": None,
            "created_at": "2025-11-01T00:00:00Z",
            "updated_at": "2025-11-01T00:00:00Z"
        }]

        response = client.post(
            "/task/v1/tasks/import?on_conflict=upsert",
            json=import_data,
            headers={"Authorization": "Bearer test-token"}
        )
        assert response.status_code == 201

        # Verify task was updated
        get_response = client.get(
            f"/task/v1/tasks/{task_id}",
            headers={"Authorization": "Bearer test-token"}
        )
        task = get_response.json()
        assert task["title"] == "Updated via import"
        assert task["priority"] == 90

    def test_import_validate_only(self):
        """Test validate_only mode doesn't create tasks."""
        import_data = [{
            "id": "validate-test-id",
            "title": "Validate only",
            "completed": False,
            "archived": False,
            "priority": 0,
            "parent_id": None,
            "created_at": "2025-11-01T00:00:00Z",
            "updated_at": "2025-11-01T00:00:00Z"
        }]

        response = client.post(
            "/task/v1/tasks/import?validate_only=true",
            json=import_data,
            headers={"Authorization": "Bearer test-token"}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["imported_count"] == 1

        # Verify task was NOT created
        get_response = client.get(
            "/task/v1/tasks/validate-test-id",
            headers={"Authorization": "Bearer test-token"}
        )
        assert get_response.status_code == 404

    def test_import_duplicate_ids_in_payload(self):
        """Test import fails with duplicate IDs in the payload."""
        import_data = [
            {
                "id": "duplicate-id",
                "title": "First",
                "completed": False,
                "archived": False,
                "priority": 0,
                "parent_id": None,
                "created_at": "2025-11-01T00:00:00Z",
                "updated_at": "2025-11-01T00:00:00Z"
            },
            {
                "id": "duplicate-id",
                "title": "Second",
                "completed": False,
                "archived": False,
                "priority": 0,
                "parent_id": None,
                "created_at": "2025-11-01T00:00:00Z",
                "updated_at": "2025-11-01T00:00:00Z"
            }
        ]

        response = client.post(
            "/task/v1/tasks/import",
            json=import_data,
            headers={"Authorization": "Bearer test-token"}
        )
        assert response.status_code == 422


class TestExportImportRoundtrip:
    """Test export and import work together."""

    def test_export_import_roundtrip(self):
        """Test exporting and re-importing tasks preserves all data."""
        # Create some tasks with various configurations
        client.post(
            "/task/v1/tasks",
            json={
                "title": "Complex task",
                "description": "With description",
                "priority": 75,
                "due_date": "2025-12-01T17:00:00Z",
                "tags": ["work", "important"],
                "metadata": {"key": "value"},
                "completed": False
            },
            headers={"Authorization": "Bearer test-token"}
        )

        # Export
        export_response = client.get(
            "/task/v1/tasks/export",
            headers={"Authorization": "Bearer test-token"}
        )
        exported_tasks = export_response.json()

        # Clear database
        for task in exported_tasks:
            client.delete(
                f"/task/v1/tasks/{task['id']}",
                headers={"Authorization": "Bearer test-token"}
            )

        # Re-import
        import_response = client.post(
            "/task/v1/tasks/import",
            json=exported_tasks,
            headers={"Authorization": "Bearer test-token"}
        )
        assert import_response.status_code == 201

        # Verify all data is preserved
        list_response = client.get(
            "/task/v1/tasks",
            headers={"Authorization": "Bearer test-token"}
        )
        imported_tasks = list_response.json()["data"]

        assert len(imported_tasks) == len(exported_tasks)

        for original, imported in zip(exported_tasks, imported_tasks):
            assert original["id"] == imported["id"]
            assert original["title"] == imported["title"]
            assert original["description"] == imported["description"]
            assert original["priority"] == imported["priority"]
            assert original["tags"] == imported["tags"]
            assert original["metadata"] == imported["metadata"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
