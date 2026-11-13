"""Adaptador web (Flask): vistas HTML + API REST JSON.

Las rutas llaman a los casos de uso, que a su vez usan los repositorios.
Ninguna regla de negocio vive aquí: solo HTTP, formularios y sesiones.
"""
from __future__ import annotations

import calendar as cal
from datetime import date, datetime, timedelta
from decimal import Decimal
from functools import wraps

from flask import (
    Blueprint, flash, jsonify, redirect, render_template, request, session, url_for,
)
from werkzeug.security import check_password_hash

from ...application.use_cases import (
    CancelarReservacion, CrearReservacion, GenerarFactura, HacerCheckIn,
    HacerCheckOut, ReporteOcupacionMensual, SincronizarDisponibilidad,
)
from ...domain.entities import Consumo as ConsumoEnt
from ...domain.entities import Habitacion as HabitacionEnt
from ...domain.entities import Huesped as HuespedEnt
from ..channels import canal_simulado
from ..telegram import (
    TelegramNotificador, formatear_cancelacion, formatear_check_in,
    formatear_check_out, formatear_factura, formatear_reservacion,
)
from ..db import (
    ConsumoModel, FacturaModel, HabitacionModel, HuespedModel, ReservacionModel,
    SQLAlchemyConsumoRepository, SQLAlchemyFacturaRepository,
    SQLAlchemyHabitacionRepository, SQLAlchemyHuespedRepository,
    SQLAlchemyReservacionRepository, SQLAlchemyUsuarioRepository, UsuarioModel,
    db,
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
    ingresos_mes = sum((float(f.total) for f in facturas_mes), 0.0)
    # Si no hay facturas este mes real, mostrar las de noviembre 2026 (datos demo)
    if not facturas_mes:
        facturas_mes = FacturaModel.query.filter(
            db.extract("year", FacturaModel.fecha_emision) == 2026,
            db.extract("month", FacturaModel.fecha_emision) == 11,
        ).all()
        ingresos_mes = sum((float(f.total) for f in facturas_mes), 0.0)
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
@login_required
def eliminar_habitacion(hid):
    try:
        repos()["hab"].eliminar(hid)
        flash("Habitación eliminada.", "ok")
    except Exception as exc:  # noqa: BLE001
        flash(f"No se pudo eliminar: {exc}", "error")
    return redirect(url_for("main.habitaciones"))


# ---------------- Huéspedes ----------------
@bp.route("/huespedes", methods=["GET", "POST"])
@login_required
def huespedes():
    r = repos()
    if request.method == "POST":
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
@login_required
def eliminar_huesped(hid):
    try:
        repos()["hues"].eliminar(hid)
        flash("Huésped eliminado.", "ok")
    except Exception as exc:  # noqa: BLE001
        flash(f"No se pudo eliminar: {exc}", "error")
    return redirect(url_for("main.huespedes"))


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
@login_required
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


@bp.route("/facturacion/nueva/<int:rid>", methods=["POST"])
@login_required
def nueva_factura(rid):
    r = repos()
    try:
        factura = GenerarFactura(r["res"], r["hab"], r["hues"], r["fac"], r["con"]).execute(rid)
        flash(f"Factura {factura.folio} generada: total ${float(factura.total):,.2f} MXN (IVA 16% incluido).", "ok")
        _res, _hab, _hues = _datos_reserva(rid)
        if _hues:
            _notificar(formatear_factura(factura.folio, rid, _hues.nombre, float(factura.total)))
        return redirect(url_for("main.ver_factura", fid=factura.id))
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("main.facturacion"))


@bp.route("/facturacion/<int:fid>")
@login_required
def ver_factura(fid):
    factura = FacturaModel.query.get_or_404(fid)
    reserva = ReservacionModel.query.get(factura.reservacion_id)
    huesped = HuespedModel.query.get(factura.huesped_id)
    habitacion = HabitacionModel.query.get(reserva.habitacion_id) if reserva else None
    consumos = ConsumoModel.query.filter_by(reservacion_id=factura.reservacion_id).all()
    return render_template("factura_detalle.html", factura=factura, reserva=reserva, huesped=huesped, habitacion=habitacion, consumos=consumos)


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


# ---------------- Reportes ----------------
@bp.route("/reportes")
@login_required
def reportes():
    anio = int(request.args.get("anio", 2026))
    mes = int(request.args.get("mes", 11))
    r = repos()
    reporte = ReporteOcupacionMensual(r["res"], r["hab"], r["fac"]).execute(anio, mes)
    return render_template("reportes.html", reporte=reporte, nombre_mes=cal.month_name[mes])


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
