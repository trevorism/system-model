"""Plain Java + Gradle shared-library adapter — the client libraries the platform builds on."""

from systemmodel.adapters.java_gradle_library.extract import JavaGradleLibraryAdapter
from systemmodel.core.adapter import register

register(JavaGradleLibraryAdapter())
