"""The template environment: ONE environment, namespaced plugin templates, undefined is fatal.

``Jinja2Templates(directory=...)`` cannot serve plugin directories at all (one FileSystemLoader), and
two environments would diverge silently. Undefined renders as a hard error: an undefined variable
rendered as ``''`` is how a CSRF field ships blank to every user while the tests stay green."""
from __future__ import annotations

import pytest
from jinja2 import TemplateNotFound, UndefinedError

from conftest import install_shell, permissions, simple_router
from services.webservice import templating


def _template_dir(tmp_path, name: str, text: str):
    directory = tmp_path / name
    directory.mkdir()
    (directory / "index.html").write_text(text, encoding="utf-8")
    return directory


def test_the_shell_template_renders_through_the_app_environment():
    from conftest import create_app
    registrar = install_shell(create_app(), node=None, config={})
    html = templating.render(registrar, "base.html")
    assert html.lstrip().startswith("<!DOCTYPE html>")
    assert "/static/shell.css" in html
    assert "{{" not in html and "{%" not in html, "unrendered template syntax in the output"


def test_an_undefined_variable_is_a_hard_error(tmp_path):
    from conftest import create_app
    registrar = install_shell(create_app(), node=None, config={})
    directory = _template_dir(tmp_path, "probe", "{{ missing_variable }}")
    registrar.register_pages("probe", simple_router("/probe/one"), templates=directory)
    with pytest.raises(UndefinedError):
        templating.render(registrar, "probe/index.html")


def test_plugin_templates_are_namespaced_and_cannot_shadow_each_other(tmp_path):
    from conftest import create_app
    registrar = install_shell(create_app(), node=None, config={})
    first = _template_dir(tmp_path, "alpha", "alpha-page")
    second = _template_dir(tmp_path, "beta", "beta-page")
    registrar.register_pages("alpha", simple_router("/probe/alpha"), templates=first)
    registrar.register_pages("beta", simple_router("/probe/beta"), templates=second)

 # both register the same template NAME; each must resolve inside its own namespace
    assert templating.render(registrar, "alpha/index.html") == "alpha-page"
    assert templating.render(registrar, "beta/index.html") == "beta-page"
    assert registrar.template_dirs["alpha"] == first


def test_a_plugin_template_dir_is_picked_up_when_it_registers_later(tmp_path):
    from conftest import create_app
    registrar = install_shell(create_app(), node=None, config={})
    assert registrar.environment is not None
    directory = _template_dir(tmp_path, "late", "late-page")
    registrar.register_pages("late", simple_router("/probe/late"), templates=directory)
    assert templating.render(registrar, "late/index.html") == "late-page"


def test_unregistering_an_owner_removes_its_template_namespace(tmp_path):
    from conftest import create_app
    registrar = install_shell(create_app(), node=None, config={})
    directory = _template_dir(tmp_path, "probe", "probe-page")
    registrar.register_pages("probe", simple_router("/probe/one"), templates=directory)
    assert templating.render(registrar, "probe/index.html") == "probe-page"
    registrar.unregister_owner("probe")
    with pytest.raises(TemplateNotFound):
        templating.render(registrar, "probe/index.html")


def test_a_missing_template_directory_is_refused(tmp_path):
    from conftest import create_app
    registrar = install_shell(create_app(), node=None, config={})
    with pytest.raises(Exception) as excinfo:
        registrar.register_pages("probe", simple_router("/probe/one"),
                                 templates=tmp_path / "nowhere")
    assert "do not exist" in str(excinfo.value)
