"""Carga los datos de ejemplo: python seed.py

Es idempotente: si ya hay habitaciones, no duplica nada.
Nota: create_app() ya siembra los datos automáticamente al primer
arranque si la base de datos está vacía, así que este script sobre
todo sirve para comprobar que los datos están ahí.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from turitec import create_app  # noqa: E402
from turitec.infrastructure.db import (  # noqa: E402
    FacturaModel, HabitacionModel, ReservacionModel, db, seed_data,
)

if __name__ == "__main__":
    app = create_app()
    with app.app_context():
        db.create_all()
        creado = seed_data()
        n_hab = HabitacionModel.query.count()
        n_res = ReservacionModel.query.count()
        n_fac = FacturaModel.query.count()
        print("Datos de ejemplo creados." if creado else "La base de datos ya tenía datos; no se duplicó nada.")
        print(f"Hotel demo listo: {n_hab} habitaciones, {n_res} reservaciones, {n_fac} factura(s).")
        print("Usuarios demo: admin@turitec.mx / admin123  |  recepcion@turitec.mx / recepcion123")
