@echo off
REM Inicia el chatbot de Telegram de Turitec.
REM
REM Para que arranque solo al iniciar Windows:
REM   1. Clic derecho sobre este archivo > Crear acceso directo.
REM   2. Pulsa Win + R, escribe shell:startup y pulsa Enter.
REM   3. Mueve el acceso directo a esa carpeta.
REM
REM Si cambias la carpeta del proyecto, cambia la ruta de abajo.
REM El bot usa el Python del venv y el .env de esa carpeta.

cd /d C:\Users\Alex5\Downloads\turitec-pms
title Turitec Bot - Telegram
"C:\Users\Alex5\Downloads\turitec-pms\venv\Scripts\python.exe" services\telegram_bot\bot.py

echo.
echo El bot se detuvo. Pulsa cualquier tecla para cerrar esta ventana.
pause >nul
