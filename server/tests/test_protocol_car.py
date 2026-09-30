"""Step 2: car line protocol (PROTOCOL.md §5)."""

import pytest

from duoware.protocol import ProtocolError
from duoware.protocol.car import (
    Boot, CarError, IdReply, Identify, Led, MAX_LINE, Move, Ping, PingReply, Stop, StopAck, TtlExpired,
    encode_boot, encode_car_error, encode_id, encode_identify, encode_led, encode_move, encode_ping,
    encode_ping_reply, encode_stop, encode_stop_ack, encode_ttl_expired, parse_car_line, parse_command_line,
)


def code_of(line) -> str:
    with pytest.raises(ProtocolError) as e:
        parse_command_line(line)
    return e.value.code


def test_command_round_trips():
    assert parse_command_line(encode_move(-255, 255, 60).decode().strip()) == Move(-255, 255, 60)
    assert parse_command_line(encode_stop().decode().strip()) == Stop()
    assert parse_command_line(encode_ping(65535).decode().strip()) == Ping(65535)
    assert parse_command_line(encode_identify().decode().strip()) == Identify()
    assert parse_command_line(encode_led(True).decode().strip()) == Led(True)
    assert parse_command_line(encode_led(False).decode().strip()) == Led(False)
    assert encode_move(1, 2, 3) == b"M 1 2 3\n"


def test_encoders_reject_out_of_range():
    for args in ((256, 0, 10), (0, -256, 10), (0, 0, 0), (0, 0, 501)):
        with pytest.raises(ProtocolError):
            encode_move(*args)
    with pytest.raises(ProtocolError):
        encode_ping(65536)


def test_ttl_clamped_not_an_error_and_zero_ttl_is_range():
    assert parse_command_line("M 1 1 900") == Move(1, 1, 500)
    assert code_of("M 1 1 0") == "range"
    assert code_of("M 1 1 -5") == "range"


def test_range_errors():
    for line in ("M 300 0 10", "M 0 -256 10", "P 65536", "L 2", "L -1"):
        assert code_of(line) == "range", line


def test_parse_errors_and_whitespace_variants():
    for line in ("M 1 1", "M 1 1 1 1", "M a 1 1", "M  1 1 10", "M 1 1 10 ", "M +1 1 10", "M 1.5 1 10",
                 "S x", "? x", "P", "P -1", "P x", "L", "L x", "M 1 1 "):
        assert code_of(line) == "parse", repr(line)
    assert code_of(" M 1 1 10") == "unknown"   # a leading space makes the command word empty


def test_negative_zero_and_carriage_return():
    assert parse_command_line("M -0 -0 10") == Move(0, 0, 10)
    assert parse_command_line("S\r") == Stop()
    assert parse_command_line(b"P 7\r") == Ping(7)


def test_unknown_commands():
    for line in ("X", "m 1 1 1", "", "HELLO"):
        assert code_of(line) == "unknown", repr(line)


def test_31_vs_32_character_lines():
    assert code_of("X" * MAX_LINE) == "unknown"        # the length rule passes; the content is judged next
    assert code_of("X" * (MAX_LINE + 1)) == "long"
    assert len("M -255 -255 500") <= MAX_LINE


def test_car_lines():
    assert parse_car_line("ID DUO-A 1.0.0 1 ttl,led 734") == IdReply("DUO-A", "1.0.0", 1, ("ttl", "led"), 734)
    assert parse_car_line("P 12") == PingReply(12)
    assert parse_car_line("OK S\r") == StopAck()
    assert parse_car_line("X 3") == TtlExpired(3)
    assert parse_car_line("BOOT DUO-B 1.0.0 brownout") == Boot("DUO-B", "1.0.0", "brownout")
    for code in ("parse", "range", "long", "unknown"):
        assert parse_car_line(f"E {code}") == CarError(code)


def test_car_line_garbage_is_bad():
    for line in ("", "hello", "E weird", "BOOT a b c", "X x", "ID a b", "P", "OK", "Z" * 65,
                 "ID DUO-A 1.0.0 x ttl 1"):
        with pytest.raises(ProtocolError) as e:
            parse_car_line(line)
        assert e.value.code == "bad", line


def test_reply_encoders_round_trip():
    assert parse_car_line(encode_id("DUO-A", "0.1.0-sim", 1, ("ttl", "led"), 12345)) == \
        IdReply("DUO-A", "0.1.0-sim", 1, ("ttl", "led"), 12345)
    assert parse_car_line(encode_ping_reply(9)) == PingReply(9)
    assert parse_car_line(encode_stop_ack()) == StopAck()
    assert parse_car_line(encode_ttl_expired(65536)) == TtlExpired(0)
    assert parse_car_line(encode_boot("DUO-A", "0.1.0-sim", "power")) == Boot("DUO-A", "0.1.0-sim", "power")
    assert parse_car_line(encode_car_error("long")) == CarError("long")
