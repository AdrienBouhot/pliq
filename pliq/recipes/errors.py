"""Le refus d'une recipe : une demande qu'on ne peut pas honorer."""

from __future__ import annotations


class RecipeError(ValueError):
    pass
