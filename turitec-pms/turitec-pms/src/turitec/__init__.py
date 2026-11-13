"""Factory de la aplicación Flask (Turitec PMS)."""
import os

from flask import Flask

from config import Config

from .infrastructure.db import db, seed_data
from .infrastructure.web.routes import bp


def create_app(config_class=Config):
    app = Flask(
        __name__,
        template_folder="infrastructure/web/templates",
        static_folder="infrastructure/web/static",
    )
    app.config.from_object(config_class)
    # Permite DATABASE_URL desde .env si python-dotenv está instalado
    try:
        from dotenv import load_dotenv

        load_dotenv()
        if os.environ.get("DATABASE_URL"):
            app.config["SQLALCHEMY_DATABASE_URI"] = os.environ["DATABASE_URL"]
    except Exception:
        pass

    db.init_app(app)
    app.register_blueprint(bp)

    with app.app_context():
        db.create_all()
        # Seed automático al primer arranque si la BD está vacía
        try:
            seed_data()
        except Exception as exc:  # noqa: BLE001 - no bloquear el arranque
            app.logger.warning("Seed automático omitido: %s", exc)
    return app
