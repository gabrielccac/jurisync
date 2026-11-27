import pytest

from jurisync.core import intimacao


def test_fields_carry_the_situation_and_the_deadline():
    assert intimacao.fields(intimacao.RUNNING, "2026-12-01", "portal") == {
        "situacao": "prazo_correndo",
        "prazoFinal": "2026-12-01",
        "prazoFinalTipo": "portal",
    }
    assert intimacao.fields(intimacao.AWAITING_CIENCIA)["prazoFinalTipo"] is None


def test_unknown_values_are_refused():
    with pytest.raises(ValueError):
        intimacao.fields("pendente")
    with pytest.raises(ValueError):
        intimacao.fields(intimacao.RUNNING, "2026-12-01", "chute")


def test_attention_is_a_subset_of_the_situations():
    assert set(intimacao.ATTENTION) <= set(intimacao.SITUATIONS)
