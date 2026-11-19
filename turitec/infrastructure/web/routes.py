"""Adaptador web (Flask): vistas HTML + API REST JSON.

Las rutas llaman a los casos de uso, que a su vez usan los repositorios.
Ninguna regla de negocio vive aquí: solo HTTP, formularios y sesiones.
"""
from __future__ import annotations

import calendar as cal
import json
from datetime import date, datetime, timedelta
from decimal import Decimal
from functools import wraps

from flask import (
    Blueprint, flash, jsonify, redirect, render_template, request, session, url_for,
)
from werkzeug.security import check_password_hash

from ...application.use_cases import (
    CancelarReservacion, CorteDia, CrearReservacion, EditarReservacion, GenerarFactura,
    GenerarFacturaVenta, HacerCheckIn, HacerCheckOut, RegistrarVenta,
    ReporteOcupacionMensual, ReporteVentas, SincronizarDisponibilidad,
)
from ...domain.entities import Consumo as ConsumoEnt
from ...domain.entities import Factura as FacturaEnt
from ...domain.entities import Habitacion as HabitacionEnt
from ...domain.entities import Huesped as HuespedEnt
from ...domain.entities import Producto as ProductoEnt
from ..channels import canal_simulado
from ..telegram import (
    TelegramNotificador, formatear_cancelacion, formatear_check_in,
    formatear_check_out, formatear_factura, formatear_reservacion, formatear_venta,
)
from ..db import (
    CierreModel, ConsumoModel, DetalleVentaModel, FacturaModel, HabitacionModel,
    HuespedModel, ProductoModel, ReservacionModel, SQLAlchemyConsumoRepository,
    SQLAlchemyFacturaRepository, SQLAlchemyHabitacionRepository,
    SQLAlchemyHuespedRepository, SQLAlchemyProductoRepository,
    SQLAlchemyReservacionRepository, SQLAlchemyUsuarioRepository,
    SQLAlchemyVentaRepository, UsuarioModel, VentaModel, db,
)

bp = Blueprint("main", __name__)

HOY = date(2026, 11, 6)  # fecha de referencia del proyecto (nov 2026)


def _hoy() -> date:
    return date.today()


def repos():
    return {
        "hab": SQLAlchemyHabitacionRepository(),
        "hues": SQLAlchemyHuespedRepository(),
        "res": SQLAlchemyReservacionRepository(),
        "fac": SQLAlchemyFacturaRepository(),
        "con": SQLAlchemyConsumoRepository(),
        "usr": SQLAlchemyUsuarioRepository(),
        "prod": SQLAlchemyProductoRepository(),
        "venta": SQLAlchemyVentaRepository(),
    }


def _notificar(mensaje: str) -> None:
    """Envía un aviso por Telegram si está configurado.

    Nunca interrumpe la operación principal: si Telegram no está
    configurado o falla, el aviso simplemente no se envía.
    """
    try:
        TelegramNotificador().enviar(mensaje)
    except Exception:  # noqa: BLE001 - un aviso nunca debe tumbar la app
        pass


def _datos_reserva(rid: int):
    """Devuelve (reserva, habitación, huésped) como modelos, o Nones."""
    reserva = ReservacionModel.query.get(rid)
    if reserva is None:
        return None, None, None
    habitacion = HabitacionModel.query.get(reserva.habitacion_id)
    huesped = HuespedModel.query.get(reserva.huesped_id)
    return reserva, habitacion, huesped


def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if "usuario_id" not in session:
            return redirect(url_for("main.login"))
        return f(*args, **kwargs)
    return wrapper


def es_admin() -> bool:
    """El usuario en sesión tiene el rol administrador."""
    return session.get("usuario_rol") == "administrador"


def solo_admin(f):
    """Solo administradores: el personal de recepción no puede
    agregar, editar ni eliminar datos del sistema. Puede vender,
    facturar, hacer check-in/check-out y consultar disponibilidad."""
    @wraps(f)
    def wrapper(*args, **kwargs):
        if "usuario_id" not in session:
            return redirect(url_for("main.login"))
        if not es_admin():
            flash("Esta función es solo para administradores.", "error")
            return redirect(url_for("main.dashboard"))
        return f(*args, **kwargs)
    return wrapper


def _parse_fecha(valor: str) -> date:
    return datetime.strptime(valor, "%Y-%m-%d").date()


def _habitaciones_dict():
    return {h.id: h for h in HabitacionModel.query.all()}


def _huespedes_dict():
    return {h.id: h for h in HuespedModel.query.all()}


# ---------------- Autenticación ----------------
@bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        repo = SQLAlchemyUsuarioRepository()
        usuario = repo.obtener_por_email(email)
        if usuario and usuario.activo and check_password_hash(usuario.password_hash, password):
            session["usuario_id"] = usuario.id
            session["usuario_nombre"] = usuario.nombre
            session["usuario_rol"] = usuario.rol
            flash(f"Bienvenido, {usuario.nombre}.", "ok")
            return redirect(url_for("main.dashboard"))
        flash("Correo o contraseña incorrectos. Prueba con admin@turitec.mx / admin123.", "error")
    return render_template("login.html")


@bp.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("main.login"))


@bp.route("/")
def index():
    return redirect(url_for("main.dashboard") if "usuario_id" in session else url_for("main.login"))


# ---------------- Dashboard ----------------
@bp.route("/dashboard")
@login_required
def dashboard():
    hoy = _hoy()
    # Para la demo, si hoy está fuera de noviembre 2026, el rack usa la fecha real;
    # los datos de ejemplo son de noviembre 2026, así que el dashboard muestra
    # también el mes de ejemplo cuando no hay actividad hoy.
    reservas = ReservacionModel.query.order_by(ReservacionModel.fecha_entrada).all()
    habitaciones = HabitacionModel.query.all()
    ocupadas_hoy = sum(
        1 for r in reservas
        if r.estado in ("reservada", "check-in") and r.fecha_entrada <= hoy < r.fecha_salida
    )
    activas = [r for r in reservas if r.estado in ("reservada", "check-in")]
    facturas_mes = FacturaModel.query.filter(
        db.extract("year", FacturaModel.fecha_emision) == hoy.year,
        db.extract("month", FacturaModel.fecha_emision) == hoy.month,
    ).all()
    ventas_mes = VentaModel.query.filter(
        db.extract("year", VentaModel.fecha) == hoy.year,
        db.extract("month", VentaModel.fecha) == hoy.month,
    ).all()
    ingresos_mes = sum((float(f.total) for f in facturas_mes), 0.0) + sum((float(v.total) for v in ventas_mes), 0.0)
    # Si no hay ingresos este mes real, mostrar los de noviembre 2026 (datos demo)
    if not facturas_mes and not ventas_mes:
        facturas_mes = FacturaModel.query.filter(
            db.extract("year", FacturaModel.fecha_emision) == 2026,
            db.extract("month", FacturaModel.fecha_emision) == 11,
        ).all()
        ventas_mes = VentaModel.query.filter(
            db.extract("year", VentaModel.fecha) == 2026,
            db.extract("month", VentaModel.fecha) == 11,
        ).all()
        ingresos_mes = sum((float(f.total) for f in facturas_mes), 0.0) + sum((float(v.total) for v in ventas_mes), 0.0)
    reporte = ReporteOcupacionMensual(repos()["res"], repos()["hab"], repos()["fac"]).execute(2026, 11)
    proximas = [r for r in activas if r.fecha_entrada >= hoy][:5] or [r for r in activas if r.fecha_entrada >= date(2026, 11, 6)][:5]
    return render_template(
        "dashboard.html",
        habitaciones=habitaciones,
        ocupadas_hoy=ocupadas_hoy,
        activas=len(activas),
        ingresos_mes=ingresos_mes,
        tasa=reporte["tasa_ocupacion"],
        proximas=proximas,
        hab_dict=_habitaciones_dict(),
        hues_dict=_huespedes_dict(),
        hoy=hoy,
    )


# ---------------- Habitaciones ----------------
@bp.route("/habitaciones", methods=["GET", "POST"])
@login_required
def habitaciones():
    r = repos()
    if request.method == "POST":
        if not es_admin():
            flash("Agregar habitaciones es solo para administradores.", "error")
            return redirect(url_for("main.habitaciones"))
        try:
            h = HabitacionEnt(
                numero=request.form["numero"].strip(),
                tipo=request.form["tipo"],
                precio_noche=Decimal(request.form["precio_noche"]),
                capacidad=int(request.form.get("capacidad", 2)),
                estado=request.form.get("estado", "libre"),
                descripcion=request.form.get("descripcion", ""),
            )
            r["hab"].guardar(h)
            flash(f"Habitación {h.numero} guardada.", "ok")
        except Exception as exc:  # noqa: BLE001
            flash(f"No se pudo guardar la habitación: {exc}", "error")
        return redirect(url_for("main.habitaciones"))
    return render_template("habitaciones.html", habitaciones=HabitacionModel.query.order_by(HabitacionModel.numero).all())


@bp.route("/habitaciones/<int:hid>/eliminar", methods=["POST"])
@solo_admin
def eliminar_habitacion(hid):
    try:
        repos()["hab"].eliminar(hid)
        flash("Habitación eliminada.", "ok")
    except Exception as exc:  # noqa: BLE001
        flash(f"No se pudo eliminar: {exc}", "error")
    return redirect(url_for("main.habitaciones"))


@bp.route("/habitaciones/<int:hid>/editar", methods=["GET", "POST"])
@solo_admin
def editar_habitacion(hid):
    r = repos()
    habitacion = r["hab"].obtener(hid)
    if habitacion is None:
        flash("La habitación no existe.", "error")
        return redirect(url_for("main.habitaciones"))
    if request.method == "POST":
        try:
            habitacion.numero = request.form["numero"].strip()
            habitacion.tipo = request.form["tipo"]
            habitacion.precio_noche = Decimal(request.form["precio_noche"])
            habitacion.capacidad = int(request.form.get("capacidad", 2))
            habitacion.estado = request.form.get("estado", "libre")
            habitacion.descripcion = request.form.get("descripcion", "")
            if not habitacion.numero:
                raise ValueError("La habitación necesita un número.")
            if habitacion.precio_noche < 0:
                raise ValueError("El precio no puede ser negativo.")
            r["hab"].guardar(habitacion)
            flash(f"Habitación {habitacion.numero} actualizada.", "ok")
            return redirect(url_for("main.habitaciones"))
        except ValueError as exc:
            flash(str(exc), "error")
        except Exception as exc:  # noqa: BLE001
            flash(f"No se pudo guardar la habitación: {exc}", "error")
    return render_template(
        "habitaciones.html",
        habitaciones=HabitacionModel.query.order_by(HabitacionModel.numero).all(),
        habitacion_editar=habitacion,
    )


# ---------------- Huéspedes ----------------
@bp.route("/huespedes", methods=["GET", "POST"])
@login_required
def huespedes():
    r = repos()
    if request.method == "POST":
        if not es_admin():
            flash("Agregar huéspedes es solo para administradores.", "error")
            return redirect(url_for("main.huespedes"))
        try:
            h = HuespedEnt(
                nombre=request.form["nombre"].strip(),
                email=request.form.get("email", ""),
                telefono=request.form.get("telefono", ""),
                rfc=request.form.get("rfc", "").upper(),
                documento=request.form.get("documento", ""),
            )
            r["hues"].guardar(h)
            flash(f"Huésped {h.nombre} guardado.", "ok")
        except Exception as exc:  # noqa: BLE001
            flash(f"No se pudo guardar el huésped: {exc}", "error")
        return redirect(url_for("main.huespedes"))
    return render_template("huespedes.html", huespedes=HuespedModel.query.order_by(HuespedModel.nombre).all())


@bp.route("/huespedes/<int:hid>/eliminar", methods=["POST"])
@solo_admin
def eliminar_huesped(hid):
    try:
        repos()["hues"].eliminar(hid)
        flash("Huésped eliminado.", "ok")
    except Exception as exc:  # noqa: BLE001
        flash(f"No se pudo eliminar: {exc}", "error")
    return redirect(url_for("main.huespedes"))


@bp.route("/huespedes/<int:hid>/editar", methods=["GET", "POST"])
@solo_admin
def editar_huesped(hid):
    r = repos()
    huesped = r["hues"].obtener(hid)
    if huesped is None:
        flash("El huésped no existe.", "error")
        return redirect(url_for("main.huespedes"))
    if request.method == "POST":
        try:
            huesped.nombre = request.form["nombre"].strip()
            huesped.email = request.form.get("email", "")
            huesped.telefono = request.form.get("telefono", "")
            huesped.rfc = request.form.get("rfc", "").upper()
            huesped.documento = request.form.get("documento", "")
            if not huesped.nombre:
                raise ValueError("El huésped necesita un nombre.")
            r["hues"].guardar(huesped)
            flash(f"Huésped {huesped.nombre} actualizado.", "ok")
            return redirect(url_for("main.huespedes"))
        except ValueError as exc:
            flash(str(exc), "error")
        except Exception as exc:  # noqa: BLE001
            flash(f"No se pudo guardar el huésped: {exc}", "error")
    return render_template(
        "huespedes.html",
        huespedes=HuespedModel.query.order_by(HuespedModel.nombre).all(),
        huesped_editar=huesped,
    )


# ---------------- Reservaciones ----------------
@bp.route("/reservaciones", methods=["GET", "POST"])
@login_required
def reservaciones():
    r = repos()
    if request.method == "POST":
        try:
            reserva = CrearReservacion(r["res"], r["hab"]).execute(
                habitacion_id=int(request.form["habitacion_id"]),
                huesped_id=int(request.form["huesped_id"]),
                fecha_entrada=_parse_fecha(request.form["fecha_entrada"]),
                fecha_salida=_parse_fecha(request.form["fecha_salida"]),
                origen=request.form.get("origen", "directo"),
            )
            flash(f"Reservación #{reserva.id} creada: {reserva.noches()} noches, total ${float(reserva.total):,.2f} MXN.", "ok")
            _res, _hab, _hues = _datos_reserva(reserva.id)
            if _res and _hab and _hues:
                _notificar(formatear_reservacion(
                    reserva.id, _hab.numero, _hues.nombre,
                    _res.fecha_entrada, _res.fecha_salida,
                    reserva.noches(), float(reserva.total),
                ))
        except ValueError as exc:
            flash(str(exc), "error")
        except Exception as exc:  # noqa: BLE001
            flash(f"No se pudo crear la reservación: {exc}", "error")
        return redirect(url_for("main.reservaciones"))
    reservas = ReservacionModel.query.order_by(ReservacionModel.fecha_entrada).all()
    return render_template(
        "reservaciones.html",
        reservas=reservas,
        habitaciones=HabitacionModel.query.order_by(HabitacionModel.numero).all(),
        huespedes=HuespedModel.query.order_by(HuespedModel.nombre).all(),
        hab_dict=_habitaciones_dict(),
        hues_dict=_huespedes_dict(),
    )


@bp.route("/reservaciones/<int:rid>/check-in", methods=["POST"])
@login_required
def check_in(rid):
    r = repos()
    try:
        HacerCheckIn(r["res"], r["hab"]).execute(rid)
        flash(f"Check-in realizado para la reservación #{rid}.", "ok")
        _res, _hab, _hues = _datos_reserva(rid)
        if _res and _hab and _hues:
            _notificar(formatear_check_in(rid, _hab.numero, _hues.nombre))
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("main.reservaciones"))


@bp.route("/reservaciones/<int:rid>/check-out", methods=["POST"])
@login_required
def check_out(rid):
    r = repos()
    try:
        HacerCheckOut(r["res"], r["hab"]).execute(rid)
        flash(f"Check-out realizado para la reservación #{rid}. Ya puedes generar su factura.", "ok")
        _res, _hab, _hues = _datos_reserva(rid)
        if _res and _hab and _hues:
            _notificar(formatear_check_out(rid, _hab.numero, _hues.nombre))
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("main.reservaciones"))


@bp.route("/reservaciones/<int:rid>/cancelar", methods=["POST"])
@solo_admin
def cancelar_reservacion(rid):
    try:
        _res, _hab, _hues = _datos_reserva(rid)
        CancelarReservacion(repos()["res"]).execute(rid)
        flash(f"Reservación #{rid} cancelada.", "ok")
        if _hab:
            _notificar(formatear_cancelacion(rid, _hab.numero))
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("main.reservaciones"))


@bp.route("/reservaciones/<int:rid>/editar", methods=["GET", "POST"])
@login_required
def editar_reservacion(rid):
    r = repos()
    reserva = r["res"].obtener(rid)
    if reserva is None:
        flash("La reservación no existe.", "error")
        return redirect(url_for("main.reservaciones"))
    if request.method == "POST":
        try:
            actualizada = EditarReservacion(r["res"], r["hab"]).execute(
                reservacion_id=rid,
                habitacion_id=int(request.form["habitacion_id"]),
                huesped_id=int(request.form["huesped_id"]),
                fecha_entrada=_parse_fecha(request.form["fecha_entrada"]),
                fecha_salida=_parse_fecha(request.form["fecha_salida"]),
            )
            flash(f"Reservación #{rid} actualizada: {actualizada.noches()} noches, total ${float(actualizada.total):,.2f} MXN.", "ok")
            return redirect(url_for("main.reservaciones"))
        except ValueError as exc:
            flash(str(exc), "error")
        except Exception as exc:  # noqa: BLE001
            flash(f"No se pudo actualizar la reservación: {exc}", "error")
    reservas = ReservacionModel.query.order_by(ReservacionModel.fecha_entrada).all()
    return render_template(
        "reservaciones.html",
        reservas=reservas,
        habitaciones=HabitacionModel.query.order_by(HabitacionModel.numero).all(),
        huespedes=HuespedModel.query.order_by(HuespedModel.nombre).all(),
        hab_dict=_habitaciones_dict(),
        hues_dict=_huespedes_dict(),
        reserva_editar=reserva,
    )


@bp.route("/reservaciones/<int:rid>/eliminar", methods=["POST"])
@solo_admin
def eliminar_reservacion(rid):
    r = repos()
    reserva = r["res"].obtener(rid)
    if reserva is None:
        flash("La reservación no existe.", "error")
    elif reserva.estado not in ("reservada", "cancelada"):
        flash(f"No se puede eliminar una reservación en estado '{reserva.estado}'. Cancélala primero si ya no aplica.", "error")
    elif FacturaModel.query.filter_by(reservacion_id=rid).first() is not None:
        flash("No se puede eliminar: la reservación ya tiene una factura.", "error")
    else:
        ConsumoModel.query.filter_by(reservacion_id=rid).delete()
        db.session.delete(ReservacionModel.query.get(rid))
        db.session.commit()
        flash(f"Reservación #{rid} eliminada.", "ok")
    return redirect(url_for("main.reservaciones"))


# ---------------- Calendario / rack ----------------
@bp.route("/calendario")
@login_required
def calendario():
    anio = int(request.args.get("anio", 2026))
    mes = int(request.args.get("mes", 11))
    dias = cal.monthrange(anio, mes)[1]
    fechas = [date(anio, mes, d) for d in range(1, dias + 1)]
    habitaciones = HabitacionModel.query.order_by(HabitacionModel.numero).all()
    reservas = ReservacionModel.query.filter(ReservacionModel.estado != "cancelada").all()
    # matriz[habitacion_id][dia] = estado: libre | reservada | check-in
    matriz = {}
    for h in habitaciones:
        fila = {}
        for d in fechas:
            estado = "libre"
            for r in reservas:
                if r.habitacion_id == h.id and r.fecha_entrada <= d < r.fecha_salida:
                    estado = r.estado
                    break
            fila[d.day] = estado
        matriz[h.id] = fila
    mes_anterior = (date(anio, mes, 1) - timedelta(days=1))
    mes_siguiente = (date(anio, mes, dias) + timedelta(days=1))
    return render_template(
        "calendario.html", anio=anio, mes=mes, dias=dias, fechas=fechas,
        habitaciones=habitaciones, matriz=matriz,
        nombre_mes=cal.month_name[mes], mes_anterior=mes_anterior, mes_siguiente=mes_siguiente,
    )


# ---------------- Facturación ----------------
@bp.route("/facturacion")
@login_required
def facturacion():
    facturas = FacturaModel.query.order_by(FacturaModel.fecha_emision.desc()).all()
    reservas = ReservacionModel.query.filter(ReservacionModel.estado.in_(["check-in", "check-out"])).order_by(ReservacionModel.fecha_entrada).all()
    return render_template("facturacion.html", facturas=facturas, reservas=reservas, hab_dict=_habitaciones_dict(), hues_dict=_huespedes_dict())


@bp.route("/facturacion/nueva/<int:rid>", methods=["GET", "POST"])
@login_required
def nueva_factura(rid):
    """Pide los datos de facturación (nombre, RFC y correo) y genera
    la factura de una reservación. Si ya tiene factura, muestra esa."""
    r = repos()
    existente = r["fac"].obtener_por_reservacion(rid)
    if existente:
        return redirect(url_for("main.ver_factura", fid=existente.id))
    reserva = r["res"].obtener(rid)
    if reserva is None:
        flash("La reservación no existe.", "error")
        return redirect(url_for("main.facturacion"))
    huesped = r["hues"].obtener(reserva.huesped_id) if reserva.huesped_id else None
    habitacion = r["hab"].obtener(reserva.habitacion_id) if reserva.habitacion_id else None
    consumos = r["con"].listar_por_reservacion(rid)
    previa = FacturaEnt.desde_reservacion(
        reserva,
        precio_noche=habitacion.precio_noche if habitacion else Decimal("0"),
        consumos=consumos,
        rfc=huesped.rfc if huesped else "",
        cliente=huesped.nombre if huesped else "",
    )
    if request.method == "POST":
        cliente = request.form.get("cliente", "").strip()
        rfc = request.form.get("rfc", "").strip().upper()
        correo = request.form.get("correo", "").strip()
        if not cliente:
            flash("Escribe el nombre o razón social para la factura.", "error")
        elif not rfc:
            flash("Escribe el RFC para la factura. Si el cliente no tiene RFC, usa XAXX010101000.", "error")
        elif correo and "@" not in correo:
            flash("El correo no se ve válido.", "error")
        else:
            try:
                factura = GenerarFactura(r["res"], r["hab"], r["hues"], r["fac"], r["con"]).execute(rid, cliente=cliente, rfc=rfc, correo=correo)
                flash(f"Factura {factura.folio} generada: total ${float(factura.total):,.2f} MXN (IVA 16% incluido).", "ok")
                _res, _hab, _hues = _datos_reserva(rid)
                if _hues:
                    _notificar(formatear_factura(factura.folio, rid, cliente, float(factura.total)))
                return redirect(url_for("main.ver_factura", fid=factura.id))
            except ValueError as exc:
                flash(str(exc), "error")
        return render_template(
            "datos_factura.html", tipo="reservacion", reserva=reserva, huesped=huesped,
            habitacion=habitacion, consumos=consumos, total=previa.total,
            cliente=cliente, rfc=rfc, correo=correo,
        )
    return render_template(
        "datos_factura.html", tipo="reservacion", reserva=reserva, huesped=huesped,
        habitacion=habitacion, consumos=consumos, total=previa.total,
        cliente=huesped.nombre if huesped else "",
        rfc=huesped.rfc if huesped else "",
        correo=huesped.email if huesped else "",
    )


@bp.route("/facturacion/<int:fid>")
@login_required
def ver_factura(fid):
    factura = FacturaModel.query.get_or_404(fid)
    reserva = ReservacionModel.query.get(factura.reservacion_id) if factura.reservacion_id else None
    huesped = HuespedModel.query.get(factura.huesped_id) if factura.huesped_id else None
    habitacion = HabitacionModel.query.get(reserva.habitacion_id) if reserva else None
    consumos = ConsumoModel.query.filter_by(reservacion_id=factura.reservacion_id).all() if factura.reservacion_id else []
    venta = VentaModel.query.get(factura.venta_id) if factura.venta_id else None
    items = DetalleVentaModel.query.filter_by(venta_id=factura.venta_id).order_by(DetalleVentaModel.id).all() if factura.venta_id else []
    return render_template("factura_detalle.html", factura=factura, reserva=reserva, huesped=huesped, habitacion=habitacion, consumos=consumos, venta=venta, items=items)


@bp.route("/reservaciones/<int:rid>/consumo", methods=["POST"])
@login_required
def agregar_consumo(rid):
    try:
        consumo = ConsumoEnt(
            reservacion_id=rid,
            concepto=request.form["concepto"],
            monto=Decimal(request.form["monto"]),
            fecha=_hoy(),
        )
        SQLAlchemyConsumoRepository().guardar(consumo)
        flash("Consumo agregado a la reservación.", "ok")
    except Exception as exc:  # noqa: BLE001
        flash(f"No se pudo agregar el consumo: {exc}", "error")
    return redirect(url_for("main.facturacion"))


# ---------------- Cobranza (caja) ----------------
@bp.route("/caja")
@login_required
def caja():
    productos = ProductoModel.query.filter_by(activo=True).order_by(ProductoModel.categoria, ProductoModel.nombre).all()
    alimentos = [p for p in productos if p.categoria == "alimentos"]
    paquetes = [p for p in productos if p.categoria == "paquetes"]
    habitaciones = HabitacionModel.query.order_by(HabitacionModel.numero).all()
    ventas = VentaModel.query.order_by(VentaModel.fecha.desc()).limit(10).all()
    # Factura de cada venta (para el botón Facturar / Ver factura).
    facturas_venta = {
        f.venta_id: f.id
        for f in FacturaModel.query.filter(FacturaModel.venta_id.isnot(None)).all()
    }
    return render_template(
        "caja.html",
        alimentos=alimentos, paquetes=paquetes, habitaciones=habitaciones,
        ventas=ventas, facturas_venta=facturas_venta,
        huespedes=HuespedModel.query.order_by(HuespedModel.nombre).all(),
    )


@bp.route("/caja/cobrar", methods=["POST"])
@login_required
def cobrar():
    r = repos()
    try:
        items = json.loads(request.form.get("items", "[]"))
        monto = request.form.get("monto_recibido", "").strip()
        venta = RegistrarVenta(r["prod"], r["hab"], r["venta"]).execute(
            cliente=request.form.get("cliente", "Cliente general"),
            metodo_pago=request.form.get("metodo_pago", "efectivo"),
            items=items,
            monto_recibido=Decimal(monto) if monto else None,
            huesped_id=int(request.form["huesped_id"]) if request.form.get("huesped_id") else None,
        )
        flash(
            f"Venta {venta.folio} cobrada: total ${float(venta.total):,.2f} MXN "
            f"con {venta.metodo_pago}" + (f", cambio ${float(venta.cambio):,.2f} MXN." if venta.cambio else "."),
            "ok",
        )
        _notificar(formatear_venta(venta.folio, venta.cliente, float(venta.total), venta.metodo_pago, float(venta.cambio)))
        # Documento: recibo sencillo (por defecto) o factura. Con factura,
        # se pide primero el formulario con los datos de facturación.
        if request.form.get("documento", "recibo") == "factura":
            return redirect(url_for("main.facturar_venta", vid=venta.id))
        return redirect(url_for("main.ver_recibo", vid=venta.id))
    except ValueError as exc:
        flash(str(exc), "error")
    except Exception as exc:  # noqa: BLE001
        flash(f"No se pudo cobrar la venta: {exc}", "error")
    return redirect(url_for("main.caja"))


@bp.route("/caja/recibo/<int:vid>/factura", methods=["GET", "POST"])
@login_required
def facturar_venta(vid):
    """Pide los datos de facturación (nombre, RFC y correo) y genera
    la factura de una venta de caja (desde el cobro o el recibo)."""
    r = repos()
    existente = r["fac"].obtener_por_venta(vid)
    if existente:
        return redirect(url_for("main.ver_factura", fid=existente.id))
    venta = r["venta"].obtener(vid)
    if venta is None:
        flash("La venta no existe.", "error")
        return redirect(url_for("main.caja"))
    huesped = r["hues"].obtener(venta.huesped_id) if venta.huesped_id else None
    if request.method == "POST":
        cliente = request.form.get("cliente", "").strip()
        rfc = request.form.get("rfc", "").strip().upper()
        correo = request.form.get("correo", "").strip()
        if not cliente:
            flash("Escribe el nombre o razón social para la factura.", "error")
        elif not rfc:
            flash("Escribe el RFC para la factura. Si el cliente no tiene RFC, usa XAXX010101000.", "error")
        elif correo and "@" not in correo:
            flash("El correo no se ve válido.", "error")
        else:
            try:
                factura = GenerarFacturaVenta(r["venta"], r["hues"], r["fac"]).execute(vid, cliente=cliente, rfc=rfc, correo=correo)
                flash(f"Factura {factura.folio} generada: total ${float(factura.total):,.2f} MXN (IVA 16% incluido).", "ok")
                _notificar(formatear_factura(factura.folio, 0, cliente, float(factura.total), venta_folio=venta.folio))
                return redirect(url_for("main.ver_factura", fid=factura.id))
            except ValueError as exc:
                flash(str(exc), "error")
        return render_template(
            "datos_factura.html", tipo="venta", venta=venta, huesped=huesped,
            cliente=cliente, rfc=rfc, correo=correo,
        )
    return render_template(
        "datos_factura.html", tipo="venta", venta=venta, huesped=huesped,
        cliente=venta.cliente,
        rfc=huesped.rfc if huesped else "",
        correo=huesped.email if huesped else "",
    )


@bp.route("/caja/recibo/<int:vid>")
@login_required
def ver_recibo(vid):
    venta = VentaModel.query.get_or_404(vid)
    huesped = HuespedModel.query.get(venta.huesped_id) if venta.huesped_id else None
    items = DetalleVentaModel.query.filter_by(venta_id=vid).order_by(DetalleVentaModel.id).all()
    factura = FacturaModel.query.filter_by(venta_id=vid).first()
    return render_template("recibo.html", venta=venta, huesped=huesped, items=items, factura=factura)


@bp.route("/caja/corte")
@solo_admin
def corte():
    fecha_txt = request.args.get("fecha", _hoy().isoformat())
    try:
        dia = _parse_fecha(fecha_txt)
    except Exception:
        dia = _hoy()
        fecha_txt = dia.isoformat()
    r = repos()
    resumen = CorteDia(r["venta"], r["fac"]).execute(dia)
    cierre = CierreModel.query.filter_by(fecha=dia).first()
    cierres = CierreModel.query.order_by(CierreModel.fecha.desc()).limit(10).all()
    return render_template("corte.html", resumen=resumen, cierre=cierre, cierres=cierres, fecha=fecha_txt)


@bp.route("/caja/corte/cerrar", methods=["POST"])
@solo_admin
def cerrar_corte():
    dia = _parse_fecha(request.form["fecha"])
    existente = CierreModel.query.filter_by(fecha=dia).first()
    if existente:
        flash(f"El día {dia.strftime('%d/%m/%Y')} ya tiene un cierre registrado.", "error")
        return redirect(url_for("main.corte", fecha=dia.isoformat()))
    r = repos()
    resumen = CorteDia(r["venta"], r["fac"]).execute(dia)
    cierre = CierreModel(
        fecha=dia, num_ventas=resumen["num_ventas"], total_ventas=resumen["total_ventas"],
        efectivo=resumen["efectivo"], tarjeta=resumen["tarjeta"],
        transferencia=resumen["transferencia"], cambio_total=resumen["cambio_total"],
        efectivo_neto=resumen["efectivo_neto"], num_facturas=resumen["num_facturas"],
        total_facturas=resumen["total_facturas"],
    )
    db.session.add(cierre)
    db.session.commit()
    flash(
        f"Día {dia.strftime('%d/%m/%Y')} cerrado: {resumen['num_ventas']} ventas, "
        f"total ${resumen['total_ventas']:,.2f} MXN, efectivo neto en caja ${resumen['efectivo_neto']:,.2f} MXN.",
        "ok",
    )
    _notificar(
        f"🧾 <b>Corte del día {dia.strftime('%d/%m/%Y')}</b>\n"
        f"Ventas: {resumen['num_ventas']} — Total: ${resumen['total_ventas']:,.2f} MXN\n"
        f"💵 Efectivo neto en caja: ${resumen['efectivo_neto']:,.2f} MXN"
    )
    return redirect(url_for("main.corte", fecha=dia.isoformat()))


# ---------------- Productos (catálogo de caja) ----------------
def _parse_stock(valor: str) -> int | None:
    """Cantidad en inventario: vacío = sin control de stock."""
    valor = (valor or "").strip()
    if not valor:
        return None
    stock = int(valor)
    if stock < 0:
        raise ValueError("La cantidad en inventario no puede ser negativa.")
    return stock


@bp.route("/caja/productos", methods=["GET", "POST"])
@solo_admin
def productos():
    r = repos()
    if request.method == "POST":
        try:
            p = ProductoEnt(
                nombre=request.form["nombre"].strip(),
                categoria=request.form["categoria"],
                precio=Decimal(request.form["precio"]),
                costo=Decimal(request.form.get("costo", "0") or "0"),
                descripcion=request.form.get("descripcion", ""),
                activo=True,
                stock=_parse_stock(request.form.get("stock", "")),
            )
            if not p.nombre:
                raise ValueError("El producto necesita un nombre.")
            if p.precio < 0:
                raise ValueError("El precio no puede ser negativo.")
            if p.costo < 0:
                raise ValueError("El costo no puede ser negativo.")
            r["prod"].guardar(p)
            flash(f"Producto {p.nombre} agregado.", "ok")
        except ValueError as exc:
            flash(str(exc), "error")
        except Exception as exc:  # noqa: BLE001
            flash(f"No se pudo guardar el producto: {exc}", "error")
        return redirect(url_for("main.productos"))
    return render_template(
        "productos.html",
        productos=ProductoModel.query.order_by(ProductoModel.categoria, ProductoModel.nombre).all(),
    )


@bp.route("/caja/productos/<int:pid>/editar", methods=["GET", "POST"])
@solo_admin
def editar_producto(pid):
    r = repos()
    producto = r["prod"].obtener(pid)
    if producto is None:
        flash("El producto no existe.", "error")
        return redirect(url_for("main.productos"))
    if request.method == "POST":
        try:
            producto.nombre = request.form["nombre"].strip()
            producto.categoria = request.form["categoria"]
            producto.precio = Decimal(request.form["precio"])
            producto.costo = Decimal(request.form.get("costo", "0") or "0")
            producto.descripcion = request.form.get("descripcion", "")
            producto.stock = _parse_stock(request.form.get("stock", ""))
            producto.activo = "activo" in request.form
            if not producto.nombre:
                raise ValueError("El producto necesita un nombre.")
            if producto.precio < 0:
                raise ValueError("El precio no puede ser negativo.")
            if producto.costo < 0:
                raise ValueError("El costo no puede ser negativo.")
            r["prod"].guardar(producto)
            flash(f"Producto {producto.nombre} actualizado.", "ok")
            return redirect(url_for("main.productos"))
        except ValueError as exc:
            flash(str(exc), "error")
        except Exception as exc:  # noqa: BLE001
            flash(f"No se pudo guardar el producto: {exc}", "error")
    return render_template(
        "productos.html",
        productos=ProductoModel.query.order_by(ProductoModel.categoria, ProductoModel.nombre).all(),
        producto_editar=producto,
    )


@bp.route("/caja/productos/<int:pid>/eliminar", methods=["POST"])
@solo_admin
def eliminar_producto(pid):
    r = repos()
    producto = r["prod"].obtener(pid)
    if producto is None:
        flash("El producto no existe.", "error")
    elif DetalleVentaModel.query.filter_by(producto_id=pid).first() is not None:
        # Ya tiene ventas: no se borra, se desactiva para conservar el historial.
        producto.activo = False
        r["prod"].guardar(producto)
        flash(
            f"{producto.nombre} ya tiene ventas, así que se desactivó: ya no aparece en Caja, "
            "pero su historial se conserva.",
            "ok",
        )
    else:
        m = ProductoModel.query.get(pid)
        db.session.delete(m)
        db.session.commit()
        flash(f"Producto {producto.nombre} eliminado.", "ok")
    return redirect(url_for("main.productos"))


# ---------------- Reportes ----------------
@bp.route("/reportes")
@solo_admin
def reportes():
    anio = int(request.args.get("anio", 2026))
    mes = int(request.args.get("mes", 11))
    r = repos()
    reporte = ReporteOcupacionMensual(r["res"], r["hab"], r["fac"]).execute(anio, mes)
    return render_template("reportes.html", reporte=reporte, nombre_mes=cal.month_name[mes])


@bp.route("/reportes/ventas")
@solo_admin
def reporte_ventas():
    hoy = _hoy()
    anio = int(request.args.get("anio", hoy.year))
    mes = int(request.args.get("mes", hoy.month))
    r = repos()
    reporte = ReporteVentas(r["venta"], r["fac"], r["prod"]).execute(anio, mes)
    # Si el mes actual no tiene nada, mostrar noviembre 2026 (datos demo)
    if not reporte["num_ventas"] and not reporte["num_facturas"] and (anio, mes) != (2026, 11):
        anio, mes = 2026, 11
        reporte = ReporteVentas(r["venta"], r["fac"], r["prod"]).execute(anio, mes)
    return render_template(
        "reporte_ventas.html", reporte=reporte, nombre_mes=cal.month_name[mes],
        hues_dict=_huespedes_dict(),
    )


# ---------------- API REST ----------------
@bp.route("/api/habitaciones")
def api_habitaciones():
    return jsonify([
        {"id": h.id, "numero": h.numero, "tipo": h.tipo, "precio_noche": float(h.precio_noche), "capacidad": h.capacidad, "estado": h.estado}
        for h in HabitacionModel.query.order_by(HabitacionModel.numero).all()
    ])


@bp.route("/api/reservaciones")
def api_reservaciones():
    return jsonify([
        {"id": r.id, "habitacion_id": r.habitacion_id, "huesped_id": r.huesped_id,
         "fecha_entrada": r.fecha_entrada.isoformat(), "fecha_salida": r.fecha_salida.isoformat(),
         "estado": r.estado, "total": float(r.total), "origen": r.origen}
        for r in ReservacionModel.query.order_by(ReservacionModel.fecha_entrada).all()
    ])


@bp.route("/api/disponibilidad")
def api_disponibilidad():
    try:
        inicio = _parse_fecha(request.args["fecha_inicio"])
        fin = _parse_fecha(request.args["fecha_fin"])
    except Exception:
        return jsonify({"error": "Usa ?fecha_inicio=AAAA-MM-DD&fecha_fin=AAAA-MM-DD"}), 400
    r = repos()
    resultado = []
    for h in HabitacionModel.query.order_by(HabitacionModel.numero).all():
        solapadas = r["res"].buscar_solapadas(h.id, inicio, fin)
        resultado.append({
            "habitacion_id": h.id, "numero": h.numero, "tipo": h.tipo,
            "precio_noche": float(h.precio_noche),
            "disponible": not solapadas and h.estado != "mantenimiento",
        })
    return jsonify({"fecha_inicio": inicio.isoformat(), "fecha_fin": fin.isoformat(), "habitaciones": resultado})


@bp.route("/api/reservaciones", methods=["POST"])
def api_crear_reservacion():
    datos = request.get_json(force=True, silent=True) or {}
    try:
        r = repos()
        reserva = CrearReservacion(r["res"], r["hab"]).execute(
            habitacion_id=int(datos["habitacion_id"]),
            huesped_id=int(datos["huesped_id"]),
            fecha_entrada=_parse_fecha(datos["fecha_entrada"]),
            fecha_salida=_parse_fecha(datos["fecha_salida"]),
            origen=datos.get("origen", "directo"),
        )
        _res, _hab, _hues = _datos_reserva(reserva.id)
        if _res and _hab and _hues:
            _notificar(formatear_reservacion(
                reserva.id, _hab.numero, _hues.nombre,
                _res.fecha_entrada, _res.fecha_salida,
                reserva.noches(), float(reserva.total),
            ))
        return jsonify({"id": reserva.id, "estado": reserva.estado, "total": float(reserva.total), "noches": reserva.noches()}), 201
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except (KeyError, TypeError, ValueError) as exc:
        return jsonify({"error": f"Datos incompletos o inválidos: {exc}"}), 400


@bp.route("/api/telegram/test", methods=["POST"])
@login_required
def api_telegram_test():
    """Envía un mensaje de prueba por Telegram (para verificar token e id)."""
    notificador = TelegramNotificador()
    if not notificador.configurado:
        return jsonify({
            "ok": False,
            "error": "Telegram no está configurado. Pon TELEGRAM_BOT_TOKEN y TELEGRAM_CHAT_ID en el archivo .env (ver README, sección Chatbot de Telegram).",
        }), 400
    enviado = notificador.enviar("✅ <b>Turitec PMS</b>\nTu chatbot de Telegram está conectado y los avisos funcionan.")
    if enviado:
        return jsonify({"ok": True, "mensaje": "Mensaje de prueba enviado a Telegram."})
    return jsonify({"ok": False, "error": "No se pudo enviar el mensaje. Revisa el token y el id del chat."}), 502


@bp.route("/api/sync", methods=["POST"])
def api_sync():
    datos = request.get_json(force=True, silent=True) or {}
    try:
        inicio = _parse_fecha(datos.get("fecha_inicio", "2026-11-01"))
        fin = _parse_fecha(datos.get("fecha_fin", "2026-11-30"))
    except Exception:
        return jsonify({"error": "Fechas inválidas, usa AAAA-MM-DD."}), 400
    canales = datos.get("canales") or ["booking", "airbnb"]
    r = repos()
    resultados = SincronizarDisponibilidad(canal_simulado, r["hab"], r["res"]).execute(inicio, fin, canales)
    return jsonify({"sincronizado": True, "resultados": resultados})
