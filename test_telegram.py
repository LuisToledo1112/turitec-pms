"""Pruebas de la integración con Telegram (sin red ni Telegram real).

El notificador se prueba con urlopen simulado; el chatbot se prueba
con una consulta falsa, sin base de datos.
"""
from datetime import date
from decimal import Decimal

import pytest

from src.turitec.domain.entities import Habitacion, Huesped, Reservacion
from src.turitec.infrastructure.telegram import (
    ConsultaTuritec, RegistroReservas, TelegramNotificador, TuritecBot,
    avanzar_reserva, formatear_reservacion, referencia_reserva, responder,
)


class FakeRespuesta:
    def __init__(self, datos):
        self._datos = datos

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        import json

        return json.dumps(self._datos).encode("utf-8")


class FakeHabitaciones:
    def __init__(self, habitaciones):
        self._habitaciones = habitaciones

    def listar(self):
        return self._habitaciones

    def obtener(self, habitacion_id):
        for h in self._habitaciones:
            if h.id == habitacion_id:
                return h
        return None


class FakeHuespedes:
    def __init__(self, huespedes=None):
        self._huespedes = list(huespedes or [])

    def listar(self):
        return self._huespedes

    def obtener(self, huesped_id):
        for h in self._huespedes:
            if h.id == huesped_id:
                return h
        return None

    def guardar(self, huesped):
        if huesped.id is None:
            huesped.id = (max((h.id for h in self._huespedes), default=0) + 1)
            self._huespedes.append(huesped)
        return huesped


class FakeReservaciones:
    def __init__(self, reservas):
        self._reservas = reservas

    def listar(self):
        return self._reservas

    def obtener(self, reserva_id):
        for r in self._reservas:
            if r.id == reserva_id:
                return r
        return None

    def guardar(self, reserva):
        if reserva.id is None:
            reserva.id = (max((r.id for r in self._reservas), default=0) + 1)
            self._reservas.append(reserva)
        return reserva

    def buscar_solapadas(self, habitacion_id, entrada, salida, excluir_id=None):
        return [
            r for r in self._reservas
            if r.habitacion_id == habitacion_id
            and r.estado in ("reservada", "check-in")
            and r.fecha_entrada < salida and entrada < r.fecha_salida
        ]


class FakeFacturas:
    def listar(self):
        return []


def _consulta():
    habitaciones = [
        Habitacion(id=1, numero="101", tipo="Sencilla", precio_noche=Decimal("650"), capacidad=2, estado="libre"),
        Habitacion(id=2, numero="102", tipo="Doble", precio_noche=Decimal("950"), capacidad=3, estado="libre"),
    ]
    reservas = [
        Reservacion(id=1, habitacion_id=1, huesped_id=1,
                    fecha_entrada=date(2026, 11, 10), fecha_salida=date(2026, 11, 12),
                    estado="reservada", total=Decimal("1300")),
    ]
    return ConsultaTuritec(FakeHabitaciones(habitaciones), FakeReservaciones(reservas), FakeFacturas())


def test_formatear_reservacion_incluye_datos():
    texto = formatear_reservacion(5, "101", "Ana Pérez", date(2026, 11, 10), date(2026, 11, 12), 2, 1300.0)
    assert "#5" in texto and "101" in texto and "Ana Pérez" in texto
    assert "1,300.00" in texto


def test_notificador_sin_configuracion_no_llama_a_la_red(monkeypatch):
    llamadas = []
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: llamadas.append(1))
    noti = TelegramNotificador(token="", chat_id="")
    assert noti.configurado is False
    assert noti.enviar("hola") is False
    assert llamadas == []


def test_notificador_envia_cuando_esta_configurado(monkeypatch):
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *a, **k: FakeRespuesta({"ok": True, "result": {"message_id": 1}}),
    )
    noti = TelegramNotificador(token="TOKEN", chat_id="123")
    assert noti.configurado is True
    assert noti.enviar("hola") is True


def test_notificador_devuelve_false_si_telegram_falla(monkeypatch):
    def _falla(*a, **k):
        raise RuntimeError("sin red")

    monkeypatch.setattr("urllib.request.urlopen", _falla)
    noti = TelegramNotificador(token="TOKEN", chat_id="123")
    assert noti.enviar("hola") is False


def test_bot_responde_ayuda():
    assert "/disponibilidad" in responder("/ayuda", _consulta())


def test_bot_disponibilidad_muestra_libres_y_ocupadas():
    texto = responder("/disponibilidad 2026-11-10 2026-11-12", _consulta())
    assert "102" in texto  # libre en esas fechas
    assert "101" in texto  # ocupada por la reserva de ejemplo, ahora se muestra como ocupada
    assert "Ocupadas" in texto


def test_bot_precios_por_tipo():
    texto = responder("/precios", _consulta())
    assert "Sencilla" in texto and "650" in texto
    assert "Doble" in texto and "950" in texto


def test_bot_habitaciones_muestra_precio_y_estado():
    texto = responder("/habitaciones", _consulta())
    assert "101" in texto and "650" in texto
    assert "102" in texto and "950" in texto
    assert "Libre" in texto or "Ocupada" in texto


def test_bot_reservas_solo_para_personal():
    assert "solo para el personal" in responder("/reservas", _consulta(), es_admin=False)
    assert "solo para el personal" in responder("/ocupacion 2026-11", _consulta(), es_admin=False)
    assert "#1" in responder("/reservas", _consulta(), es_admin=True)


def test_bot_entiende_mensajes_sin_slash():
    consulta = _consulta()
    assert "101" in responder("habitaciones", consulta)
    assert "Sencilla" in responder("precios", consulta)
    assert "Precios" in responder("¿cuánto cuesta una habitación?", consulta)
    assert "Libres" in responder("¿hay habitaciones libres?", consulta)
    assert "Ocupada" in responder("¿qué habitaciones están ocupadas?", consulta)
    assert "Libres" in responder("disponibilidad 2026-11-10 2026-11-12", consulta)
    assert "nombre" in responder("quiero reservar", consulta)
    assert "/disponibilidad" in responder("hola", consulta)
    assert "/disponibilidad" in responder("no entiendo nada de esto", consulta)


def test_bot_disponibilidad_sin_libres():
    consulta = _consulta()
    # Reserva la 102 también en las mismas fechas
    consulta.reservaciones._reservas.append(
        Reservacion(id=2, habitacion_id=2, huesped_id=1,
                    fecha_entrada=date(2026, 11, 10), fecha_salida=date(2026, 11, 12),
                    estado="reservada", total=Decimal("1900"))
    )
    assert "No hay habitaciones libres" in responder("/disponibilidad 2026-11-10 2026-11-12", consulta)


def test_bot_reservas_y_ocupacion():
    assert "#1" in responder("/reservas", _consulta())
    texto = responder("/ocupacion 2026-11", _consulta())
    assert "Ocupación 2026-11" in texto
    assert "Tasa de ocupación" in texto


def test_bot_comando_desconocido():
    assert "/ayuda" in responder("/inventado", _consulta())


def test_bot_procesa_mensaje_con_app_context(monkeypatch):
    """El bot responde dentro del contexto de la app (antes fallaba con
    'Working outside of application context' al consultar la base)."""
    from src.turitec import create_app

    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *a, **k: FakeRespuesta({"ok": True, "result": {"message_id": 1}}),
    )
    app = create_app()
    noti = TelegramNotificador(token="TOKEN", chat_id="111")
    bot = TuritecBot(noti, _consulta(), app=app)
    update = {"update_id": 1, "message": {"chat": {"id": 999}, "text": "habitaciones"}}
    assert bot.procesar_update(update) is True


def test_bot_responde_a_clientes_pero_reservas_solo_al_personal(monkeypatch):
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *a, **k: FakeRespuesta({"ok": True, "result": {"message_id": 1}}),
    )
    noti = TelegramNotificador(token="TOKEN", chat_id="111")
    bot = TuritecBot(noti, _consulta())
    # Un cliente (otro chat) sí recibe respuesta a /habitaciones...
    update_cliente = {"update_id": 1, "message": {"chat": {"id": 999}, "text": "/habitaciones"}}
    assert bot.procesar_update(update_cliente) is True
    # ...pero /reservas le responde con la negativa, no con los datos.
    update_admin = {"update_id": 2, "message": {"chat": {"id": 111}, "text": "/reservas"}}
    assert bot.procesar_update(update_admin) is True
    assert "solo para el personal" in responder("/reservas", _consulta(), es_admin=False)


# ---------------- Reservaciones desde el chatbot ----------------

def _registro_relativo():
    """Registro con fechas relativas a hoy, para que las pruebas no caduquen."""
    from datetime import timedelta

    hoy = date.today()
    habitaciones = [
        Habitacion(id=1, numero="101", tipo="Sencilla", precio_noche=Decimal("650"), capacidad=2, estado="libre"),
        Habitacion(id=2, numero="102", tipo="Doble", precio_noche=Decimal("950"), capacidad=3, estado="libre"),
    ]
    reservas = [
        Reservacion(id=1, habitacion_id=1, huesped_id=1,
                    fecha_entrada=hoy + timedelta(days=2), fecha_salida=hoy + timedelta(days=4),
                    estado="reservada", total=Decimal("1300")),
    ]
    consulta = ConsultaTuritec(FakeHabitaciones(habitaciones), FakeReservaciones(reservas), FakeFacturas())
    registro = RegistroReservas(FakeHuespedes(), consulta.habitaciones, consulta.reservaciones)
    return registro, consulta


def _hacer_flujo(registro, nombre="Ana María López", contacto="ana@correo.mx",
                 entrada=None, salida=None, tipo="Doble"):
    """Recorre el flujo de reserva y devuelve (respuestas, creado)."""
    from datetime import timedelta

    hoy = date.today()
    entrada = entrada or (hoy + timedelta(days=10))
    salida = salida or (hoy + timedelta(days=12))
    estado = {"paso": "nombre", "datos": {}}
    respuestas = []
    for texto in [nombre, contacto, f"{entrada.isoformat()} {salida.isoformat()}", tipo, "SI"]:
        respuesta, terminado, creado = avanzar_reserva(estado, texto, registro)
        respuestas.append(respuesta)
        if terminado:
            return respuestas, creado
    return respuestas, None


def test_referencia_tiene_formato_tur():
    assert referencia_reserva(1) == "TUR-000001"
    assert referencia_reserva(123) == "TUR-000123"


def test_flujo_reserva_completo_da_referencia():
    registro, _ = _registro_relativo()
    respuestas, creado = _hacer_flujo(registro)
    assert creado is not None
    assert "TUR-000002" in respuestas[-1]  # la reserva 1 ya existía en los datos falsos
    assert creado["reserva"].estado == "reservada"
    assert creado["reserva"].origen == "telegram"
    assert float(creado["reserva"].total) == 1900.0  # 2 noches x $950
    assert creado["huesped"].nombre == "Ana María López"
    assert creado["huesped"].email == "ana@correo.mx"
    # La reservación quedó guardada y bloquea esas fechas (anti-sobreventa).
    assert len(registro.reservaciones.listar()) == 2


def test_flujo_reutiliza_huesped_con_mismo_contacto():
    registro, _ = _registro_relativo()
    _, creado1 = _hacer_flujo(registro)
    _, creado2 = _hacer_flujo(registro, nombre="Ana López", tipo="Sencilla")
    assert creado1["huesped"].id == creado2["huesped"].id
    assert len(registro.huespedes.listar()) == 1


def test_flujo_tipo_sin_disponibilidad_pide_otro_tipo():
    from datetime import timedelta

    registro, _ = _registro_relativo()
    hoy = date.today()
    estado = {"paso": "nombre", "datos": {}}
    avanzar_reserva(estado, "Ana López", registro)
    avanzar_reserva(estado, "9611234567", registro)
    avanzar_reserva(estado, f"{(hoy + timedelta(days=2)).isoformat()} {(hoy + timedelta(days=4)).isoformat()}", registro)
    # La Sencilla 101 está ocupada en esas fechas.
    respuesta, terminado, _ = avanzar_reserva(estado, "Sencilla", registro)
    assert terminado is False
    assert "no está libre" in respuesta
    assert estado["paso"] == "tipo"


def test_flujo_fechas_invalidas_vuelve_a_preguntar():
    registro, _ = _registro_relativo()
    estado = {"paso": "fechas", "datos": {"nombre": "Ana", "contacto": "ana@correo.mx"}}
    respuesta, terminado, _ = avanzar_reserva(estado, "cualquier cosa", registro)
    assert terminado is False
    assert "dos fechas" in respuesta
    respuesta, _, _ = avanzar_reserva(estado, "2026-12-14 2026-12-12", registro)
    assert "después" in respuesta


def test_flujo_cancelar_en_confirmacion():
    registro, _ = _registro_relativo()
    from datetime import timedelta

    hoy = date.today()
    estado = {"paso": "nombre", "datos": {}}
    avanzar_reserva(estado, "Ana López", registro)
    avanzar_reserva(estado, "ana@correo.mx", registro)
    avanzar_reserva(estado, f"{(hoy + timedelta(days=10)).isoformat()} {(hoy + timedelta(days=12)).isoformat()}", registro)
    avanzar_reserva(estado, "Doble", registro)
    respuesta, terminado, creado = avanzar_reserva(estado, "NO", registro)
    assert terminado is True
    assert creado is None
    assert "cancelada" in respuesta
    assert len(registro.reservaciones.listar()) == 1  # no se creó nada


def test_bot_consultar_por_referencia(monkeypatch):
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *a, **k: FakeRespuesta({"ok": True, "result": {"message_id": 1}}),
    )
    registro, consulta = _registro_relativo()
    noti = TelegramNotificador(token="TOKEN", chat_id="111")
    bot = TuritecBot(noti, consulta, registro=registro)
    # La reserva 1 existe en los datos falsos.
    assert "TUR-000001" in bot._consultar("TUR-000001")
    assert "No encontré" in bot._consultar("TUR-999999")
    update = {"update_id": 1, "message": {"chat": {"id": 999}, "text": "/consultar TUR-000001"}}
    assert bot.procesar_update(update) is True


def test_bot_flujo_reserva_por_mensajes(monkeypatch):
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *a, **k: FakeRespuesta({"ok": True, "result": {"message_id": 1}}),
    )
    from datetime import timedelta

    registro, consulta = _registro_relativo()
    noti = TelegramNotificador(token="TOKEN", chat_id="111")
    bot = TuritecBot(noti, consulta, registro=registro)
    hoy = date.today()
    mensajes = [
        "/start",
        "Soy cliente",
        "/reservar",
        "Ana María López",
        "ana@correo.mx",
        f"{(hoy + timedelta(days=10)).isoformat()} {(hoy + timedelta(days=12)).isoformat()}",
        "Doble",
        "SI",
    ]
    for i, texto in enumerate(mensajes):
        update = {"update_id": i, "message": {"chat": {"id": 999}, "text": texto}}
        assert bot.procesar_update(update) is True
    assert 999 not in bot.conversaciones  # el flujo terminó
    assert len(registro.reservaciones.listar()) == 2
    assert "/cancelar" in responder("/cancelar", consulta) or "curso" in responder("/cancelar", consulta)


# ---------------- Identificación de usuario, anti-duplicados y personal ----------------

class NotiCaptura(TelegramNotificador):
    """Notificador falso que guarda lo enviado, sin tocar la red."""

    def __init__(self, chat_id="111"):
        super().__init__(token="TOKEN", chat_id=chat_id)
        self.enviados: list[tuple] = []
        self.ultimo_teclado = None

    def enviar_a(self, chat_id, mensaje, teclado=None):
        self.enviados.append((chat_id, mensaje))
        self.ultimo_teclado = teclado
        return True

    def enviar(self, mensaje):
        return self.enviar_a(self.chat_id, mensaje)


class FakeVentas:
    def __init__(self, ventas):
        self._ventas = list(ventas)

    def listar(self):
        return list(self._ventas)

    def obtener(self, venta_id):
        return next((v for v in self._ventas if v.id == venta_id), None)

    def guardar(self, venta):
        self._ventas.append(venta)
        return venta


class FakeFacturasConDatos:
    def __init__(self, facturas):
        self._facturas = list(facturas)

    def listar(self):
        return list(self._facturas)

    def obtener(self, factura_id):
        return next((f for f in self._facturas if f.id == factura_id), None)

    def guardar(self, factura):
        self._facturas.append(factura)
        return factura

    def obtener_por_reservacion(self, reservacion_id):
        return next((f for f in self._facturas if f.reservacion_id == reservacion_id), None)


def _consulta_con_ventas():
    from datetime import datetime, timedelta

    from src.turitec.domain.entities import DetalleVenta, Factura, Venta

    hoy = datetime.now()
    venta = Venta(
        id=1, folio="VTA-00001", fecha=hoy, cliente="Cliente general",
        items=[DetalleVenta(concepto="Café americano", categoria="alimentos",
                            producto_id=6, precio_unitario=Decimal("35"), cantidad=2)],
        metodo_pago="efectivo", monto_recibido=Decimal("100"),
    )
    venta.calcular_totales()
    venta.validar_pago()
    factura = Factura(
        id=1, folio="TUR-00001", reservacion_id=1, huesped_id=1,
        fecha_emision=date.today(), subtotal=Decimal("1300"),
        iva=Decimal("208"), total=Decimal("1508"),
    )
    consulta = _consulta()
    consulta.ventas = FakeVentas([venta])
    consulta.facturas = FakeFacturasConDatos([factura])
    consulta.huespedes = FakeHuespedes([Huesped(id=1, nombre="Ana María López")])
    return consulta


def test_bloqueo_bot_segunda_instancia_rechazada(tmp_path):
    from src.turitec.infrastructure.telegram import adquirir_bloqueo_bot

    bloqueo = adquirir_bloqueo_bot(tmp_path / "bot.lock")
    with pytest.raises(RuntimeError, match="Ya hay otro bot"):
        adquirir_bloqueo_bot(tmp_path / "bot.lock")
    bloqueo.close()
    # Al liberar el bloqueo se puede arrancar de nuevo.
    bloqueo2 = adquirir_bloqueo_bot(tmp_path / "bot.lock")
    bloqueo2.close()


def test_flujo_reserva_no_manda_no_entendi_despues_de_confirmar():
    from datetime import timedelta

    registro, consulta = _registro_relativo()
    noti = NotiCaptura(chat_id="111")
    bot = TuritecBot(noti, consulta, registro=registro)
    hoy = date.today()
    entrada = (hoy + timedelta(days=10)).isoformat()
    salida = (hoy + timedelta(days=12)).isoformat()
    mensajes = ["/start", "Soy cliente", "/reservar", "Luis Toledo", "9611234567", f"{entrada} {salida}", "Doble", "SI"]
    for i, texto in enumerate(mensajes):
        update = {
            "update_id": i,
            "message": {
                "chat": {"id": 999},
                "from": {"id": 777, "username": "luistoledo", "first_name": "Luis", "last_name": "Toledo"},
                "text": texto,
            },
        }
        assert bot.procesar_update(update) is True
    del bot
    enviados_cliente = [t for cid, t in noti.enviados if cid == 999]
    assert len(enviados_cliente) == 8  # una respuesta por mensaje, ni una más
    assert "¡Reservación confirmada!" in enviados_cliente[-1]
    assert not any("No entendí" in t for t in enviados_cliente)
    # El aviso al personal identifica al cliente de Telegram.
    avisos = [t for cid, t in noti.enviados if str(cid) == "111"]
    assert len(avisos) == 1
    assert "@luistoledo" in avisos[0]
    assert "777" in avisos[0]


def test_update_duplicado_no_se_contesta_dos_veces():
    noti = NotiCaptura()
    bot = TuritecBot(noti, _consulta())
    update = {"update_id": 5, "message": {"chat": {"id": 999}, "text": "/habitaciones"}}
    assert bot.procesar_update(update) is True
    assert bot.procesar_update(update) is False
    assert len(noti.enviados) == 1


def test_miid_muestra_chat_y_usuario():
    noti = NotiCaptura()
    bot = TuritecBot(noti, _consulta())
    update = {
        "update_id": 1,
        "message": {
            "chat": {"id": 999},
            "from": {"id": 777, "username": "fabiola", "first_name": "Fabiola"},
            "text": "/miid",
        },
    }
    assert bot.procesar_update(update) is True
    assert len(noti.enviados) == 1
    texto = noti.enviados[0][1]
    assert "999" in texto and "777" in texto and "@fabiola" in texto


def test_ventas_facturas_corte_solo_para_personal():
    consulta = _consulta_con_ventas()
    for comando in ("/ventas", "/facturas", "/corte"):
        assert "solo para el personal" in responder(comando, consulta, es_admin=False)
    texto = responder("/ventas", consulta, es_admin=True)
    assert "VTA-00001" in texto and "81.20" in texto
    texto = responder("/facturas", consulta, es_admin=True)
    assert "TUR-00001" in texto and "Ana María López" in texto
    texto = responder("/corte", consulta, es_admin=True)
    assert "Corte del día" in texto and "Efectivo neto" in texto
    # También por palabras, sin slash: al cliente se le niega igual.
    assert "solo para el personal" in responder("¿cuánto vendimos hoy?", consulta, es_admin=False)
    assert "solo para el personal" in responder("muéstrame las facturas", consulta, es_admin=False)


def test_ayuda_del_personal_muestra_comandos_restringidos():
    consulta = _consulta_con_ventas()
    assert "/ventas" in responder("/ayuda", consulta, es_admin=True)
    assert "/corte" in responder("/ayuda", consulta, es_admin=True)
    assert "/ventas" not in responder("/ayuda", consulta, es_admin=False)
    assert "/corte" not in responder("/ayuda", consulta, es_admin=False)


# ---------------- Login del chat: cliente vs personal ----------------

from werkzeug.security import generate_password_hash

from src.turitec.domain.entities import Usuario


class FakeUsuarios:
    def __init__(self, usuarios):
        self._usuarios = {u.email.lower(): u for u in usuarios}

    def obtener_por_email(self, email):
        return self._usuarios.get((email or "").lower())

    def guardar(self, usuario):
        self._usuarios[usuario.email.lower()] = usuario
        return usuario


def _usuarios_admin():
    return FakeUsuarios([
        Usuario(id=1, nombre="Admin Turitec", email="admin@turitec.mx",
                password_hash=generate_password_hash("admin123"), rol="administrador"),
    ])


def _enviar(bot, noti, chat_id, texto, user_id=777, username="luistoledo", nombre="Luis"):
    update_id = len(noti.enviados) + 100
    update = {
        "update_id": update_id,
        "message": {
            "chat": {"id": chat_id},
            "from": {"id": user_id, "username": username, "first_name": nombre},
            "text": texto,
        },
    }
    assert bot.procesar_update(update) is True
    return noti.enviados[-1][1]


def test_bot_pide_elegir_rol_antes_de_responder():
    noti = NotiCaptura()
    bot = TuritecBot(noti, _consulta())
    texto = _enviar(bot, noti, 999, "habitaciones")
    assert "¿Quién eres?" in texto
    assert noti.ultimo_teclado is not None  # botones Cliente / Personal


def test_login_cliente_solo_ve_sus_reservaciones():
    from datetime import timedelta

    registro, consulta = _registro_relativo()
    noti = NotiCaptura(chat_id="111")
    bot = TuritecBot(noti, consulta, registro=registro)
    hoy = date.today()
    entrada = (hoy + timedelta(days=10)).isoformat()
    salida = (hoy + timedelta(days=12)).isoformat()
    _enviar(bot, noti, 999, "/start")
    texto = _enviar(bot, noti, 999, "Soy cliente")
    assert "Solo verás lo que tú generes" in texto
    # El cliente no ve reservaciones, ventas ni facturas.
    assert "solo para el personal" in _enviar(bot, noti, 999, "/reservas")
    assert "solo para el personal" in _enviar(bot, noti, 999, "/ventas")
    assert "solo para el personal" in _enviar(bot, noti, 999, "/facturas")
    # Hace su reservación (queda vinculada a su id de Telegram 777).
    for texto_msg in ["/reservar", "Luis Toledo", "9611234567", f"{entrada} {salida}", "Doble", "SI"]:
        _enviar(bot, noti, 999, texto_msg)
    assert "¡Reservación confirmada!" in noti.enviados[-1][1]
    assert "TUR-000002" in noti.enviados[-1][1]
    # Él sí puede consultar su reservación...
    assert "TUR-000002" in _enviar(bot, noti, 999, "/consultar TUR-000002")
    # ...pero otra persona (otro id de Telegram) no puede verla.
    _enviar(bot, noti, 555, "/start", user_id=888, username="otro", nombre="Otro")
    _enviar(bot, noti, 555, "Soy cliente", user_id=888, username="otro", nombre="Otro")
    texto = _enviar(bot, noti, 555, "/consultar TUR-000002", user_id=888, username="otro", nombre="Otro")
    assert "no es tuya" in texto


def test_login_personal_con_telefono_correo_y_contrasena():
    registro, consulta = _registro_relativo()
    noti = NotiCaptura(chat_id="111")
    bot = TuritecBot(noti, consulta, registro=registro, usuarios=_usuarios_admin())
    _enviar(bot, noti, 999, "/start")
    assert "teléfono" in _enviar(bot, noti, 999, "Soy del personal")
    assert "correo" in _enviar(bot, noti, 999, "9611234567")
    assert "contraseña" in _enviar(bot, noti, 999, "admin@turitec.mx")
    assert "incorrectos" in _enviar(bot, noti, 999, "clave-mal")
    texto = _enviar(bot, noti, 999, "admin123")
    assert "Entraste como personal" in texto
    # Ya como personal, ve las ventas y las facturas.
    assert "Ventas de hoy" in _enviar(bot, noti, 999, "/ventas") or "no hay ventas" in _enviar(bot, noti, 999, "/ventas")
    # Cierra sesión y vuelve a pedir el rol.
    texto = _enviar(bot, noti, 999, "/salir")
    assert "Sesión cerrada" in texto
    assert "¿Quién eres?" in _enviar(bot, noti, 999, "/ventas")


def test_login_personal_bloquea_tras_3_intentos():
    registro, consulta = _registro_relativo()
    noti = NotiCaptura(chat_id="111")
    bot = TuritecBot(noti, consulta, registro=registro, usuarios=_usuarios_admin())
    _enviar(bot, noti, 999, "/start")
    _enviar(bot, noti, 999, "Soy del personal")
    _enviar(bot, noti, 999, "9611234567")
    _enviar(bot, noti, 999, "admin@turitec.mx")
    _enviar(bot, noti, 999, "mal1")
    _enviar(bot, noti, 999, "mal2")
    texto = _enviar(bot, noti, 999, "mal3")
    assert "3 intentos" in texto
    assert 999 not in bot.login_en_curso


def test_sesion_admin_caducada_pide_login_de_nuevo():
    from datetime import datetime, timedelta

    registro, consulta = _registro_relativo()
    noti = NotiCaptura(chat_id="111")
    bot = TuritecBot(noti, consulta, registro=registro, usuarios=_usuarios_admin())
    _enviar(bot, noti, 999, "/start")
    _enviar(bot, noti, 999, "Soy del personal")
    _enviar(bot, noti, 999, "9611234567")
    _enviar(bot, noti, 999, "admin@turitec.mx")
    _enviar(bot, noti, 999, "admin123")
    assert bot._rol_sesion(999) == "admin"
    # La sesión caducó: hay que volver a entrar.
    bot.sesiones[999]["expira"] = datetime.now() - timedelta(minutes=1)
    texto = _enviar(bot, noti, 999, "/ventas")
    assert "¿Quién eres?" in texto
