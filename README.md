# AutoExec.py 

## A Git-Based Process Manager

`AutoExec.py` is a powerful and resilient process manager designed to automate the deployment and maintenance of Python applications hosted in Git repositories. It runs continuously, monitoring your services for code changes, and ensures they are always running the latest version. It's built to be robust, handling application crashes and configuration changes in real-time without requiring a restart of the main manager.

## Features

-   **Dynamic Service Management**: Add, remove, or update services by simply editing the `services.txt` file. The manager detects changes in real-time.
-   **Automatic Git Operations**: Automatically clones new repositories and periodically checks for updates in the specified branch.
-   **Automated Updates**: When updates are found, the script automatically terminates the running application, pulls the latest changes, and restarts it.
-   **Process Isolation**: Each service runs in its own dedicated process, preventing a crash in one application from affecting the manager or other services.
-   **Crash Recovery**: If a managed application crashes for any reason, `AutoExec.py` will automatically restart it.
-   **HTTP API for Monitoring**: An integrated HTTP server provides a `/status` endpoint to get real-time JSON data about running services, their status, PIDs, and latest log entries.
-   **Configurable**: Easily configure check intervals, directories, and API settings via constants at the top of the script.
-   **Detailed Logging**: Provides clear, timestamped logs for all management actions, making it easy to monitor and debug.

## How It Works

The system is composed of a central manager (`AutoExec.py`) and the individual services it supervises, with an optional API process for monitoring.

1.  **The Main Manager Loop**:
    -   The `AutoExec.py` script starts, launches the API server (if enabled), and enters an infinite loop.
    -   It periodically re-reads the `services.txt` file to get the "desired state" of services.
    -   It compares this list with the services it is currently running and synchronizes the state:
        -   **New Services**: Spawns a new management process for any new service found.
        -   **Removed Services**: Terminates the process for any service removed from the file.
        -   **Crashed Processes**: Restarts the management process for any service that has died unexpectedly.

2.  **The Individual Service Process**:
    -   Each service is managed by its own child process, which ensures isolation and updates a shared status dictionary.
    -   **Initial Setup**: If the service's repository doesn't exist locally, the process clones it from the specified Git URL.
    -   **Execution Loop**: The process enters its own loop to:
        -   **Check for Updates**: Periodically runs `git fetch` to see if there are new commits on the remote branch.
        -   **Run the Application**: Reads the target script name from the repository's `autoexec.txt` file and launches it as a subprocess.
        -   **Monitor Health**: Constantly checks if the application script is still running. If it has crashed, it's flagged for restart.
        -   **Apply Updates**: If new Git commits are detected, it terminates the application, runs `git pull`, and restarts the script with the new code.

3.  **The API Process**:
    -   Runs as a separate, lightweight HTTP server.
    -   It has read-only access to the shared status dictionary that is maintained by the service processes.
    -   When a request is made to the `/status` endpoint, it reads the current state from the shared dictionary and serves it as a JSON response.

## Setup and Usage

### Prerequisites

-   Python 3.6+
-   Git must be installed on your system and accessible via the command line (i.e., its path must be in the `PATH` environment variable).

### 1. File Structure

Place the `AutoExec.py` script in a directory. This will be your main working directory.

```
/your-project-folder/
|
|-- AutoExec.py
|-- services.txt
|
|-- repos/          <-- This directory will be created automatically
```

### 2. Configure `services.txt`

Create a `services.txt` file in the same directory as `AutoExec.py`. Each line in this file defines a service to manage.

**Line Format:**
`repository_url [branch] [directory_name]`

-   `repository_url` (Required): The HTTPS or SSH URL of the Git repository.
-   `branch` (Optional): The branch or tag to track. Defaults to `main`.
-   `directory_name` (Optional): The name for the subdirectory inside `repos/`. If not provided, it will be inferred from the repository URL.

**Example `services.txt`:**

```
# Lines starting with # are comments and will be ignored.

# Service 1: Clones from the 'main' branch into 'repos/my-api-service/'
https://github.com/your-username/my-api-service.git

# Service 2: Tracks the 'develop' branch and clones into 'repos/data-processor/'
https://github.com/your-username/data-processor.git develop

# Service 3: Tracks a specific version tag and clones into a custom directory name
https://github.com/your-username/legacy-app.git v1.5.2 legacy-v1
```

### 3. Create `autoexec.txt` in Your Repositories

Inside **each** of the Git repositories you want to manage, create a file named `autoexec.txt`. This file must contain a single line specifying the name of the Python script that should be executed.

**Example `autoexec.txt` inside `my-api-service` repository:**

```
app.py
```

### 4. Run the Manager

Open your terminal, navigate to the directory containing `AutoExec.py`, and run the script:

```bash
python AutoExec.py
```

The manager will start, and you will see log output in your console. To stop the manager and all services gracefully, press `Ctrl+C`.

## HTTP Monitoring API

`AutoExec.py` includes a built-in web server to monitor the status of all managed services. By default, it runs on `http://localhost:8000`.

**Endpoint**: `GET /status`

Making a GET request to this endpoint will return a JSON object containing detailed information about the manager and each service.

**Example JSON Response:**
```json
{
    "manager_pid": 24510,
    "api_url": "http://localhost:8000/status",
    "services": {
        "/path/to/AutoExec/repos/my-api-service": {
            "status": "running",
            "url": "https://github.com/your-username/my-api-service.git",
            "branch": "main",
            "repo_path": "/path/to/AutoExec/repos/my-api-service",
            "script_to_run": "app.py",
            "service_manager_pid": 24517,
            "script_pid": 24520,
            "logs": [
                "[2025-06-17 15:30:00] [INFO] Starting script: python app.py"
            ]
        },
        "/path/to/AutoExec/repos/data-processor": {
            "status": "crashed",
            "url": "https://github.com/your-username/data-processor.git",
            "branch": "develop",
            "repo_path": "/path/to/AutoExec/repos/data-processor",
            "script_to_run": "main.py",
            "service_manager_pid": 24518,
            "script_pid": null,
            "logs": [
                "[2025-06-17 15:32:00] [WARNING] Script has terminated unexpectedly. Restarting..."
            ]
        }
    }
}
```

### API Response Fields

| Field                 | Description                                                                                              |
| --------------------- | -------------------------------------------------------------------------------------------------------- |
| `manager_pid`         | The Process ID of the main `AutoExec.py` script.                                                         |
| `api_url`             | The full URL of the status endpoint.                                                                     |
| `services`            | An object containing all managed services, keyed by their absolute local path.                           |
| `status`              | The current state: `initializing`, `cloning`, `running`, `crashed`, `updating`, `failed`.                |
| `url`, `branch`       | The Git configuration for the service.                                                                   |
| `repo_path`           | The absolute local path to the cloned repository.                                                        |
| `script_to_run`       | The name of the Python script being executed.                                                            |
| `service_manager_pid` | The Process ID of the dedicated process managing this specific service.                                  |
| `script_pid`          | The Process ID of the user's application script (e.g., `app.py`). `null` if not running.                 |
| `logs`                | A list of the most recent log entries related to management actions for this service.                    |

## Configuration

You can easily modify the script's behavior by changing the constant values at the top of `AutoExec.py`.

| Constant             | Description                                                            | Default Value      |
| -------------------- | ---------------------------------------------------------------------- | ------------------ |
| `SERVICES_FILE`      | The name of the service configuration file.                            | `"services.txt"`   |
| `REPOS_DIR`          | The directory where repositories are cloned.                           | `"repos"`          |
| `MAIN_LOOP_SLEEP`    | The interval (seconds) for the main loop to check for changes.         | `5`                |
| `GIT_CHECK_INTERVAL` | The interval (seconds) for each service to check for Git updates.      | `30`               |
| `API_ENABLED`        | Set to `True` or `False` to enable or disable the API server.          | `True`             |
| `API_HOST`           | The host address for the API server to listen on.                      | `"localhost"`      |
| `API_PORT`           | The port for the API server.                                           | `8000`             |
| `MAX_LOG_ENTRIES`    | The maximum number of log entries to store per service for the API.    | `20`               |