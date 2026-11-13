"""Tests de ventas de caja con dos opciones: recibo sencillo o factura."""
import json
import os
import sqlite3
import tempfile
from datetime import date

import pytest

os.environ["DATABASE_URL"] = "sqlite:///" + tempfile.mktemp(suffix=".db")

from src.turitec import create_app  # noqa: E402
from src.turitec.infrastructure.db import FacturaModel, VentaModel, db  # noqa: E402


@pytest.fixture
def cliente():
    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as c:
        c.post("/login", data={"email": "admin@turitec.mx", "password": "admin123"})
        yield c, app


def _cobrar(c, documento="recibo"):
    items = json.dumps([{"tipo": "producto", "producto_id": 6, "cantidad": 2}])
    return c.post("/caja/cobrar", data={
        "cliente": "Cliente general", "metodo_pago": "efectivo",
        "monto_recibido": "100", "items": items, "documento": documento,
    }, follow_redirects=False)


def test_cobrar_con_factura_pide_datos_y_genera_factura(cliente):
    c, app = cliente
    r = _cobrar(c, documento="factura")
    assert r.status_code == 302
    # Al cobrar con Factura primero pide los datos de facturación.
    assert "/factura" in r.headers["Location"]
    r = c.get(r.headers["Location"])
    assert r.status_code == 200
    assert "RFC".encode() in r.data
    assert "Cliente general".encode() in r.data
    with app.app_context():
        venta = VentaModel.query.order_by(VentaModel.id.desc()).first()
        vid = venta.id
    r = c.post(f"/caja/recibo/{vid}/factura", data={
        "cliente": "Ana María López", "rfc": "loma850101abc",
        "correo": "ana.lopez@correo.mx",
    }, follow_redirects=False)
    assert r.status_code == 302
    assert "/facturacion/" in r.headers["Location"]
    with app.app_context():
        factura = FacturaModel.query.filter_by(venta_id=vid).first()
        assert factura is not None
        assert factura.folio == f"TUR-V{vid:05d}"
        assert float(factura.total) == float(venta.total)
        assert float(factura.subtotal) == float(venta.subtotal)
        assert factura.cliente == "Ana María López"
        assert factura.rfc == "LOMA850101ABC"  # se guarda en mayúsculas
        assert factura.correo == "ana.lopez@correo.mx"
        assert factura.reservacion_id is None
        folio = factura.folio
    r = c.get(r.headers["Location"])
    assert r.status_code == 200
    assert folio.encode() in r.data
    assert "ana.lopez@correo.mx".encode() in r.data
    assert f"Venta {venta.folio}".encode() in r.data


def test_factura_sin_rfc_no_se_genera(cliente):
    c, app = cliente
    _cobrar(c, documento="recibo")
    with app.app_context():
        venta = VentaModel.query.order_by(VentaModel.id.desc()).first()
        vid = venta.id
    # Sin RFC no se genera: se queda en el formulario con el aviso.
    r = c.post(f"/caja/recibo/{vid}/factura", data={
        "cliente": "Ana María López", "rfc": "", "correo": "",
    }, follow_redirects=True)
    assert r.status_code == 200
    assert "RFC".encode() in r.data
    with app.app_context():
        assert FacturaModel.query.filter_by(venta_id=vid).first() is None
    # Con RFC sí se genera.
    r = c.post(f"/caja/recibo/{vid}/factura", data={
        "cliente": "Ana María López", "rfc": "LOMA850101ABC", "correo": "",
    }, follow_redirects=False)
    assert "/facturacion/" in r.headers["Location"]


def test_factura_reservacion_pide_datos_y_los_guarda(cliente):
    from src.turitec.infrastructure.db import ReservacionModel
    c, app = cliente
    with app.app_context():
        reserva = ReservacionModel.query.filter_by(estado="reservada").first()
        reserva.estado = "check-in"
        from src.turitec.infrastructure.db import db as _db
        _db.session.commit()
        rid = reserva.id
    # El formulario viene prellenado con los datos del huésped.
    r = c.get(f"/facturacion/nueva/{rid}")
    assert r.status_code == 200
    assert "RFC".encode() in r.data
    r = c.post(f"/facturacion/nueva/{rid}", data={
        "cliente": "Ana María López", "rfc": "LOMA850101ABC",
        "correo": "ana.lopez@correo.mx",
    }, follow_redirects=False)
    assert r.status_code == 302
    assert "/facturacion/" in r.headers["Location"]
    with app.app_context():
        factura = FacturaModel.query.filter_by(reservacion_id=rid).first()
        assert factura is not None
        assert factura.cliente == "Ana María López"
        assert factura.rfc == "LOMA850101ABC"
        assert factura.correo == "ana.lopez@correo.mx"


def test_cobrar_con_recibo_y_luego_facturar_desde_el_recibo(cliente):
    c, app = cliente
    r = _cobrar(c, documento="recibo")
    assert "/caja/recibo/" in r.headers["Location"]
    with app.app_context():
        venta = VentaModel.query.order_by(VentaModel.id.desc()).first()
        vid = venta.id
    r = c.get(f"/caja/recibo/{vid}")
    assert r.status_code == 200
    assert "Generar factura".encode() in r.data
    r = c.get(f"/caja/recibo/{vid}/factura")
    assert r.status_code == 200
    assert "RFC".encode() in r.data
    datos = {"cliente": "Cliente general", "rfc": "XAXX010101000", "correo": ""}
    r = c.post(f"/caja/recibo/{vid}/factura", data=datos, follow_redirects=False)
    assert r.status_code == 302
    assert "/facturacion/" in r.headers["Location"]
    # Una segunda vez no duplica la factura: devuelve la misma.
    r2 = c.post(f"/caja/recibo/{vid}/factura", data=datos, follow_redirects=False)
    assert r2.headers["Location"] == r.headers["Location"]
    with app.app_context():
        assert FacturaModel.query.filter_by(venta_id=vid).count() == 1
        folio = FacturaModel.query.filter_by(venta_id=vid).first().folio
    # Y el recibo ya muestra el enlace a la factura.
    r = c.get(f"/caja/recibo/{vid}")
    assert folio.encode() in r.data


def test_factura_de_venta_aparece_en_facturacion(cliente):
    c, app = cliente
    r = _cobrar(c, documento="factura")
    with app.app_context():
        venta = VentaModel.query.order_by(VentaModel.id.desc()).first()
        vid = venta.id
    c.post(f"/caja/recibo/{vid}/factura", data={
        "cliente": "Cliente general", "rfc": "XAXX010101000", "correo": "",
    }, follow_redirects=False)
    with app.app_context():
        folio = FacturaModel.query.filter_by(venta_id=vid).first().folio
    r = c.get("/facturacion")
    assert r.status_code == 200
    assert folio.encode() in r.data
    assert "Cliente general".encode() in r.data


def test_migracion_facturas_viejas_sin_venta_id():
    """La BD vieja (facturas sin venta_id/cliente, huéspedes sin
    telegram_user_id) se migra sola al arrancar, sin perder datos."""
    dbpath = tempfile.mktemp(suffix=".db")
    con = sqlite3.connect(dbpath)
    con.execute(
        "CREATE TABLE huespedes (id INTEGER PRIMARY KEY, nombre VARCHAR(150) NOT NULL,"
        " email VARCHAR(120), telefono VARCHAR(30), rfc VARCHAR(13), documento VARCHAR(50))"
    )
    con.execute(
        "CREATE TABLE facturas (id INTEGER PRIMARY KEY, folio VARCHAR(20) UNIQUE NOT NULL,"
        " reservacion_id INTEGER NOT NULL, huesped_id INTEGER NOT NULL,"
        " fecha_emision DATE, subtotal NUMERIC(10,2) NOT NULL, iva NUMERIC(10,2) NOT NULL,"
        " total NUMERIC(10,2) NOT NULL, rfc VARCHAR(13), estado VARCHAR(30))"
    )
    con.execute(
        "INSERT INTO facturas (folio, reservacion_id, huesped_id, fecha_emision,"
        " subtotal, iva, total, rfc, estado) VALUES"
        " ('TUR-00001', 1, 1, '2026-11-06', 2310, 369.60, 2679.60, 'LOMA850101ABC', 'emitida')"
    )
    con.commit()
    con.close()
    anterior = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = "sqlite:///" + dbpath
    try:
        app = create_app()
        with app.app_context():
            cols_fact = {c["name"] for c in __import__("sqlalchemy").inspect(db.engine).get_columns("facturas")}
            assert "venta_id" in cols_fact and "cliente" in cols_fact
            assert "correo" in cols_fact
            cols_hues = {c["name"] for c in __import__("sqlalchemy").inspect(db.engine).get_columns("huespedes")}
            assert "telegram_user_id" in cols_hues
            vieja = FacturaModel.query.filter_by(folio="TUR-00001").first()
            assert vieja is not None and float(vieja.total) == 2679.60
            assert vieja.venta_id is None
    finally:
        # Restaurar la BD de las demás pruebas (esta prueba cambia la
        # variable de entorno para simular la BD vieja).
        if anterior is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = anterior
