# Task Server - Single User

A simple, self-hosted task management API server designed for single-user deployments. This server implements a comprehensive task API with support for hierarchical tasks, tags, priorities, filtering, search, and import/export functionality.

## Features

- **Full Task CRUD Operations**: Create, read, update, and delete tasks
- **Hierarchical Tasks**: Support for parent-child task relationships with cascading deletes
- **Advanced Filtering**: Filter by completion status, archived status, priority, tags, due dates, and more
- **Full-Text Search**: Search across task titles and descriptions
- **Tag Management**: Case-insensitive tag filtering with support for multiple tags
- **Sorting & Pagination**: Sort by various fields with configurable ordering and pagination
- **Import/Export**: Export all tasks and import them to another instance
- **Optimistic Locking**: ETags and conditional updates for concurrent access safety
- **Bearer Token Authentication**: Simple token-based auth from config file
- **SQLite Database**: Lightweight, file-based database with no external dependencies

## Quick Start

### Installation

1. Install dependencies:
```bash
pip install -r requirements.txt
```

2. Configure authentication tokens in `config.yaml`:
```yaml
auth:
  tokens:
    - "your-secret-token-here"
    - "another-token-if-needed"

database:
  path: "tasks.db"

server:
  host: "0.0.0.0"
  port: 8000
```

3. Run the server:
```bash
python server.py
```

The server will start on `http://localhost:8000` and automatically create the database on first run.

### API Documentation

Once running, visit `http://localhost:8000/docs` for interactive API documentation (Swagger UI) or `http://localhost:8000/redoc` for ReDoc-style documentation.

## Usage Examples

All requests require a Bearer token in the Authorization header:

```bash
curl -H "Authorization: Bearer your-secret-token-here" http://localhost:8000/task/v1/tasks
```

### Create a Task

```bash
curl -X POST http://localhost:8000/task/v1/tasks \
  -H "Authorization: Bearer your-secret-token-here" \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Buy groceries",
    "description": "Milk, eggs, bread",
    "priority": 50,
    "due_date": "2025-11-15T17:00:00Z",
    "tags": ["shopping", "home"]
  }'
```

### List Tasks

```bash
# List all root-level tasks
curl http://localhost:8000/task/v1/tasks \
  -H "Authorization: Bearer your-secret-token-here"

# Filter by tags and completion status
curl "http://localhost:8000/task/v1/tasks?tag=work&completed=false" \
  -H "Authorization: Bearer your-secret-token-here"
```

### Update a Task

```bash
curl -X PATCH http://localhost:8000/task/v1/tasks/{task-id} \
  -H "Authorization: Bearer your-secret-token-here" \
  -H "Content-Type: application/json" \
  -d '{"completed": true}'
```

### Export All Tasks

```bash
curl http://localhost:8000/task/v1/tasks/export \
  -H "Authorization: Bearer your-secret-token-here" \
  > tasks_backup.json
```

### Import Tasks

```bash
curl -X POST http://localhost:8000/task/v1/tasks/import \
  -H "Authorization: Bearer your-secret-token-here" \
  -H "Content-Type: application/json" \
  -d @tasks_backup.json
```

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/task/v1/tasks` | Create a new task |
| GET | `/task/v1/tasks` | List tasks with filtering |
| GET | `/task/v1/tasks/{id}` | Get a single task |
| PATCH | `/task/v1/tasks/{id}` | Update a task |
| DELETE | `/task/v1/tasks/{id}` | Delete a task (cascades to children) |
| GET | `/task/v1/tasks/export` | Export all tasks |
| POST | `/task/v1/tasks/import` | Import tasks in bulk |

## Data Storage

This server uses **SQLite** for data storage:
- Simple file-based database (`tasks.db` by default)
- No separate database server required
- Automatic schema creation on first run
- Foreign key constraints enabled for referential integrity
- Suitable for single-user workloads

To back up your data, simply copy the `tasks.db` file or use the export endpoint.

## Testing

Run the comprehensive integration test suite:

```bash
pytest test_api.py -v
```

The test suite includes 42 tests covering:
- Authentication
- CRUD operations
- Filtering and search
- Sorting and pagination
- Parent-child relationships
- Export/import functionality
- Error handling

## Architecture

```
TaskServerSingleUser/
├── config.yaml          # Configuration file
├── database.py          # Database connection setup
├── models.py            # SQLAlchemy models and Pydantic schemas
├── server.py            # FastAPI application and routes
├── test_api.py          # Integration test suite
├── requirements.txt     # Python dependencies
└── tasks.db            # SQLite database (auto-created)
```

## Security Note

This server is designed for single-user, self-hosted deployments. Authentication uses simple bearer tokens from the config file. For multi-user or production deployments, consider:
- Using proper OAuth2 or JWT authentication
- Enabling HTTPS/TLS
- Implementing rate limiting
- Adding audit logging

## License

This is a single-user task server with hardcoded authentication. Auth keys are configured in a YAML file, and only a single database is ever created.
