"""Adapters: one per target tech-stack. Importing a submodule registers its adapter.

Registration order is match order: the Micronaut adapter is tried first so a Groovy service that
also carries Java sources is never claimed by the library adapter.
"""

from systemmodel.adapters import micronaut_groovy  # noqa: F401  (registers adapter)
from systemmodel.adapters import java_gradle_library  # noqa: F401  (registers adapter)
