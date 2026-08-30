"""Small shared helpers for interpreting gateway data (Q87)."""


def alarm_flag_active(value) -> bool:
    """Whether a gateway boolean-ish alarm flag is set.

    Active for ``True`` / ``1`` / ``"true"`` / ``"1"`` — every encoding the
    central might use for a raised flag; everything else (``False``,
    ``"false"``, ``None``, absent) is inactive. Shared by the panel's
    TRIGGERED derivation and the trigger binary sensors so the two never
    disagree and the security-critical panel never under-reports a real
    trigger on a non-boolean encoding.
    """
    return value in (True, 1, "true", "1")
