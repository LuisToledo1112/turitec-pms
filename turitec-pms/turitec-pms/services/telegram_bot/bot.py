"""Chatbot de Telegram de Turitec PMS.

Uso (desde la carpeta turitec-pms, con el entorno virtual activado):

    # 1. Poner el token y el id del chat en el archivo .env (ver README).
    # 2. Probar que el bot puede escribirte:
    python services/telegram_bot/bot.py --test
    # 3. Averiguar tu id de chat (primero escribe /start al bot en Telegram):
    python services/telegram_bot/bot.py --get-id
    # 4. Poner el bot a escuchar (en otra terminal, la app web no tiene
    #    que estar corriendo para que el bot responda, usa su propia BD):
    python services/telegram_bot/bot.py

Comandos que entiende el bot en Telegram:
    Para clientes (cualquiera): /start, /ayuda, /habitaciones, /precios,
    /disponibilidad
    Solo para el personal (chat de TELEGRAM_CHAT_ID): /reservas, /ocupacion

No necesita instalar nada más: usa solo la librería estándar de Python.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Permite ejecutar el script desde cualquier carpeta del proyecto.
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from src.turitec import create_app  # noqa: E402
from src.turitec.infrastructure.db import (  # noqa: E402
    SQLAlchemyFacturaRepository, SQLAlchemyHabitacionRepository,
    SQLAlchemyReservacionRepository,
)
from src.turitec.infrastructure.telegram import (  # noqa: E402
    ConsultaTuritec, TelegramNotificador, TuritecBot,
)


def _bot_y_app():
    app = create_app()
    with app.app_context():
        consulta = ConsultaTuritec(
            SQLAlchemyHabitacionRepository(),
            SQLAlchemyReservacionRepository(),
            SQLAlchemyFacturaRepository(),
        )
        notificador = TelegramNotificador()
        # Se le pasa la app al bot para que cada mensaje se responda dentro
        # de su contexto (las consultas a la base de datos lo necesitan).
        return app, TuritecBot(notificador, consulta, app=app), notificador


def main() -> int:
    parser = argparse.ArgumentParser(description="Chatbot de Telegram de Turitec PMS")
    parser.add_argument("--test", action="store_true", help="Enviar un mensaje de prueba al chat configurado")
    parser.add_argument("--get-id", action="store_true", help="Mostrar los chats que han escrito al bot (para averiguar tu id)")
    args = parser.parse_args()

    app, bot, notificador = _bot_y_app()

    if args.test:
        if not notificador.configurado:
            print("Telegram no está configurado.")
            print("Pon TELEGRAM_BOT_TOKEN y TELEGRAM_CHAT_ID en el archivo .env")
            print("(ver README, sección Chatbot de Telegram) y vuelve a intentarlo.")
            return 1
        ok = notificador.enviar(
            "✅ <b>Turitec PMS</b>\nTu chatbot de Telegram está conectado y los avisos funcionan."
        )
        print("Mensaje de prueba enviado." if ok else "No se pudo enviar el mensaje. Revisa el token y el id del chat.")
        return 0 if ok else 1

    if args.get_id:
        if not notificador.token:
            print("Falta TELEGRAM_BOT_TOKEN en el archivo .env.")
            return 1
        print("Escribe /start al bot en Telegram y vuelve a correr este comando.")
        print("Chats que han escrito al bot:")
        chats = notificador.obtener_chat_ids_recientes()
        if not chats:
            print("  (ninguno todavía)")
        for chat in chats:
            print(f"  chat_id={chat['chat_id']}  tipo={chat['tipo']}  nombre={chat['nombre']}  usuario=@{chat['usuario']}")
        return 0

    bot.correr()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
