import math

import pytest
import rfc8785

from app.identity import assert_canonical_sha256, canonical_json, canonical_sha256


VALUE = {
    "中文键": "值🌐",
    "nested": {"é": "e\u0301", "array": [3, 1.0, 0.000001, 1e30, {"😀": "line\ntext"}]},
    "a": True,
    "z": None,
}
HASH = "sha256:08b030c5ecd691aefc764a519589212fc3be954dd8ccf159f48699439985e3b1"


def test_runner_uses_rfc8785_jcs_before_signing():
    assert canonical_sha256(VALUE) == HASH
    assert_canonical_sha256(VALUE, HASH)
    assert b'"nested":{"array":[3,1,0.000001,1e+30' in canonical_json(VALUE)


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_runner_rejects_non_finite_numbers(value: float):
    with pytest.raises(rfc8785.FloatDomainError):
        canonical_json({"value": value})
