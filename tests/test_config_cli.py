from types import SimpleNamespace

import pytest

from bioenv.cli import main

VALID_CATALOG = 'tools:\n  test:\n    package: test\n    commands: [test]\n'


def test_config_path_uses_default_and_does_not_require_catalog(monkeypatch, tmp_path, capsys):
    monkeypatch.delenv('BIOENV_CONFIG', raising=False)
    monkeypatch.chdir(tmp_path)
    assert main(['config', 'path']) == 0
    assert capsys.readouterr().out == '/etc/bioenv/tools.yaml\n'


def test_config_path_uses_environment_variable_and_makes_path_absolute(
    monkeypatch, tmp_path, capsys
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('BIOENV_CONFIG', 'catalog.yaml')
    catalog = tmp_path / 'catalog.yaml'
    catalog.write_text('invalid: [', encoding='utf-8')
    before = catalog.read_bytes()
    assert main(['config', 'path']) == 0
    assert capsys.readouterr().out == f'{catalog}\n'
    assert catalog.read_bytes() == before


def test_config_argument_has_priority_and_path_is_absolute(monkeypatch, tmp_path, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('BIOENV_CONFIG', '/ignored/catalog.yaml')
    assert main(['--config', 'relative.yaml', 'config', 'path']) == 0
    assert capsys.readouterr().out == f'{tmp_path / "relative.yaml"}\n'


@pytest.mark.parametrize(
    ('arguments', 'environment', 'expected'),
    [
        (['--editor', 'chosen --wait'], {'VISUAL': 'visual', 'EDITOR': 'editor'},
         ['chosen', '--wait']),
        ([], {'VISUAL': 'visual --block', 'EDITOR': 'editor'}, ['visual', '--block']),
        ([], {'EDITOR': 'editor --reuse-window'}, ['editor', '--reuse-window']),
        ([], {}, ['nano']),
    ],
)
def test_config_edit_selects_editor_and_splits_arguments(
    arguments, environment, expected, monkeypatch, tmp_path, capsys
):
    monkeypatch.delenv('VISUAL', raising=False)
    monkeypatch.delenv('EDITOR', raising=False)
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    catalog = tmp_path / 'catalog.yaml'
    catalog.write_text(VALID_CATALOG, encoding='utf-8')
    calls = []

    def run(command, check):
        calls.append((command, check))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr('bioenv.cli.subprocess.run', run)
    assert main(['--config', str(catalog), 'config', 'edit', *arguments]) == 0
    assert calls == [([*expected, str(catalog)], False)]
    assert capsys.readouterr().out == 'Catalog validation: OK (1 tools declared)\n'


def test_config_edit_reports_missing_editor(monkeypatch, tmp_path, capsys):
    catalog = tmp_path / 'catalog.yaml'
    catalog.write_text(VALID_CATALOG, encoding='utf-8')
    monkeypatch.setattr(
        'bioenv.cli.subprocess.run',
        lambda *args, **kwargs: (_ for _ in ()).throw(FileNotFoundError('not found')),
    )
    assert main(['--config', str(catalog), 'config', 'edit', '--editor', 'not-installed']) == 2
    assert 'Cannot run editor' in capsys.readouterr().err


def test_config_edit_reports_nonzero_editor_status_without_validation(
    monkeypatch, tmp_path, capsys
):
    catalog = tmp_path / 'catalog.yaml'
    catalog.write_text(VALID_CATALOG, encoding='utf-8')
    monkeypatch.setattr(
        'bioenv.cli.subprocess.run', lambda *args, **kwargs: SimpleNamespace(returncode=7)
    )
    assert main(['--config', str(catalog), 'config', 'edit', '--editor', 'editor']) == 2
    assert 'exited with status 7' in capsys.readouterr().err


def test_config_edit_validates_updated_catalog(monkeypatch, tmp_path, capsys):
    catalog = tmp_path / 'catalog.yaml'
    catalog.write_text('invalid: [', encoding='utf-8')

    def run(command, check):
        catalog.write_text(VALID_CATALOG, encoding='utf-8')
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr('bioenv.cli.subprocess.run', run)
    assert main(['--config', str(catalog), 'config', 'edit', '--editor', 'editor']) == 0
    assert 'Catalog validation: OK (1 tools declared)' in capsys.readouterr().out


def test_config_edit_reports_invalid_catalog_after_editor(monkeypatch, tmp_path, capsys):
    catalog = tmp_path / 'catalog.yaml'
    catalog.write_text('invalid: [', encoding='utf-8')
    monkeypatch.setattr(
        'bioenv.cli.subprocess.run', lambda *args, **kwargs: SimpleNamespace(returncode=0)
    )
    assert main(['--config', str(catalog), 'config', 'edit', '--editor', 'editor']) == 1
    assert 'Catalog validation failed:\n' in capsys.readouterr().err


def test_config_edit_recommends_sudoedit_for_unwritable_catalog(
    monkeypatch, tmp_path, capsys
):
    catalog = tmp_path / 'catalog.yaml'
    catalog.write_text(VALID_CATALOG, encoding='utf-8')
    monkeypatch.setattr('bioenv.cli.os.access', lambda path, mode: False)
    called = []
    monkeypatch.setattr('bioenv.cli.subprocess.run', lambda *args, **kwargs: called.append(args))
    assert main(['--config', str(catalog), 'config', 'edit']) == 2
    error = capsys.readouterr().err
    assert f'sudoedit {catalog}' in error
    assert called == []
