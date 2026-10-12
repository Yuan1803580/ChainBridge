from __future__ import annotations

import math

import pytest
import rfc8785

from app.jcs import canonical_json_bytes, canonical_sha256, verify_canonical_sha256


COMPLEX_VALUE = {
    "中文键": "值🌐",
    "nested": {
        "é": "e\u0301",
        "array": [3, 1.0, 0.000001, 1e30, {"😀": "line\ntext"}],
    },
    "a": True,
    "z": None,
}

EXPECTED_CANONICAL = (
    b'{"a":true,"nested":{"array":[3,1,0.000001,1e+30,{"\xf0\x9f\x98\x80":"line\\ntext"}],'
    b'"\xc3\xa9":"e\xcc\x81"},"z":null,"\xe4\xb8\xad\xe6\x96\x87\xe9\x94\xae":"\xe5\x80\xbc\xf0\x9f\x8c\x90"}'
)
EXPECTED_SHA256 = "sha256:08b030c5ecd691aefc764a519589212fc3be954dd8ccf159f48699439985e3b1"


def test_jcs_canonicalization_covers_unicode_nested_arrays_and_floats():
    assert canonical_json_bytes(COMPLEX_VALUE) == EXPECTED_CANONICAL
    assert canonical_sha256(COMPLEX_VALUE, prefix="sha256:") == EXPECTED_SHA256
    assert verify_canonical_sha256(
        COMPLEX_VALUE,
        EXPECTED_SHA256,
        prefix="sha256:",
    )


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_jcs_rejects_non_finite_numbers(value: float):
    with pytest.raises(rfc8785.FloatDomainError):
        canonical_json_bytes({"value": value})
