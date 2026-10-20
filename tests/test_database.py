from pathlib import Path

import pytest
import yaml

from bioenv.cli import main
from bioenv.config import ConfigError, load_config
from bioenv.manager import BioenvError, check_database, import_database


def make_config(tmp_path, *, path='tool/db', kind='directory', required=True):
    config = {
        'settings': {
            'envs_root': str(tmp_path / 'envs'), 'bin_dir': str(tmp_path / 'bin'),
            'micromamba': str(tmp_path / 'micromamba'), 'mamba_root': str(tmp_path / 'mamba'),
            'databases_root': str(tmp_path / 'databases'),
        },
        'tools': {'tool': {
            'package': 'tool', 'commands': ['tool'],
            'databases': [{'name': 'db', 'path': path, 'type': kind, 'required': required,
                           'env': 'TOOL_DB'}],
        }},
    }
    config_path = tmp_path / 'tools.yaml'
    config_path.write_text(yaml.safe_dump(config), encoding='utf-8')
    return config_path, load_config(config_path)


def test_import_directory_copies_contents_and_preserves_origin(tmp_path):
    _, config = make_config(tmp_path)
    source = tmp_path / 'prepared'
    (source / 'nested').mkdir(parents=True)
    (source / 'nested' / 'data').write_text('database')
    import_database(config, 'tool', 'db', source=source)
    destination = tmp_path / 'databases' / 'tool' / 'db'
    assert (destination / 'nested' / 'data').read_text() == 'database'
    assert (source / 'nested' / 'data').read_text() == 'database'
    assert check_database(config, 'tool', 'db')[0]


def test_import_file_copies_file_to_declared_destination(tmp_path):
    _, config = make_config(tmp_path, path='tool/db.bin', kind='file')
    source = tmp_path / 'prepared.bin'
    source.write_bytes(b'raw database')
    import_database(config, 'tool', 'db', file=source)
    assert (tmp_path / 'databases' / 'tool' / 'db.bin').read_bytes() == b'raw database'
    assert source.read_bytes() == b'raw database'
    assert check_database(config, 'tool', 'db')[0]


@pytest.mark.parametrize('source_kind', ['missing', 'file'])
def test_source_option_requires_existing_directory(tmp_path, source_kind):
    _, config = make_config(tmp_path)
    source = tmp_path / 'source'
    if source_kind == 'file':
        source.touch()
    elif source_kind == 'directory':
        source.mkdir()
    with pytest.raises(BioenvError, match='does not exist|must be an accessible directory'):
        import_database(config, 'tool', 'db', source=source)


def test_file_option_requires_configured_file_database(tmp_path):
    _, config = make_config(tmp_path)
    source = tmp_path / 'source'
    source.touch()
    with pytest.raises(BioenvError, match='configured as a directory'):
        import_database(config, 'tool', 'db', file=source)


def test_file_import_requires_existing_file(tmp_path):
    _, config = make_config(tmp_path, kind='file')
    with pytest.raises(BioenvError, match='does not exist'):
        import_database(config, 'tool', 'db', file=tmp_path / 'missing')
    directory = tmp_path / 'directory'
    directory.mkdir()
    with pytest.raises(BioenvError, match='accessible file'):
        import_database(config, 'tool', 'db', file=directory)


def test_import_rejects_existing_destination(tmp_path):
    _, config = make_config(tmp_path)
    source = tmp_path / 'source'
    source.mkdir()
    destination = tmp_path / 'databases' / 'tool' / 'db'
    destination.mkdir(parents=True)
    with pytest.raises(BioenvError, match='already exists'):
        import_database(config, 'tool', 'db', source=source)


def test_import_rejects_path_outside_root(tmp_path):
    _, config = make_config(tmp_path, path=str(tmp_path / 'outside' / 'db'))
    source = tmp_path / 'source'
    source.mkdir()
    with pytest.raises(BioenvError, match='must stay within settings.databases_root'):
        import_database(config, 'tool', 'db', source=source)


def test_import_rejects_destination_symlink_escape(tmp_path):
    _, config = make_config(tmp_path)
    root = Path(config['settings']['databases_root'])
    root.mkdir()
    outside = tmp_path / 'outside'
    outside.mkdir()
    (root / 'tool').symlink_to(outside, target_is_directory=True)
    source = tmp_path / 'source'
    source.mkdir()
    with pytest.raises(BioenvError, match='escapes settings.databases_root'):
        import_database(config, 'tool', 'db', source=source)


def test_import_rejects_inaccessible_source(tmp_path, monkeypatch):
    _, config = make_config(tmp_path)
    source = tmp_path / 'source'
    source.mkdir()
    monkeypatch.setattr('bioenv.manager.os.access', lambda *args: False)
    with pytest.raises(BioenvError, match='not accessible'):
        import_database(config, 'tool', 'db', source=source)


def test_import_rejects_symlinks_in_source_tree(tmp_path):
    _, config = make_config(tmp_path)
    source = tmp_path / 'source'
    source.mkdir()
    outside = tmp_path / 'outside'
    outside.write_text('outside')
    (source / 'link').symlink_to(outside)
    with pytest.raises(BioenvError, match='Symbolic links are not allowed'):
        import_database(config, 'tool', 'db', source=source)
    assert not (tmp_path / 'databases' / 'tool' / 'db').exists()


def test_import_rejects_source_root_symlink(tmp_path):
    _, config = make_config(tmp_path)
    source = tmp_path / 'source'
    source.mkdir()
    link = tmp_path / 'link'
    link.symlink_to(source, target_is_directory=True)
    with pytest.raises(BioenvError, match='Symbolic links are not allowed'):
        import_database(config, 'tool', 'db', source=link)


def test_import_rejects_source_inside_its_destination(tmp_path):
    _, config = make_config(tmp_path)
    destination = tmp_path / 'databases' / 'tool' / 'db'
    source = destination / 'prepared'
    source.mkdir(parents=True)
    with pytest.raises(BioenvError, match='into itself'):
        import_database(config, 'tool', 'db', source=source)


def test_import_dry_run_has_no_side_effects(tmp_path, capsys):
    config_path, config = make_config(tmp_path)
    source = tmp_path / 'prepared'
    source.mkdir()
    before = set(tmp_path.rglob('*'))
    assert main(['--config', str(config_path), 'db', 'import', 'tool', 'db',
                 '--source', str(source), '--dry-run']) == 0
    output = capsys.readouterr().out
    assert f'Source: {source}' in output
    assert 'Destination:' in output
    assert set(tmp_path.rglob('*')) == before


def test_copy_failure_does_not_leave_destination(tmp_path, monkeypatch):
    _, config = make_config(tmp_path)
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'data').write_text('data')

    def fail_copy(*args, **kwargs):
        staged = args[1]
        (Path(staged) / 'partial').write_text('partial')
        raise OSError('copy failed')

    monkeypatch.setattr('bioenv.manager.shutil.copytree', fail_copy)
    with pytest.raises(BioenvError, match='Could not import database'):
        import_database(config, 'tool', 'db', source=source)
    assert not (tmp_path / 'databases' / 'tool' / 'db').exists()
    assert (source / 'data').read_text() == 'data'


def test_check_distinguishes_directory_and_file_and_optional_required(tmp_path):
    _, config = make_config(tmp_path)
    ok, label = check_database(config, 'tool', 'db')
    assert not ok and 'not installed' in label
    config['tools']['tool']['databases'][0]['required'] = False
    assert check_database(config, 'tool', 'db')[0]
    (tmp_path / 'databases' / 'tool' / 'db').mkdir(parents=True)
    assert check_database(config, 'tool', 'db')[0]
    config['tools']['tool']['databases'][0]['type'] = 'file'
    assert not check_database(config, 'tool', 'db')[0]


def test_database_catalog_rejects_install_and_invalid_type(tmp_path):
    config_path, _ = make_config(tmp_path)
    raw = yaml.safe_load(config_path.read_text(encoding='utf-8'))
    raw['tools']['tool']['databases'][0]['install'] = ['installer']
    config_path.write_text(yaml.safe_dump(raw), encoding='utf-8')
    with pytest.raises(ConfigError, match='Unknown Tool.*setting'):
        load_config(config_path)
    raw['tools']['tool']['databases'][0].pop('install')
    raw['tools']['tool']['databases'][0]['type'] = 'archive'
    config_path.write_text(yaml.safe_dump(raw), encoding='utf-8')
    with pytest.raises(ConfigError, match='type must be directory or file'):
        load_config(config_path)
