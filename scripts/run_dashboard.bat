@echo off
cd /d "%~dp0.."
py -m uv run streamlit run src\kalshi_agent\dashboard\app.py
