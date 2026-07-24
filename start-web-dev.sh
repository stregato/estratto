#!/bin/bash
# Estratto Web UI startup script with auto-reload for development

cd "$(dirname "$0")"
source venv/bin/activate

# Use uvicorn CLI with --reload for auto-reload on code changes
# This watches Python files and automatically reloads when they change
export ESTRATTO_CONFIG_PATH="config.yaml"
uvicorn estratto.webapp:create_app --factory --host 0.0.0.0 --port 8001 --reload
