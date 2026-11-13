@echo off
REM Inicia la app web y el chatbot de Telegram de Turitec, cada uno en su ventana.
REM
REM Para que arranquen solos al iniciar Windows:
REM   1. Clic derecho sobre este archivo > Crear acceso directo.
REM   2. Pulsa Win + R, escribe shell:startup y pulsa Enter.
REM   3. Mueve el acceso directo a esa carpeta.
REM
REM Si cambias la carpeta del proyecto, cambia la ruta de abajo.
REM La app y el bot usan el Python del venv y el .env de esa carpeta.

cd /d C:\Users\Alex5\Downloads\turitec-pms
start "Turitec PMS - App web" "C:\Users\Alex5\Downloads\turitec-pms\venv\Scripts\python.exe" run.py
start "Turitec Bot - Telegram" "C:\Users\Alex5\Downloads\turitec-pms\venv\Scripts\python.exe" services\telegram_bot\bot.py
