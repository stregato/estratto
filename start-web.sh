#!/bin/bash
# Estratto Web UI startup script

cd "$(dirname "$0")"
source venv/bin/activate
python -m estratto.main web
