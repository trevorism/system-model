"""Micronaut + Groovy (Gradle, GCP App Engine) adapter — the Trevorism backend archetype."""

from systemmodel.adapters.micronaut_groovy.extract import MicronautGroovyAdapter
from systemmodel.core.adapter import register

register(MicronautGroovyAdapter())
