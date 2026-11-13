"""Tests de roles: administrador vs recepción (empleado)."""
import json
import os
import tempfile

import pytest

os.environ["DATABASE_URL"] = "sqlite:///" + tempfile.mktemp(suffix=".db")

from src.turitec import create_app  # noqa: E402
from src.turitec.infrastructure.db import HabitacionModel, HuespedModel, ProductoModel, ReservacionModel  # noqa: E402


def _login(c, email, password):
    c.post("/login", data={"email": email, "password": password})


@pytest.fixture
def admin():
    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as c:
        _login(c, "admin@turitec.mx", "admin123")
        yield c, app


@pytest.fixture
def recepcion():
    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as c:
        _login(c, "recepcion@turitec.mx", "recepcion123")
        yield c, app


def _texto(r):
    return r.get_data(as_text=True)


def test_recepcion_no_ve_botones_de_admin(recepcion):
    c, app = recepcion
    r = c.get("/habitaciones")
    assert "Agregar habitación" not in _texto(r)
    assert "Editar" not in _texto(r)
    r = c.get("/reservaciones")
    assert "Nueva reservación" in _texto(r)  # recepción SÍ puede crear reservaciones
    assert "Check-in" in _texto(r)  # sí ve el check-in
    assert "Editar" in _texto(r)  # recepción SÍ puede editar reservaciones
    assert "eliminar_reservacion" not in _texto(r)
    assert "cancelar_reservacion" not in _texto(r)
    r = c.get("/caja")
    assert "Administrar productos" not in _texto(r)
    assert "Cobrar" in _texto(r)
    r = c.get("/dashboard")
    assert "Reportes" not in _texto(r)


def test_recepcion_no_puede_agregar_ni_eliminar_habitacion(recepcion):
    c, app = recepcion
    with app.app_context():
        antes = HabitacionModel.query.count()
    r = c.post("/habitaciones", data={
        "numero": "999", "tipo": "Suite", "precio_noche": "5000",
        "capacidad": "2", "estado": "libre", "descripcion": "No debe crearse.",
    }, follow_redirects=False)
    assert r.status_code == 302
    with app.app_context():
        assert HabitacionModel.query.count() == antes
        assert HabitacionModel.query.filter_by(numero="999").first() is None
    r = c.get("/habitaciones/1/editar", follow_redirects=False)
    assert r.status_code == 302
    r = c.post("/habitaciones/1/eliminar", follow_redirects=False)
    assert r.status_code == 302
    with app.app_context():
        assert HabitacionModel.query.get(1) is not None


def test_recepcion_no_puede_productos_reportes_ni_corte(recepcion):
    c, app = recepcion
    with app.app_context():
        antes = ProductoModel.query.count()
    assert c.get("/caja/productos", follow_redirects=False).status_code == 302
    c.post("/caja/productos", data={
        "nombre": "No debe crearse", "categoria": "alimentos", "precio": "10",
        "costo": "0", "descripcion": "", "stock": "",
    }, follow_redirects=False)
    with app.app_context():
        assert ProductoModel.query.count() == antes
        assert ProductoModel.query.filter_by(nombre="No debe crearse").first() is None
    assert c.get("/reportes", follow_redirects=False).status_code == 302
    assert c.get("/reportes/ventas", follow_redirects=False).status_code == 302
    assert c.get("/caja/corte", follow_redirects=False).status_code == 302
    assert c.post("/caja/corte/cerrar", follow_redirects=False).status_code == 302


def test_recepcion_si_puede_vender_facturar_y_checkin_checkout(recepcion):
    c, app = recepcion
    with app.app_context():
        cafe = ProductoModel.query.filter_by(nombre="Café americano").first()
        reserva = ReservacionModel.query.filter_by(estado="reservada").first()
    # Vender en Caja (se pagan $200 para cubrir el precio aunque otro
    # test haya cambiado el precio del café en la BD compartida).
    items = json.dumps([{"tipo": "producto", "producto_id": cafe.id, "cantidad": 1}])
    r = c.post("/caja/cobrar", data={
        "cliente": "Cliente general", "metodo_pago": "efectivo",
        "monto_recibido": "200", "items": items, "documento": "recibo",
    }, follow_redirects=False)
    assert r.status_code == 302 and "/caja/recibo/" in r.headers["Location"]
    # Check-in y check-out.
    r = c.post(f"/reservaciones/{reserva.id}/check-in", follow_redirects=False)
    assert r.status_code == 302
    with app.app_context():
        assert ReservacionModel.query.get(reserva.id).estado == "check-in"
    r = c.post(f"/reservaciones/{reserva.id}/check-out", follow_redirects=False)
    assert r.status_code == 302
    with app.app_context():
        assert ReservacionModel.query.get(reserva.id).estado == "check-out"
    # Consultar disponibilidad y facturación.
    assert c.get("/calendario").status_code == 200
    assert c.get("/habitaciones").status_code == 200
    assert c.get("/facturacion").status_code == 200


def test_recepcion_si_puede_crear_reservacion(recepcion):
    c, app = recepcion
    with app.app_context():
        antes = ReservacionModel.query.count()
        habitacion = HabitacionModel.query.filter_by(numero="105").first()
        huesped = HuespedModel.query.first()
    r = c.post("/reservaciones", data={
        "habitacion_id": str(habitacion.id), "huesped_id": str(huesped.id),
        "fecha_entrada": "2026-12-20", "fecha_salida": "2026-12-22",
        "origen": "directo",
    }, follow_redirects=False)
    assert r.status_code == 302
    with app.app_context():
        assert ReservacionModel.query.count() == antes + 1


def test_recepcion_si_puede_editar_reservacion_pero_no_cancelar_ni_eliminar(recepcion):
    c, app = recepcion
    with app.app_context():
        reserva = ReservacionModel.query.filter_by(estado="reservada").first()
        rid = reserva.id
        habitacion_id = reserva.habitacion_id
        huesped_id = reserva.huesped_id
        entrada = reserva.fecha_entrada.isoformat()
        salida = reserva.fecha_salida.isoformat()
    # Ver el formulario de edición.
    r = c.get(f"/reservaciones/{rid}/editar")
    assert r.status_code == 200
    assert "Editar reservación".encode() in r.data
    # Guardar la edición (mismas fechas, sin solapamiento).
    r = c.post(f"/reservaciones/{rid}/editar", data={
        "habitacion_id": str(habitacion_id), "huesped_id": str(huesped_id),
        "fecha_entrada": entrada, "fecha_salida": salida,
    }, follow_redirects=False)
    assert r.status_code == 302
    with app.app_context():
        assert ReservacionModel.query.get(rid) is not None
        assert ReservacionModel.query.get(rid).estado == "reservada"
    # Pero cancelar y eliminar siguen siendo solo del administrador.
    r = c.post(f"/reservaciones/{rid}/cancelar", follow_redirects=False)
    assert r.status_code == 302
    with app.app_context():
        assert ReservacionModel.query.get(rid).estado == "reservada"
    r = c.post(f"/reservaciones/{rid}/eliminar", follow_redirects=False)
    assert r.status_code == 302
    with app.app_context():
        assert ReservacionModel.query.get(rid) is not None


def test_admin_si_puede_agregar_habitacion_y_ver_reportes(admin):
    c, app = admin
    with app.app_context():
        antes = HabitacionModel.query.count()
    r = c.post("/habitaciones", data={
        "numero": "106", "tipo": "Doble", "precio_noche": "1100",
        "capacidad": "2", "estado": "libre", "descripcion": "Creada por admin.",
    }, follow_redirects=False)
    assert r.status_code == 302
    with app.app_context():
        assert HabitacionModel.query.count() == antes + 1
        assert HabitacionModel.query.filter_by(numero="106").first() is not None
    assert c.get("/caja/productos").status_code == 200
    assert c.get("/reportes").status_code == 200
    assert c.get("/caja/corte").status_code == 200
    r = c.get("/habitaciones")
    assert "Agregar habitación" in _texto(r)
