"""Tests del CRUD de productos de caja (agregar, editar, eliminar)."""
import json
import os
import tempfile

import pytest

os.environ["DATABASE_URL"] = "sqlite:///" + tempfile.mktemp(suffix=".db")

from src.turitec import create_app  # noqa: E402
from src.turitec.infrastructure.db import DetalleVentaModel, ProductoModel, db  # noqa: E402


@pytest.fixture
def cliente():
    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as c:
        c.post("/login", data={"email": "admin@turitec.mx", "password": "admin123"})
        yield c, app


def _producto_por_nombre(app, nombre):
    with app.app_context():
        return ProductoModel.query.filter_by(nombre=nombre).first()


def test_agregar_producto(cliente):
    c, app = cliente
    r = c.post("/caja/productos", data={
        "nombre": "Tacos de cochito", "categoria": "alimentos",
        "precio": "85", "descripcion": "Tacos de cochito horneado.",
    }, follow_redirects=True)
    assert r.status_code == 200
    assert "Tacos de cochito".encode() in r.data
    p = _producto_por_nombre(app, "Tacos de cochito")
    assert p is not None and float(p.precio) == 85.0 and p.activo is True
    # Y ya aparece para cobrar en Caja
    r = c.get("/caja")
    assert "Tacos de cochito".encode() in r.data


def test_editar_producto_cambia_precio(cliente):
    c, app = cliente
    p = _producto_por_nombre(app, "Café americano")
    r = c.post(f"/caja/productos/{p.id}/editar", data={
        "nombre": "Café americano", "categoria": "alimentos",
        "precio": "45", "descripcion": "Café de altura de Chiapas.", "activo": "on",
    }, follow_redirects=True)
    assert r.status_code == 200
    p = _producto_por_nombre(app, "Café americano")
    assert float(p.precio) == 45.0


def test_eliminar_producto_sin_ventas_lo_borra(cliente):
    c, app = cliente
    c.post("/caja/productos", data={
        "nombre": "Producto temporal", "categoria": "alimentos", "precio": "10",
    }, follow_redirects=True)
    p = _producto_por_nombre(app, "Producto temporal")
    r = c.post(f"/caja/productos/{p.id}/eliminar", follow_redirects=True)
    assert r.status_code == 200
    assert _producto_por_nombre(app, "Producto temporal") is None


def test_eliminar_producto_con_ventas_solo_lo_desactiva(cliente):
    c, app = cliente
    p = _producto_por_nombre(app, "Desayuno buffet")
    # Venderlo una vez
    items = json.dumps([{"tipo": "producto", "producto_id": p.id, "cantidad": 1}])
    c.post("/caja/cobrar", data={
        "cliente": "Cliente general", "metodo_pago": "tarjeta", "items": items,
    }, follow_redirects=True)
    with app.app_context():
        assert DetalleVentaModel.query.filter_by(producto_id=p.id).first() is not None
    r = c.post(f"/caja/productos/{p.id}/eliminar", follow_redirects=True)
    assert r.status_code == 200
    assert "se desactiv".encode() in r.data
    p = _producto_por_nombre(app, "Desayuno buffet")
    assert p is not None and p.activo is False  # se conserva, ya no está en venta
    r = c.get("/caja")
    assert "Desayuno buffet".encode() not in r.data
