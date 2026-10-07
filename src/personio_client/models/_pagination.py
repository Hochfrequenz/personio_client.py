"""A page of a cursor-paginated list response of the Personio API v2."""

from __future__ import annotations

from typing import Annotated, Generic, TypeVar
from urllib.parse import unquote, urlsplit

from pydantic import BaseModel, ConfigDict, Field

ItemT = TypeVar("ItemT")


class PageLink(BaseModel):
    """A link in the `_meta` object of a list response."""

    model_config = ConfigDict(extra="ignore")

    href: str | None = None


class PageMeta(BaseModel):
    """The `_meta` object of a list response."""

    model_config = ConfigDict(extra="ignore")

    links: dict[str, PageLink] = Field(default_factory=dict)


class CursorPage(BaseModel, Generic[ItemT]):
    """One page of a cursor-paginated list response (`_data` and `_meta`).

    The specs describe the list responses inline, so this model is written by hand.
    """

    model_config = ConfigDict(extra="ignore")

    data: Annotated[list[ItemT], Field(alias="_data", default_factory=list)]
    meta: Annotated[PageMeta, Field(alias="_meta", default_factory=PageMeta)]

    @property
    def next_cursor(self) -> str | None:
        """The cursor of the next page, taken from the next link, or None on the last page.

        Only the query of the link is used: the link may be relative,
        and its path is wrong in some examples of the specs.
        A `+` stays a `+` (cursors may be base64 values), unlike in `urllib.parse.parse_qs`,
        which turns it into a space.
        """
        link = self.meta.links.get("next")
        if link is None or link.href is None:
            return None
        for parameter in urlsplit(link.href).query.split("&"):
            key, _, value = parameter.partition("=")
            if unquote(key) == "cursor":
                return unquote(value)
        return None
