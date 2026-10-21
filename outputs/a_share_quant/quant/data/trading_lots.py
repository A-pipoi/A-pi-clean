"""Exchange-specific minimum purchase sizes for A-share auction orders."""


def buy_quantity_error(symbol: str, quantity: int) -> str | None:
    code = str(symbol).upper()
    if code.endswith(".BJ"):
        return None if quantity >= 100 else "beijing_buy_minimum_100"
    if code.endswith(".SH") and code.startswith(("688", "689")):
        return None if quantity >= 200 else "star_buy_minimum_200"
    if code.endswith((".SH", ".SZ")) and quantity % 100:
        return "buy_quantity_must_be_multiple_of_100"
    return None


def rounded_buy_quantity(symbol: str, quantity: int) -> int:
    if str(symbol).upper().endswith(".BJ"):
        return quantity if quantity >= 100 else 0
    if str(symbol).upper().endswith(".SH") and str(symbol).startswith(("688", "689")):
        return quantity if quantity >= 200 else 0
    return quantity // 100 * 100
