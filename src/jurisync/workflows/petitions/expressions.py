"""Safe evaluator for the conditions in a petition model, such as ``autor.menor == true && viagem.x != null``.

Evaluation is three-valued: a field that was not informed makes a comparison unknown (``None``) unless ``&&`` or
``||`` can decide without it. The caller gets the fields that are still missing, so it can ask for them instead of
guessing.
"""

import re

TOKEN = re.compile(r'\s*(&&|\|\||==|!=|\(|\)|"[^"]*"|[^\s()=!&|"]+)')
LITERALS = {"true": True, "false": False, "null": None}


class ExpressionError(ValueError):
    pass


def _tokens(expression):
    tokens, position = [], 0
    while position < len(expression):
        match = TOKEN.match(expression, position)
        if not match:
            if not expression[position:].strip():
                break
            raise ExpressionError(f"Invalid condition: {expression}")
        tokens.append(match.group(1))
        position = match.end()
    return tokens


class _Parser:
    def __init__(self, expression, lookup):
        self.expression = expression
        self.tokens = _tokens(expression)
        self.lookup = lookup
        self.missing = set()

    def _peek(self):
        return self.tokens[0] if self.tokens else None

    def _next(self):
        if not self.tokens:
            raise ExpressionError(f"Incomplete condition: {self.expression}")
        return self.tokens.pop(0)

    def parse(self):
        value = self._or()
        if self.tokens:
            raise ExpressionError(f"Invalid condition: {self.expression}")
        return value

    def _or(self):
        value = self._and()
        while self._peek() == "||":
            self._next()
            right = self._and()
            value = True if value is True or right is True else None if value is None or right is None else False
        return value

    def _and(self):
        value = self._comparison()
        while self._peek() == "&&":
            self._next()
            right = self._comparison()
            value = False if value is False or right is False else None if value is None or right is None else True
        return value

    def _comparison(self):
        if self._peek() == "(":
            self._next()
            value = self._or()
            if self._next() != ")":
                raise ExpressionError(f"Invalid condition: {self.expression}")
            return value
        field = self._next()
        operator = self._peek()
        if operator not in ("==", "!="):
            raise ExpressionError(f"Invalid condition: {self.expression}")
        self._next()
        raw = self._next()
        expected = raw[1:-1] if raw.startswith('"') else LITERALS.get(raw, raw)
        actual = self.lookup(field)
        if actual is None and expected is not None:
            self.missing.add(field)
            return None
        return (actual == expected) if operator == "==" else (actual != expected)


def evaluate(expression, lookup):
    """Return ``(True | False | None, missing_fields)``. ``None`` means it depends on a field not informed."""
    text = (expression or "").strip()
    if not text or text == "sempre":
        return True, set()
    parser = _Parser(text, lookup)
    value = parser.parse()
    return value, parser.missing if value is None else set()
