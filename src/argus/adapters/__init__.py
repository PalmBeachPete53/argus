from .base import BankAdapter
from .boc import BoCAdapter
from .boe import BoEAdapter
from .boj import BoJAdapter
from .ecb import ECBAdapter
from .fed import FedAdapter
from .norges import NorgesBankAdapter
from .rba import RBAAdapter
from .rbnz import RBNZAdapter
from .riksbank import RiksbankAdapter
from .snb import SNBAdapter

ALL_ADAPTERS: list[BankAdapter] = [
    FedAdapter(),
    ECBAdapter(),
    BoEAdapter(),
    BoJAdapter(),
    SNBAdapter(),
    BoCAdapter(),
    RBAAdapter(),
    RBNZAdapter(),
    NorgesBankAdapter(),
    RiksbankAdapter(),
]

__all__ = [
    "BankAdapter",
    "FedAdapter",
    "ECBAdapter",
    "BoEAdapter",
    "BoJAdapter",
    "SNBAdapter",
    "BoCAdapter",
    "RBAAdapter",
    "RBNZAdapter",
    "NorgesBankAdapter",
    "RiksbankAdapter",
    "ALL_ADAPTERS",
]