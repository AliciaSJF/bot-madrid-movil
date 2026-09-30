from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from src.api.auth import validate_new_house_password
from src.api.forms import BookingForm

MADRID = ZoneInfo("Europe/Madrid")


def future_form(**overrides) -> BookingForm:
    day = (datetime.now(MADRID) + timedelta(days=2)).date().isoformat()
    data = {"center": "  La Chopera ", "slot_date": day, "slot_time": "19:00"} | overrides
    return BookingForm(**data)


def test_valid_booking_form():
    form = future_form()
    assert form.validate(MADRID) is None
    assert form.center == "La Chopera"


def test_slot_keeps_madrid_wall_clock_time():
    slot = BookingForm(slot_date="2026-10-25", slot_time="19:00").slot_at(MADRID)
    # 25 oct 2026: primer día en horario de invierno (UTC+1)
    assert slot.utcoffset() == timedelta(hours=1)
    assert slot.hour == 19


def test_invalid_date_or_time():
    assert future_form(slot_time="25:00").validate(MADRID) == "Fecha u hora no válidas."
    assert future_form(slot_date="").validate(MADRID) == "Fecha u hora no válidas."


def test_unknown_service_or_mode():
    assert future_form(service="padel").validate(MADRID) == "Elige un servicio."
    assert future_form(mode="otro").validate(MADRID) == "Elige reservar u observar."


def test_past_slot():
    yesterday = (datetime.now(MADRID) - timedelta(days=1)).date().isoformat()
    assert future_form(slot_date=yesterday).validate(MADRID) == "Ese turno ya ha pasado."


def test_house_password_rules():
    assert validate_new_house_password("corta", "corta") is not None
    assert validate_new_house_password("larga-123", "otra-1234") == "Las contraseñas no coinciden."
    assert validate_new_house_password("larga-123", "larga-123") is None
