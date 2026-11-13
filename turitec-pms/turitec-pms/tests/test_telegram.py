"""Pruebas de la integración con Telegram (sin red ni Telegram real).

El notificador se prueba con urlopen simulado; el chatbot se prueba
con una consulta falsa, sin base de datos.
"""
from datetime import date
from decimal import Decimal

from src.turitec.domain.entities import Habitacion, Reservacion
from src.turitec.infrastructure.telegram import (
    ConsultaTuritec, TelegramNotificador, TuritecBot, formatear_reservacion,
    responder,
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


class FakeReservaciones:
    def __init__(self, reservas):
        self._reservas = reservas

    def listar(self):
        return self._reservas

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
    assert "reservar" in responder("quiero reservar", consulta).lower()
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
