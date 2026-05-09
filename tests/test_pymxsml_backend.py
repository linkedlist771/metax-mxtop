from mxtop.backends.pymxsml import normalize_power_w, normalize_temperature_c


def test_normalize_temperature_handles_scaled_pymxsml_values():
    assert normalize_temperature_c(4775) == 47.75
    assert normalize_temperature_c(47.75) == 47.75


def test_normalize_power_handles_scaled_pymxsml_values():
    assert normalize_power_w(181800) == 181.8
    assert normalize_power_w(181.8) == 181.8
