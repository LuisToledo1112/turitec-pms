"""Tests de edición (habitaciones, huéspedes, reservaciones) y corte del día."""
import json
import os
import tempfile
from datetime import date

import pytest

os.environ["DATABASE_URL"] = "sqlite:///" + tempfile.mktemp(suffix=".db")

from src.turitec import create_app  # noqa: E402
from src.turitec.infrastructure.db import CierreModel, HabitacionModel, HuespedModel, ReservacionModel, db  # noqa: E402


@pytest.fixture
def cliente():
    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as c:
        c.post("/login", data={"email": "admin@turitec.mx", "password": "admin123"})
        yield c, app


def test_editar_habitacion_cambia_precio(cliente):
    c, app = cliente
    with app.app_context():
        hab = HabitacionModel.query.filter_by(numero="105").first()
    r = c.post(f"/habitaciones/{hab.id}/editar", data={
        "numero": "105", "tipo": "Sencilla", "precio_noche": "750",
        "capacidad": "2", "estado": "libre", "descripcion": "Actualizada.",
    }, follow_redirects=True)
    assert r.status_code == 200
    with app.app_context():
        hab = HabitacionModel.query.filter_by(numero="105").first()
        assert float(hab.precio_noche) == 750.0
        assert hab.descripcion == "Actualizada."


def test_editar_huesped_cambia_telefono(cliente):
    c, app = cliente
    with app.app_context():
        hues = HuespedModel.query.filter_by(nombre="Ana María López").first()
    r = c.post(f"/huespedes/{hues.id}/editar", data={
        "nombre": "Ana María López", "email": "ana.lopez@correo.mx",
        "telefono": "961 999 9999", "rfc": "LOMA850101ABC", "documento": "INE",
    }, follow_redirects=True)
    assert r.status_code == 200
    with app.app_context():
        hues = HuespedModel.query.filter_by(nombre="Ana María López").first()
        assert hues.telefono == "961 999 9999"


def test_editar_reservacion_cambia_fechas_y_recalcula(cliente):
    c, app = cliente
    with app.app_context():
        res = ReservacionModel.query.filter_by(habitacion_id=6).first()  # 201, 6-9 nov
        assert res is not None
    r = c.post(f"/reservaciones/{res.id}/editar", data={
        "habitacion_id": "6", "huesped_id": str(res.huesped_id),
        "fecha_entrada": "2026-11-20", "fecha_salida": "2026-11-22",
    }, follow_redirects=True)
    assert r.status_code == 200
    with app.app_context():
        res = ReservacionModel.query.get(res.id)
        assert res.fecha_entrada == date(2026, 11, 20)
        assert float(res.total) == 1900.0  # 2 noches x 950


def test_editar_reservacion_solapada_se_rechaza(cliente):
    c, app = cliente
    with app.app_context():
        res = ReservacionModel.query.filter_by(habitacion_id=3).first()  # 103, 10-13 nov
    r = c.post(f"/reservaciones/{res.id}/editar", data={
        "habitacion_id": "2", "huesped_id": str(res.huesped_id),  # 102, ocupada 5-8 nov
        "fecha_entrada": "2026-11-06", "fecha_salida": "2026-11-07",
    }, follow_redirects=True)
    assert "ya está reservada".encode() in r.data


def test_eliminar_reservacion_sin_factura(cliente):
    c, app = cliente
    with app.app_context():
        res = ReservacionModel.query.filter_by(habitacion_id=9).first()  # 204, 25-28 nov
    r = c.post(f"/reservaciones/{res.id}/eliminar", follow_redirects=True)
    assert r.status_code == 200
    with app.app_context():
        assert ReservacionModel.query.get(res.id) is None


def test_eliminar_reservacion_con_check_out_bloqueada(cliente):
    c, app = cliente
    with app.app_context():
        res = ReservacionModel.query.filter_by(habitacion_id=1).first()  # 101, check-out + factura
    r = c.post(f"/reservaciones/{res.id}/eliminar", follow_redirects=True)
    assert "No se puede eliminar".encode() in r.data
    with app.app_context():
        assert ReservacionModel.query.get(res.id) is not None


def test_corte_del_dia_y_cierre(cliente):
    c, app = cliente
    hoy = date.today().isoformat()
    # Una venta hoy: 1 café americano ($35, id 6 en el seed) en efectivo con $50
    items = json.dumps([{"tipo": "producto", "producto_id": 6, "cantidad": 1}])
    r = c.post("/caja/cobrar", data={
        "cliente": "Cliente general", "metodo_pago": "efectivo",
        "monto_recibido": "50", "items": items,
    }, follow_redirects=True)
    assert r.status_code == 200
    r = c.get(f"/caja/corte?fecha={hoy}")
    assert r.status_code == 200
    assert "Corte del día".encode() in r.data
    r = c.post("/caja/corte/cerrar", data={"fecha": hoy}, follow_redirects=True)
    assert "cerrado".encode() in r.data
    with app.app_context():
        cierre = CierreModel.query.filter_by(fecha=date.today()).first()
        assert cierre is not None
        assert float(cierre.total_ventas) >= 40.60
        assert float(cierre.efectivo_neto) >= 40.60
    # Un segundo cierre el mismo día se rechaza
    r = c.post("/caja/corte/cerrar", data={"fecha": hoy}, follow_redirects=True)
    assert "ya tiene un cierre".encode() in r.data


def test_reporte_ventas_carga(cliente):
    c, app = cliente
    r = c.get("/reportes/ventas?anio=2026&mes=11")
    assert r.status_code == 200
    assert "Reporte de ventas".encode() in r.data
    assert "Facturas del mes".encode() in r.data
