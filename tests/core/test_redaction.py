import pytest

from jurisync.core.redaction import mask_cpf


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("123.456.789-09", "123.***.***-09"),
        ("12345678909", "123.***.***-09"),
        ("123456789-09", "123.***.***-09"),
        ("123.456.78909", "123.***.***-09"),
        (" 123456.789-09 ", "123.***.***-09"),
        ("PARTE - CPF: 123.456.789-09 (AUTOR)", "PARTE - CPF: 123.***.***-09 (AUTOR)"),
        ("PARTE - CPF: 123456789-09 (AUTOR)", "PARTE - CPF: 123******09 (AUTOR)"),
        ("PARTE - CPF: 12345678909", "PARTE - CPF: 123******09"),
        ("12.345.678/0001-90", "12.345.678/0001-90"),
        ("12345678000190", "12345678000190"),
        ("5000001-23.2026.4.04.0000", "5000001-23.2026.4.04.0000"),
        ("OAB/SC 123.456", "OAB/SC 123.456"),
        ("SC123456", "SC123456"),
        ("processo 1234567890123", "processo 1234567890123"),
        ("", ""),
        (None, None),
    ],
)
def test_mask_cpf(value, expected):
    assert mask_cpf(value) == expected
