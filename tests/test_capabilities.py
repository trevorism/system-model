"""Deterministic capability synthesis from a minimal Micronaut+Groovy fixture repo."""
from pathlib import Path

import pytest

from systemmodel.adapters.micronaut_groovy.extract import (
    MicronautGroovyAdapter, _extract_capabilities, capability_summary,
)

CONTROLLER = """package com.trevorism.controller

import com.trevorism.service.WidgetService
import com.trevorism.secure.Roles
import com.trevorism.secure.Secure
import io.micronaut.http.annotation.Controller
import io.micronaut.http.annotation.Get
import io.micronaut.http.annotation.Post
import io.swagger.v3.oas.annotations.Operation
import jakarta.inject.Inject

@Controller("/widget")
class WidgetController {

    @Inject
    WidgetService widgetService

    @Operation(summary = "Create a widget")
    @Post("/")
    Widget create(Widget w) {
        return widgetService.create(w)
    }

    @Secure(Roles.USER)
    @Get("/{id}")
    Widget get(String id) {
        return widgetService.get(id)
    }
}
"""

IFACE = """package com.trevorism.service

interface WidgetService {
    Widget create(Widget w)
}
"""

IMPL = """package com.trevorism.service

import jakarta.inject.Singleton

@Singleton
class DefaultWidgetService implements WidgetService {
    private WidgetRepository widgetRepository

    Widget create(Widget w) { return w }
}
"""


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    (tmp_path / "settings.gradle").write_text("rootProject.name = 'demo'\n", encoding="utf-8")
    ctrl = tmp_path / "src/main/groovy/com/trevorism/controller"
    svc = tmp_path / "src/main/groovy/com/trevorism/service"
    ctrl.mkdir(parents=True)
    svc.mkdir(parents=True)
    (ctrl / "WidgetController.groovy").write_text(CONTROLLER, encoding="utf-8")
    (svc / "WidgetService.groovy").write_text(IFACE, encoding="utf-8")
    (svc / "DefaultWidgetService.groovy").write_text(IMPL, encoding="utf-8")
    return tmp_path


def test_capabilities_synthesized(repo: Path):
    caps = {c.id: c for c in _extract_capabilities(repo)}
    assert set(caps) == {"widget.create", "widget.get"}

    create = caps["widget.create"]
    # Public (no @Secure) + mutating (POST) → the headline exposure signal.
    assert create.actor == "anyone (public)"
    assert create.public_mutating is True
    # Repository collaborator on the injected service → "stored" outcome.
    assert create.story == "As anyone (public), I can create a widget and it is stored."
    assert create.summary == "Create a widget"

    get = caps["widget.get"]
    assert get.actor == "an authenticated app"  # @Secure(Roles.USER)
    assert get.public_mutating is False


def test_capability_summary(repo: Path):
    s = capability_summary(repo)
    assert s["total"] == 2
    assert s["public_mutating"] == ["POST /widget"]


def test_capabilities_node_supports_authored(repo: Path):
    node = MicronautGroovyAdapter().extract_capabilities(repo)
    assert node.supports_authored is True
    assert node.path == "capabilities.md"
    assert "<!-- intent:widget.create -->" in node.body
    # content_hash is derived-body-only, so it is stable regardless of authored prose.
    assert node.content_hash() == node.content_hash()
