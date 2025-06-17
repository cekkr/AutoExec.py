# AutoExec.py
# A script to automatically manage and run Python applications from Git repositories,
# with a built-in HTTP API for status monitoring.

import os
import sys
import time
import subprocess
import logging
import json
from multiprocessing import Process, Manager
from urllib.parse import urlparse
from http.server import BaseHTTPRequestHandler, HTTPServer
from collections import deque

# --- Configuration ---
SERVICES_FILE = "services.txt"
REPOS_DIR = "repos"
# How often the main loop checks for changes in services.txt (in seconds)
MAIN_LOOP_SLEEP = 5
# How often each service process checks for Git updates (in seconds)
GIT_CHECK_INTERVAL = 30
# API Server Configuration
API_ENABLED = True
API_HOST = "localhost"
API_PORT = 8000
MAX_LOG_ENTRIES = 20

# --- Logging Setup ---
# Main logger configuration
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] [%(processName)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stdout,
)


# --- Helper Classes and Functions ---

class SharedLogHandler(logging.Handler):
    """A logging handler that writes records to a shared list (from multiprocessing.Manager)."""
    def __init__(self, shared_list):
        super().__init__()
        self.shared_list = shared_list

    def emit(self, record):
        log_entry = self.format(record)
        self.shared_list.append(log_entry)
        # Keep the list trimmed to the max size
        while len(self.shared_list) > MAX_LOG_ENTRIES:
            self.shared_list.pop(0)

def run_command(command, cwd="."):
    """Executes a shell command and returns its output."""
    try:
        logging.debug(f"Running command: {' '.join(command)} in {cwd}")
        result = subprocess.run(
            command, cwd=cwd, check=True, capture_output=True, text=True, encoding='utf-8', errors='ignore'
        )
        return result.stdout.strip()
    except subprocess.CalledProcessError as e:
        logging.error(f"Command failed: {' '.join(command)}\nError: {e.stderr.strip()}")
        return None
    except FileNotFoundError:
        logging.error(f"Command not found: {command[0]}. Is it installed and in your PATH?")
        return None

def get_repo_name_from_url(url):
    """Extracts a repository name from a Git URL."""
    path = urlparse(url).path
    repo_name = os.path.splitext(os.path.basename(path))[0]
    return repo_name if repo_name else "unknown_repo"

def parse_services_file():
    """Parses the services.txt file and returns a dictionary of service configurations."""
    services = {}
    if not os.path.exists(SERVICES_FILE):
        logging.warning(f"'{SERVICES_FILE}' not found. No services to manage.")
        return services

    with open(SERVICES_FILE, "r") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            
            parts = line.split()
            url = parts[0]
            branch = parts[1] if len(parts) > 1 else "main"
            dir_name = parts[2] if len(parts) > 2 else get_repo_name_from_url(url)
            repo_path = os.path.abspath(os.path.join(REPOS_DIR, dir_name))
            
            services[repo_path] = {"url": url, "branch": branch, "path": repo_path}
    return services

# --- API Server Implementation ---

def create_api_handler(shared_status):
    """Factory function to create the request handler class with shared state."""
    class StatusAPIRequestHandler(BaseHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            self.shared_status = shared_status
            super().__init__(*args, **kwargs)

        def do_GET(self):
            if self.path == '/status':
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                # Convert Manager objects to regular Python objects for JSON serialization
                status_copy = {
                    "manager_pid": self.shared_status.get("manager_pid"),
                    "api_url": f"http://{API_HOST}:{API_PORT}/status",
                    "services": {
                        path: {k: list(v) if isinstance(v, list.__class__) else v for k, v in service.items()}
                        for path, service in self.shared_status.get("services", {}).items()
                    }
                }
                self.wfile.write(json.dumps(status_copy, indent=4).encode('utf-8'))
            else:
                self.send_response(404)
                self.end_headers()
                self.wfile.write(b'Not Found')
    return StatusAPIRequestHandler

def run_api_server(shared_status, host, port):
    """The target function to run the HTTP server in its own process."""
    try:
        handler = create_api_handler(shared_status)
        httpd = HTTPServer((host, port), handler)
        logging.info(f"API server started on http://{host}:{port}")
        httpd.serve_forever()
    except Exception as e:
        logging.critical(f"API server failed: {e}")


# --- Service Management ---

def manage_service(service_config, shared_status_dict):
    """
    The main function for a service process. It manages cloning, updating,
    and running the target Python script, while reporting status to a shared dictionary.
    """
    repo_path = service_config["path"]
    process_name = os.path.basename(repo_path)
    
    # Setup logger for this specific service
    service_logger = logging.getLogger(process_name)
    service_logger.setLevel(logging.INFO)
    # Prevent logs from propagating to the root logger to avoid duplicates
    service_logger.propagate = False 

    # Prepare shared state for this service
    with Manager() as manager: # A sub-manager for lists and dicts
        shared_logs = manager.list()
        handler = SharedLogHandler(shared_logs)
        handler.setFormatter(logging.Formatter("[%(asctime)s] [%(levelname)s] %(message)s"))
        service_logger.addHandler(handler)

        service_status = manager.dict({
            "status": "initializing",
            "url": service_config["url"],
            "branch": service_config["branch"],
            "repo_path": repo_path,
            "script_to_run": None,
            "service_manager_pid": os.getpid(),
            "script_pid": None,
            "logs": shared_logs
        })
        shared_status_dict[repo_path] = service_status

        # 1. Clone repo if needed
        service_status["status"] = "cloning"
        if not os.path.isdir(os.path.join(repo_path, ".git")):
            service_logger.info(f"Cloning {service_config['url']}...")
            os.makedirs(repo_path, exist_ok=True)
            clone_cmd = ["git", "clone", "--branch", service_config["branch"], service_config["url"], "."]
            if run_command(clone_cmd, cwd=repo_path) is None:
                service_logger.error("Failed to clone repository. Shutting down service manager.")
                service_status["status"] = "failed"
                return
        
        child_process = None
        
        while True:
            try:
                # 2. Check for updates
                service_logger.debug("Fetching updates from remote...")
                run_command(["git", "fetch"], cwd=repo_path)
                local_hash = run_command(["git", "rev-parse", "HEAD"], cwd=repo_path)
                remote_hash = run_command(["git", "rev-parse", f"origin/{service_config['branch']}"], cwd=repo_path)
                has_updates = local_hash != remote_hash and remote_hash is not None

                # 3. Handle process execution
                if child_process and child_process.poll() is not None:
                    service_logger.warning("Script has terminated unexpectedly. Restarting...")
                    service_status["status"] = "crashed"
                    service_status["script_pid"] = None
                    child_process = None

                if has_updates:
                    service_status["status"] = "updating"
                    service_logger.info("New updates found. Restarting script.")
                    if child_process:
                        service_logger.info("Terminating running script...")
                        child_process.terminate()
                        child_process.wait()
                        service_status["script_pid"] = None
                        child_process = None
                    
                    service_logger.info("Pulling latest changes...")
                    if run_command(["git", "pull", "origin", service_config["branch"]], cwd=repo_path) is None:
                        service_logger.error("Failed to pull updates. Retrying later.")
                        time.sleep(GIT_CHECK_INTERVAL)
                        continue

                if not child_process:
                    autoexec_path = os.path.join(repo_path, "autoexec.txt")
                    if not os.path.exists(autoexec_path):
                        service_logger.error("'autoexec.txt' not found. Cannot start script.")
                        time.sleep(GIT_CHECK_INTERVAL)
                        continue
                    
                    with open(autoexec_path, "r") as f:
                        script_to_run = f.read().strip()
                    service_status["script_to_run"] = script_to_run

                    script_path = os.path.join(repo_path, script_to_run)
                    if not script_to_run or not os.path.exists(script_path):
                        service_logger.error(f"Script '{script_to_run}' not found or invalid.")
                        time.sleep(GIT_CHECK_INTERVAL)
                        continue

                    service_logger.info(f"Starting script: python {script_to_run}")
                    child_process = subprocess.Popen([sys.executable, script_to_run], cwd=repo_path)
                    service_status["status"] = "running"
                    service_status["script_pid"] = child_process.pid
                
                time.sleep(GIT_CHECK_INTERVAL)

            except Exception as e:
                service_logger.error(f"Unexpected error in service manager: {e}")
                if child_process:
                    child_process.terminate()
                service_status["status"] = "failed"
                time.sleep(GIT_CHECK_INTERVAL)


# --- Main Application Logic ---

def main():
    """Main loop that reads services.txt and manages service processes and the API server."""
    logging.info("--- AutoExec.py Manager Started ---")
    if not os.path.exists(REPOS_DIR):
        os.makedirs(REPOS_DIR)
        
    if run_command(["git", "--version"]) is None:
        logging.critical("Git is not installed or not in PATH. Exiting.")
        return

    manager = Manager()
    shared_status = manager.dict()
    shared_status["manager_pid"] = os.getpid()
    shared_status["services"] = manager.dict()
    
    managed_processes = {}
    api_process = None

    if API_ENABLED:
        api_process = Process(target=run_api_server, args=(shared_status, API_HOST, API_PORT), name="APIServer")
        api_process.start()
        managed_processes["_api_server"] = api_process

    try:
        while True:
            desired_services = parse_services_file()
            desired_paths = set(desired_services.keys())
            current_paths = set(shared_status["services"].keys())

            # Stop services removed from config
            for path in current_paths - desired_paths:
                logging.info(f"Service for {path} removed from config. Stopping...")
                if path in managed_processes:
                    process = managed_processes.pop(path)
                    if process.is_alive():
                        process.terminate()
                        process.join(timeout=5)
                # Remove from shared status
                if path in shared_status["services"]:
                    del shared_status["services"][path]
                logging.info(f"Process for {path} stopped.")

            # Start new services
            for path in desired_paths - current_paths:
                logging.info(f"New service for {path} found. Starting...")
                service_config = desired_services[path]
                process_name = os.path.basename(path)
                process = Process(target=manage_service, args=(service_config, shared_status["services"]), name=process_name)
                process.start()
                managed_processes[path] = process
            
            # Health check on processes
            for path, process in list(managed_processes.items()):
                if path == "_api_server": continue # Don't respawn API server here
                if not process.is_alive():
                    logging.warning(f"Process for {path} has died unexpectedly. It will be restarted.")
                    service_config = desired_services[path]
                    new_process = Process(target=manage_service, args=(service_config, shared_status["services"]), name=process.name)
                    new_process.start()
                    managed_processes[path] = new_process

            time.sleep(MAIN_LOOP_SLEEP)

    except KeyboardInterrupt:
        logging.info("Shutdown signal received. Terminating all managed services...")
    finally:
        for name, process in managed_processes.items():
            if process.is_alive():
                logging.info(f"Stopping process for {name}...")
                process.terminate()
                process.join(timeout=5)
        logging.info("--- AutoExec.py Manager Shut Down ---")

if __name__ == "__main__":
    main()