"""Adaptadores de base de datos (infraestructura).

Modelos SQLAlchemy + repositorios que implementan los puertos del dominio.
También contiene los datos de ejemplo (seed) del hotel demo.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash

from ..domain.entities import Consumo as ConsumoEnt
from ..domain.entities import Factura as FacturaEnt
from ..domain.entities import Habitacion as HabitacionEnt
from ..domain.entities import Huesped as HuespedEnt
from ..domain.entities import Reservacion as ReservacionEnt
from ..domain.entities import Usuario as UsuarioEnt
from ..domain.ports import (
    ConsumoRepository,
    FacturaRepository,
    HabitacionRepository,
    HuespedRepository,
    ReservacionRepository,
    UsuarioRepository,
)

db = SQLAlchemy()


# ---------------- Modelos SQLAlchemy ----------------
class UsuarioModel(db.Model):
    __tablename__ = "usuarios"
    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    rol = db.Column(db.String(30), default="recepcion")
    activo = db.Column(db.Boolean, default=True)


class HabitacionModel(db.Model):
    __tablename__ = "habitaciones"
    id = db.Column(db.Integer, primary_key=True)
    numero = db.Column(db.String(10), unique=True, nullable=False)
    tipo = db.Column(db.String(30), nullable=False)
    precio_noche = db.Column(db.Numeric(10, 2), nullable=False)
    capacidad = db.Column(db.Integer, default=2)
    estado = db.Column(db.String(30), default="libre")
    descripcion = db.Column(db.Text, default="")


class HuespedModel(db.Model):
    __tablename__ = "huespedes"
    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(150), nullable=False)
    email = db.Column(db.String(120))
    telefono = db.Column(db.String(30))
    rfc = db.Column(db.String(13))
    documento = db.Column(db.String(50))


class ReservacionModel(db.Model):
    __tablename__ = "reservaciones"
    id = db.Column(db.Integer, primary_key=True)
    habitacion_id = db.Column(db.Integer, db.ForeignKey("habitaciones.id"), nullable=False)
    huesped_id = db.Column(db.Integer, db.ForeignKey("huespedes.id"), nullable=False)
    fecha_entrada = db.Column(db.Date, nullable=False)
    fecha_salida = db.Column(db.Date, nullable=False)
    estado = db.Column(db.String(30), default="reservada")
    total = db.Column(db.Numeric(10, 2), default=0)
    origen = db.Column(db.String(30), default="directo")
    creada_en = db.Column(db.DateTime, default=db.func.now())


class ConsumoModel(db.Model):
    __tablename__ = "consumos"
    id = db.Column(db.Integer, primary_key=True)
    reservacion_id = db.Column(db.Integer, db.ForeignKey("reservaciones.id"), nullable=False)
    concepto = db.Column(db.String(150), nullable=False)
    monto = db.Column(db.Numeric(10, 2), nullable=False)
    fecha = db.Column(db.Date, default=date.today)


class FacturaModel(db.Model):
    __tablename__ = "facturas"
    id = db.Column(db.Integer, primary_key=True)
    folio = db.Column(db.String(20), unique=True, nullable=False)
    reservacion_id = db.Column(db.Integer, db.ForeignKey("reservaciones.id"), nullable=False)
    huesped_id = db.Column(db.Integer, db.ForeignKey("huespedes.id"), nullable=False)
    fecha_emision = db.Column(db.Date, default=date.today)
    subtotal = db.Column(db.Numeric(10, 2), nullable=False)
    iva = db.Column(db.Numeric(10, 2), nullable=False)
    total = db.Column(db.Numeric(10, 2), nullable=False)
    rfc = db.Column(db.String(13))
    estado = db.Column(db.String(30), default="emitida")


class SincronizacionModel(db.Model):
    __tablename__ = "sincronizaciones"
    id = db.Column(db.Integer, primary_key=True)
    canal = db.Column(db.String(50), nullable=False)
    habitacion_id = db.Column(db.Integer, db.ForeignKey("habitaciones.id"))
    fecha_inicio = db.Column(db.Date)
    fecha_fin = db.Column(db.Date)
    disponible = db.Column(db.Boolean, default=True)
    resultado = db.Column(db.String(255))
    creada_en = db.Column(db.DateTime, default=db.func.now())


# ---------------- Conversores modelo <-> entidad ----------------
def _hab_ent(m: HabitacionModel) -> HabitacionEnt:
    return HabitacionEnt(m.id, m.numero, m.tipo, Decimal(str(m.precio_noche)), m.capacidad, m.estado, m.descripcion or "")


def _huesped_ent(m: HuespedModel) -> HuespedEnt:
    return HuespedEnt(m.id, m.nombre, m.email or "", m.telefono or "", m.rfc or "", m.documento or "")


def _res_ent(m: ReservacionModel) -> ReservacionEnt:
    return ReservacionEnt(m.id, m.habitacion_id, m.huesped_id, m.fecha_entrada, m.fecha_salida, m.estado, Decimal(str(m.total)), m.origen)


def _factura_ent(m: FacturaModel) -> FacturaEnt:
    return FacturaEnt(m.id, m.folio, m.reservacion_id, m.huesped_id, m.fecha_emision, Decimal(str(m.subtotal)), Decimal(str(m.iva)), Decimal(str(m.total)), m.rfc or "", m.estado)


# ---------------- Adaptadores (repositorios SQLAlchemy) ----------------
class SQLAlchemyHabitacionRepository(HabitacionRepository):
    def listar(self):
        return [_hab_ent(m) for m in HabitacionModel.query.order_by(HabitacionModel.numero).all()]

    def obtener(self, habitacion_id):
        m = HabitacionModel.query.get(habitacion_id)
        return _hab_ent(m) if m else None

    def guardar(self, h: HabitacionEnt):
        m = HabitacionModel.query.get(h.id) if h.id else None
        if m is None:
            m = HabitacionModel(numero=h.numero, tipo=h.tipo, precio_noche=h.precio_noche, capacidad=h.capacidad, estado=h.estado, descripcion=h.descripcion)
            db.session.add(m)
        else:
            m.numero, m.tipo, m.precio_noche, m.capacidad, m.estado, m.descripcion = h.numero, h.tipo, h.precio_noche, h.capacidad, h.estado, h.descripcion
        db.session.commit()
        return _hab_ent(m)

    def eliminar(self, habitacion_id):
        m = HabitacionModel.query.get(habitacion_id)
        if m:
            db.session.delete(m)
            db.session.commit()


class SQLAlchemyHuespedRepository(HuespedRepository):
    def listar(self):
        return [_huesped_ent(m) for m in HuespedModel.query.order_by(HuespedModel.nombre).all()]

    def obtener(self, huesped_id):
        m = HuespedModel.query.get(huesped_id)
        return _huesped_ent(m) if m else None

    def guardar(self, h: HuespedEnt):
        m = HuespedModel.query.get(h.id) if h.id else None
        if m is None:
            m = HuespedModel(nombre=h.nombre, email=h.email, telefono=h.telefono, rfc=h.rfc, documento=h.documento)
            db.session.add(m)
        else:
            m.nombre, m.email, m.telefono, m.rfc, m.documento = h.nombre, h.email, h.telefono, h.rfc, h.documento
        db.session.commit()
        return _huesped_ent(m)

    def eliminar(self, huesped_id):
        m = HuespedModel.query.get(huesped_id)
        if m:
            db.session.delete(m)
            db.session.commit()


class SQLAlchemyReservacionRepository(ReservacionRepository):
    def listar(self):
        return [_res_ent(m) for m in ReservacionModel.query.order_by(ReservacionModel.fecha_entrada).all()]

    def obtener(self, reservacion_id):
        m = ReservacionModel.query.get(reservacion_id)
        return _res_ent(m) if m else None

    def guardar(self, r: ReservacionEnt):
        m = ReservacionModel.query.get(r.id) if r.id else None
        if m is None:
            m = ReservacionModel(habitacion_id=r.habitacion_id, huesped_id=r.huesped_id, fecha_entrada=r.fecha_entrada, fecha_salida=r.fecha_salida, estado=r.estado, total=r.total, origen=r.origen)
            db.session.add(m)
        else:
            m.habitacion_id, m.huesped_id, m.fecha_entrada, m.fecha_salida, m.estado, m.total, m.origen = r.habitacion_id, r.huesped_id, r.fecha_entrada, r.fecha_salida, r.estado, r.total, r.origen
        db.session.commit()
        return _res_ent(m)

    def buscar_solapadas(self, habitacion_id, entrada, salida, excluir_id=None):
        q = ReservacionModel.query.filter(
            ReservacionModel.habitacion_id == habitacion_id,
            ReservacionModel.estado.in_(["reservada", "check-in"]),
            ReservacionModel.fecha_entrada < salida,
            ReservacionModel.fecha_salida > entrada,
        )
        if excluir_id:
            q = q.filter(ReservacionModel.id != excluir_id)
        return [_res_ent(m) for m in q.all()]

    def listar_por_habitacion(self, habitacion_id):
        return [_res_ent(m) for m in ReservacionModel.query.filter_by(habitacion_id=habitacion_id).order_by(ReservacionModel.fecha_entrada).all()]


class SQLAlchemyFacturaRepository(FacturaRepository):
    def listar(self):
        return [_factura_ent(m) for m in FacturaModel.query.order_by(FacturaModel.fecha_emision.desc()).all()]

    def obtener(self, factura_id):
        m = FacturaModel.query.get(factura_id)
        return _factura_ent(m) if m else None

    def guardar(self, f: FacturaEnt):
        m = FacturaModel.query.get(f.id) if f.id else None
        if m is None:
            m = FacturaModel(folio=f.folio, reservacion_id=f.reservacion_id, huesped_id=f.huesped_id, fecha_emision=f.fecha_emision, subtotal=f.subtotal, iva=f.iva, total=f.total, rfc=f.rfc, estado=f.estado)
            db.session.add(m)
        else:
            m.folio, m.reservacion_id, m.huesped_id, m.fecha_emision, m.subtotal, m.iva, m.total, m.rfc, m.estado = f.folio, f.reservacion_id, f.huesped_id, f.fecha_emision, f.subtotal, f.iva, f.total, f.rfc, f.estado
        db.session.commit()
        return _factura_ent(m)

    def obtener_por_reservacion(self, reservacion_id):
        m = FacturaModel.query.filter_by(reservacion_id=reservacion_id).first()
        return _factura_ent(m) if m else None


class SQLAlchemyConsumoRepository(ConsumoRepository):
    def listar_por_reservacion(self, reservacion_id):
        return [
            ConsumoEnt(m.id, m.reservacion_id, m.concepto, Decimal(str(m.monto)), m.fecha)
            for m in ConsumoModel.query.filter_by(reservacion_id=reservacion_id).all()
        ]

    def guardar(self, c: ConsumoEnt):
        m = ConsumoModel(reservacion_id=c.reservacion_id, concepto=c.concepto, monto=c.monto, fecha=c.fecha or date.today())
        db.session.add(m)
        db.session.commit()
        return ConsumoEnt(m.id, m.reservacion_id, m.concepto, Decimal(str(m.monto)), m.fecha)


class SQLAlchemyUsuarioRepository(UsuarioRepository):
    def obtener_por_email(self, email):
        m = UsuarioModel.query.filter_by(email=email.lower()).first()
        return UsuarioEnt(m.id, m.nombre, m.email, m.password_hash, m.rol, m.activo) if m else None

    def guardar(self, u: UsuarioEnt):
        m = UsuarioModel.query.get(u.id) if u.id else None
        if m is None:
            m = UsuarioModel(nombre=u.nombre, email=u.email, password_hash=u.password_hash, rol=u.rol, activo=u.activo)
            db.session.add(m)
        else:
            m.nombre, m.email, m.password_hash, m.rol, m.activo = u.nombre, u.email, u.password_hash, u.rol, u.activo
        db.session.commit()
        return UsuarioEnt(m.id, m.nombre, m.email, m.password_hash, m.rol, m.activo)


# ---------------- Datos de ejemplo (seed) ----------------
def seed_data():
    """Crea el hotel demo si la base de datos está vacía. Es idempotente."""
    if HabitacionModel.query.first():
        return False
    # Usuarios demo
    db.session.add_all([
        UsuarioModel(nombre="Víctor Manuel Calderón Vázquez", email="admin@turitec.mx", password_hash=generate_password_hash("admin123"), rol="administrador"),
        UsuarioModel(nombre="Recepción Turitec", email="recepcion@turitec.mx", password_hash=generate_password_hash("recepcion123"), rol="recepcion"),
    ])
    # 12 habitaciones: 5 sencillas, 4 dobles, 3 suites
    habitaciones = [
        ("101", "Sencilla", 650, 1, "Habitación sencilla con cama matrimonial y vista al jardín."),
        ("102", "Sencilla", 650, 1, "Habitación sencilla, ideal para viajeros solos."),
        ("103", "Sencilla", 650, 2, "Habitación sencilla con dos camas individuales."),
        ("104", "Sencilla", 700, 2, "Habitación sencilla amplia, cerca de la recepción."),
        ("105", "Sencilla", 700, 2, "Habitación sencilla con escritorio de trabajo."),
        ("201", "Doble", 950, 2, "Habitación doble con cama king size y balcón."),
        ("202", "Doble", 950, 3, "Habitación doble familiar con sofá cama."),
        ("203", "Doble", 1050, 2, "Habitación doble con vista a la ciudad."),
        ("204", "Doble", 1050, 4, "Habitación doble familiar, dos camas matrimoniales."),
        ("301", "Suite", 1500, 2, "Suite con sala, tina y vista panorámica."),
        ("302", "Suite", 1600, 3, "Suite familiar con cocineta."),
        ("303", "Suite", 1800, 4, "Suite presidencial con terraza privada."),
    ]
    for numero, tipo, precio, capacidad, desc in habitaciones:
        db.session.add(HabitacionModel(numero=numero, tipo=tipo, precio_noche=precio, capacidad=capacidad, descripcion=desc))
    db.session.flush()
    hab = {h.numero: h.id for h in HabitacionModel.query.all()}

    huespedes = [
        ("Ana María López", "ana.lopez@correo.mx", "961 123 4567", "LOMA850101ABC", "INE"),
        ("Carlos Hernández Ruiz", "carlos.hr@correo.mx", "961 234 5678", "HERC790515XYZ", "Pasaporte"),
        ("María Fernanda Torres", "mftorres@correo.mx", "961 345 6789", "TOFM920320DEF", "INE"),
        ("Jorge Luis Mendoza", "jorge.mendoza@correo.mx", "961 456 7890", "MEJO880710GHI", "INE"),
        ("Lucía Pérez García", "lucia.pg@correo.mx", "961 567 8901", "PEGL950425JKL", "Pasaporte"),
        ("Diego Ramírez Soto", "diego.rs@correo.mx", "961 678 9012", "RASD900812MNO", "INE"),
        ("Sofía Castillo Vega", "sofia.cv@correo.mx", "961 789 0123", "CAVS940630PQR", "INE"),
        ("Miguel Ángel Díaz", "miguel.diaz@correo.mx", "961 890 1234", "DIAM860215STU", "INE"),
    ]
    for nombre, email, tel, rfc, doc in huespedes:
        db.session.add(HuespedModel(nombre=nombre, email=email, telefono=tel, rfc=rfc, documento=doc))
    db.session.flush()
    hues = {h.nombre: h.id for h in HuespedModel.query.all()}

    def total_reserva(numero, entrada, salida):
        precio = next(p for n, _t, p, _c, _d in habitaciones if n == numero)
        return Decimal(str(precio)) * (salida - entrada).days

    # Reservaciones de ejemplo en noviembre de 2026 (hoy: 6 nov 2026)
    reservas = [
        # (habitación, huésped, entrada, salida, estado, origen)
        ("101", "Ana María López", date(2026, 11, 3), date(2026, 11, 6), "check-out", "directo"),
        ("102", "Carlos Hernández Ruiz", date(2026, 11, 5), date(2026, 11, 8), "check-in", "booking"),
        ("201", "María Fernanda Torres", date(2026, 11, 6), date(2026, 11, 9), "reservada", "directo"),
        ("202", "Jorge Luis Mendoza", date(2026, 11, 7), date(2026, 11, 10), "reservada", "airbnb"),
        ("301", "Lucía Pérez García", date(2026, 11, 4), date(2026, 11, 7), "check-in", "directo"),
        ("103", "Diego Ramírez Soto", date(2026, 11, 10), date(2026, 11, 13), "reservada", "booking"),
        ("203", "Sofía Castillo Vega", date(2026, 11, 12), date(2026, 11, 15), "reservada", "directo"),
        ("302", "Miguel Ángel Díaz", date(2026, 11, 20), date(2026, 11, 23), "reservada", "airbnb"),
        ("104", "Ana María López", date(2026, 11, 15), date(2026, 11, 17), "reservada", "directo"),
        ("204", "Carlos Hernández Ruiz", date(2026, 11, 25), date(2026, 11, 28), "reservada", "booking"),
    ]
    for numero, huesped, entrada, salida, estado, origen in reservas:
        db.session.add(ReservacionModel(
            habitacion_id=hab[numero], huesped_id=hues[huesped],
            fecha_entrada=entrada, fecha_salida=salida, estado=estado,
            total=total_reserva(numero, entrada, salida), origen=origen,
        ))
    db.session.flush()
    # Marcar ocupadas las habitaciones con check-in
    for numero in ("102", "301"):
        h = HabitacionModel.query.filter_by(numero=numero).first()
        h.estado = "ocupada"
    # Consumos de ejemplo para la reservación 1 (ya con check-out)
    res1 = ReservacionModel.query.filter_by(habitacion_id=hab["101"]).first()
    db.session.add_all([
        ConsumoModel(reservacion_id=res1.id, concepto="Desayuno buffet (2 personas)", monto=Decimal("240.00"), fecha=date(2026, 11, 4)),
        ConsumoModel(reservacion_id=res1.id, concepto="Servicio de lavandería", monto=Decimal("120.00"), fecha=date(2026, 11, 5)),
    ])
    db.session.flush()
    # Factura de ejemplo para la reservación 1: 3 noches x 650 + 360 consumos = 2310 subtotal, IVA 369.60, total 2679.60
    subtotal = Decimal("2310.00")
    iva = (subtotal * Decimal("0.16")).quantize(Decimal("0.01"))
    db.session.add(FacturaModel(
        folio="TUR-00001", reservacion_id=res1.id, huesped_id=hues["Ana María López"],
        fecha_emision=date(2026, 11, 6), subtotal=subtotal, iva=iva, total=subtotal + iva,
        rfc="LOMA850101ABC", estado="emitida",
    ))
    db.session.commit()
    return True
