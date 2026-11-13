"""Punto de entrada: python run.py  ->  http://127.0.0.1:5000"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from turitec import create_app  # noqa: E402

app = create_app()

if __name__ == "__main__":
    print("Turitec PMS corriendo en http://127.0.0.1:5000")
    print("Usuarios demo: admin@turitec.mx / admin123  |  recepcion@turitec.mx / recepcion123")
    app.run(host="127.0.0.1", port=5000, debug=True)
