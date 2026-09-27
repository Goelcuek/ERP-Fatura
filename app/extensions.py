from decimal import Decimal, ROUND_HALF_UP

from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import Integer
from sqlalchemy.types import TypeDecorator

db = SQLAlchemy()


class ScaledDecimal(TypeDecorator):
    """Stores a Decimal as a scaled integer (e.g. kuruş) so SQLite never rounds.

    SUM()/comparisons in SQL stay exact because the column is a plain INTEGER.
    """

    impl = Integer
    cache_ok = True

    def __init__(self, scale=2, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.scale = scale
        self.factor = 10**scale
        self.quant = Decimal(1).scaleb(-scale)

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        d = Decimal(str(value)).quantize(self.quant, rounding=ROUND_HALF_UP)
        return int(d * self.factor)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return (Decimal(int(value)) / self.factor).quantize(self.quant)

    @property
    def python_type(self):
        return Decimal


def Money():
    return ScaledDecimal(2)


def Qty():
    return ScaledDecimal(3)
