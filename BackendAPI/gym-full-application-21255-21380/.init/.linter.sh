#!/bin/bash
set -e
cd /home/kavia/workspace/code-generation/gym-full-application-21255-21380/BackendAPI
# Prefer local venv flake8 if exists, else fallback to system flake8
if [ -x "venv/bin/flake8" ]; then
  FLAKE="venv/bin/flake8"
else
  FLAKE="flake8"
fi
$FLAKE src
