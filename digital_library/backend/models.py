"""Request bodies. Limits mirror the pubs columns and CHECK constraints, so a form gets a clear
field error before the database is ever asked."""
from datetime import date
from decimal import Decimal
from typing import Literal, Optional, get_args

from pydantic import BaseModel, Field, field_validator

TITLE_ID = r"^[A-Z]{2}[0-9]{4}$"
AU_ID = r"^[0-9]{3}-[0-9]{2}-[0-9]{4}$"
PUB_ID = r"^[0-9]{4}$"
STOR_ID = r"^[0-9]{4}$"
EMP_ID = r"^[A-Za-z]{3}[0-9]{5}[A-Za-z]$"
# The one list of book types; frontend/lib.js keeps a copy of the labels (tests/test_units.py checks they match).
TYPE_LABEL = {"business": "Business", "psychology": "Psychology", "popular_comp": "Popular computing",
              "mod_cook": "Modern cooking", "trad_cook": "Traditional cooking", "UNDECIDED": "Undecided"}
TYPES = Literal[tuple(TYPE_LABEL)]
PAYTERMS = Literal["Net 30", "Net 60", "ON invoice"]
PAYTERM_VALUES = list(get_args(PAYTERMS))


def _blank_to_none(v):
    return None if isinstance(v, str) and not v.strip() else v


class _Form(BaseModel):
    model_config = {"str_strip_whitespace": True, "extra": "forbid"}

    @field_validator("*", mode="before")
    @classmethod
    def blanks(cls, v):
        return _blank_to_none(v)


class BookFields(_Form):
    title: str = Field(min_length=1, max_length=80)
    type: TYPES
    pub_id: str = Field(pattern=PUB_ID)
    price: Optional[Decimal] = Field(None, ge=0, max_digits=10, decimal_places=2)
    advance: Optional[Decimal] = Field(None, ge=0, max_digits=10, decimal_places=2)
    royalty: Optional[int] = Field(None, ge=0, le=100)
    notes: Optional[str] = Field(None, max_length=200)
    pubdate: Optional[date] = None


class BookIn(BookFields):
    title_id: str = Field(pattern=TITLE_ID)
    au_id: Optional[str] = Field(None, pattern=AU_ID)  # required for an admin; an author is always linked to themself


class SaleIn(_Form):
    stor_id: str = Field(pattern=STOR_ID)
    ord_num: str = Field(min_length=1, max_length=20)
    ord_date: date
    qty: int = Field(gt=0, le=32767)
    payterms: PAYTERMS
    title_id: str = Field(pattern=TITLE_ID)


class DiscountIn(_Form):
    discounttype: str = Field(min_length=1, max_length=40)
    stor_id: Optional[str] = Field(None, pattern=STOR_ID)
    lowqty: Optional[int] = Field(None, ge=0, le=32767)
    highqty: Optional[int] = Field(None, ge=0, le=32767)
    discount: Decimal = Field(ge=0, le=Decimal("99.99"), max_digits=4, decimal_places=2)

    @field_validator("highqty")
    @classmethod
    def range_order(cls, v, info):
        low = info.data.get("lowqty")
        if v is not None and low is not None and v < low:
            raise ValueError("High quantity must be at least the low quantity.")
        return v


class PublisherIn(_Form):
    pub_id: str = Field(pattern=PUB_ID)
    pub_name: str = Field(min_length=1, max_length=40)
    city: Optional[str] = Field(None, max_length=20)
    state: Optional[str] = Field(None, pattern=r"^[A-Za-z]{2}$")
    country: str = Field("USA", min_length=1, max_length=30)
    pr_info: Optional[str] = Field(None, max_length=2000)


class EmployeeIn(_Form):
    emp_id: str = Field(pattern=EMP_ID)
    fname: str = Field(min_length=1, max_length=20)
    minit: Optional[str] = Field(None, max_length=1)
    lname: str = Field(min_length=1, max_length=30)
    pub_id: str = Field(pattern=PUB_ID)
    job_id: int = Field(gt=0)
    job_lvl: int = Field(ge=0, le=32767)
    hire_date: Optional[date] = None


# ---------------------------------------------------------------- logins
# Passwords are taken exactly as typed, so these do not strip whitespace like the forms above.
USERNAME = r"^[a-z0-9][a-z0-9._-]{2,39}$"


class LoginIn(BaseModel):
    model_config = {"extra": "forbid"}
    username: str = Field(min_length=1, max_length=40)
    password: str = Field(min_length=1, max_length=200)
    remember: bool = False


class PasswordIn(BaseModel):
    model_config = {"extra": "forbid"}
    password: str = Field(max_length=200)


class ChangePasswordIn(PasswordIn):
    current_password: str = Field(max_length=200)


class NewLoginIn(PasswordIn):
    au_id: str = Field(pattern=AU_ID)
    username: str = Field(pattern=USERNAME)

    @field_validator("username", mode="before")
    @classmethod
    def lower(cls, v):
        return v.strip().lower() if isinstance(v, str) else v


class LoginActiveIn(BaseModel):
    model_config = {"extra": "forbid"}
    is_active: bool


class SwitchIn(BaseModel):
    model_config = {"extra": "forbid"}
    username: str = Field(min_length=1, max_length=40)


class LogoutIn(BaseModel):
    model_config = {"extra": "forbid"}
    all: bool = False



# ---------------------------------------------------------------- a brand-new author (authors row), optionally with a login
class AuthorIn(_Form):
    au_id: Optional[str] = Field(None, pattern=AU_ID)       # blank: the server picks a free one
    au_fname: str = Field(min_length=1, max_length=20)
    au_lname: str = Field(min_length=1, max_length=40)
    phone: Optional[str] = Field(None, max_length=12)       # blank: the column default, UNKNOWN
    address: Optional[str] = Field(None, max_length=40)
    city: Optional[str] = Field(None, max_length=20)
    state: Optional[str] = Field(None, pattern=r"^[A-Za-z]{2}$")
    zip: Optional[str] = Field(None, pattern=r"^[0-9]{5}$")
    contract: bool


class AuthorLoginIn(PasswordIn):
    username: str = Field(pattern=USERNAME)

    @field_validator("username", mode="before")
    @classmethod
    def lower(cls, v):
        return v.strip().lower() if isinstance(v, str) else v


class NewAuthorIn(BaseModel):
    model_config = {"extra": "forbid"}
    author: AuthorIn
    login: Optional[AuthorLoginIn] = None
