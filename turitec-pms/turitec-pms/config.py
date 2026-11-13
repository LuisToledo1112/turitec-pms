"""Configuración de Turitec PMS.

Por defecto usa SQLite (turitec.db) para que la demo corra sin instalar
PostgreSQL. Para usar PostgreSQL, define DATABASE_URL, por ejemplo:
    DATABASE_URL=postgresql://turitec:turitec123@localhost:5432/turitec
"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "turitec-dev-secret-cambiar-en-produccion")
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "DATABASE_URL",
        f"sqlite:///{BASE_DIR / 'turitec.db'}",
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    # Nombre del hotel demo
    HOTEL_NOMBRE = os.environ.get("HOTEL_NOMBRE", "Posada Turitec")
    IVA = 0.16
    # Chatbot de Telegram (ver README, sección Chatbot de Telegram).
    # El token y el id del chat van en el archivo .env, nunca en el código.
    # Sin token o sin chat id, los avisos simplemente no se envían.
    TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
    TELEGRAM_ENABLED = os.environ.get("TELEGRAM_ENABLED", "true").strip().lower() in ("1", "true", "si", "sí")
