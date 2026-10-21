import shutil
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
import yaml

from bioenv.cli import main
from bioenv.config import ConfigError, load_config
from bioenv.manager import BioenvError, _copy_file_no_follow, check_database, import_database


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


def test_file_symlink_swap_during_import_is_not_followed(tmp_path, monkeypatch):
    _, config = make_config(tmp_path, kind='file')
    source = tmp_path / 'source.bin'
    source.write_text('prepared')
    outside = tmp_path / 'outside.bin'
    outside.write_text('outside')
    real_copy = _copy_file_no_follow

    def swap_for_symlink(origin, staged):
        Path(origin).unlink()
        Path(origin).symlink_to(outside)
        return real_copy(origin, staged)

    monkeypatch.setattr('bioenv.manager._copy_file_no_follow', swap_for_symlink)
    with pytest.raises(BioenvError, match='Could not import database'):
        import_database(config, 'tool', 'db', file=source)
    assert not (Path(config['settings']['databases_root']) / 'tool' / 'db').exists()
    assert outside.read_text() == 'outside'


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


def test_import_preserves_relative_internal_symlink(tmp_path):
    _, config = make_config(tmp_path)
    source = tmp_path / 'source'
    (source / 'versions' / 'v1').mkdir(parents=True)
    (source / 'versions' / 'v1' / 'db').write_text('database')
    (source / 'latest').symlink_to(Path('versions/v1'), target_is_directory=True)
    import_database(config, 'tool', 'db', source=source)
    imported = tmp_path / 'databases' / 'tool' / 'db'
    assert (imported / 'latest').is_symlink()
    assert imported.joinpath('latest').readlink() == Path('versions/v1')
    assert (imported / 'latest' / 'db').read_text() == 'database'
    assert (source / 'latest').is_symlink()


def test_import_rejects_external_symlink(tmp_path):
    _, config = make_config(tmp_path)
    source = tmp_path / 'source'
    source.mkdir()
    outside = tmp_path / 'outside'
    outside.write_text('outside')
    (source / 'link').symlink_to(Path('../outside'))
    with pytest.raises(BioenvError, match='outside the import source'):
        import_database(config, 'tool', 'db', source=source)
    assert not (tmp_path / 'databases' / 'tool' / 'db').exists()


def test_import_rejects_broken_symlink(tmp_path):
    _, config = make_config(tmp_path)
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'broken').symlink_to(Path('missing'))
    with pytest.raises(BioenvError, match='Broken or cyclic'):
        import_database(config, 'tool', 'db', source=source)


def test_import_rejects_cyclic_symlinks(tmp_path):
    _, config = make_config(tmp_path)
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'first').symlink_to(Path('second'))
    (source / 'second').symlink_to(Path('first'))
    with pytest.raises(BioenvError, match='Broken or cyclic'):
        import_database(config, 'tool', 'db', source=source)


def test_import_rejects_source_root_symlink(tmp_path):
    _, config = make_config(tmp_path)
    source = tmp_path / 'source'
    source.mkdir()
    link = tmp_path / 'link'
    link.symlink_to(source, target_is_directory=True)
    with pytest.raises(BioenvError, match='Symbolic links are not allowed as import sources'):
        import_database(config, 'tool', 'db', source=link)


def test_import_rejects_source_inside_its_destination(tmp_path):
    _, config = make_config(tmp_path)
    destination = tmp_path / 'databases' / 'tool' / 'db'
    source = destination / 'prepared'
    source.mkdir(parents=True)
    with pytest.raises(BioenvError, match='into itself'):
        import_database(config, 'tool', 'db', source=source)


def test_import_dry_run_has_no_side_effects(tmp_path, capsys):
    config_path, _config = make_config(tmp_path)
    source = tmp_path / 'prepared'
    source.mkdir()
    before = set(tmp_path.rglob('*'))
    assert main(['--config', str(config_path), 'db', 'import', 'tool', 'db',
                 '--source', str(source), '--dry-run']) == 0
    output = capsys.readouterr().out
    assert f'Source: {source}' in output
    assert 'Destination:' in output
    assert set(tmp_path.rglob('*')) == before


@pytest.mark.parametrize('failure', [OSError('copy failed'), shutil.Error('copy failed')])
def test_copy_failure_does_not_leave_destination_or_change_source(tmp_path, monkeypatch, failure):
    _, config = make_config(tmp_path)
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'data').write_text('data')

    original_files = {path.relative_to(source): path.read_bytes()
                      for path in source.rglob('*') if path.is_file()}

    def fail_copy(*args, **kwargs):
        staged = args[1]
        Path(staged).mkdir()
        (Path(staged) / 'partial').write_text('partial')
        raise failure

    monkeypatch.setattr('bioenv.manager.shutil.copytree', fail_copy)
    with pytest.raises(BioenvError, match='Could not import database'):
        import_database(config, 'tool', 'db', source=source)
    assert not (tmp_path / 'databases' / 'tool' / 'db').exists()
    assert {path.relative_to(source): path.read_bytes()
            for path in source.rglob('*') if path.is_file()} == original_files


def test_concurrent_imports_publish_only_one_complete_database(tmp_path):
    _, config = make_config(tmp_path)
    sources = [tmp_path / 'source-a', tmp_path / 'source-b']
    for value, source in zip((b'a', b'b'), sources):
        source.mkdir()
        (source / 'payload').write_bytes(value)
    barrier = threading.Barrier(2)

    def run(source):
        barrier.wait()
        try:
            import_database(config, 'tool', 'db', source=source)
            return 'imported'
        except BioenvError:
            return 'occupied'

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(run, sources))
    assert sorted(results) == ['imported', 'occupied']
    destination = tmp_path / 'databases' / 'tool' / 'db'
    assert (destination / 'payload').read_bytes() in (b'a', b'b')
    assert [(source / 'payload').read_bytes() for source in sources] == [b'a', b'b']


@pytest.mark.parametrize('kind', ['directory', 'file'])
def test_publish_conflict_does_not_replace_concurrent_destination(tmp_path, monkeypatch, kind):
    is_file = kind == 'file'
    _, config = make_config(tmp_path, path='tool/db.bin' if is_file else 'tool/db', kind=kind)
    source = tmp_path / ('source.bin' if is_file else 'source')
    if is_file:
        source.write_text('prepared')
        real_copy = _copy_file_no_follow

        def occupy_after_copy(origin, staged, **kwargs):
            real_copy(origin, staged, **kwargs)
            Path(config['settings']['databases_root'], 'tool').mkdir(
                parents=True, exist_ok=True
            )
            (Path(config['settings']['databases_root']) / 'tool' / 'db.bin').write_text('external')

        monkeypatch.setattr('bioenv.manager._copy_file_no_follow', occupy_after_copy)
    else:
        source.mkdir()
        (source / 'payload').write_text('prepared')
        real_copytree = shutil.copytree

        def occupy_after_copytree(origin, staged, **kwargs):
            result = real_copytree(origin, staged, **kwargs)
            (Path(config['settings']['databases_root']) / 'tool' / 'db').mkdir()
            return result

        monkeypatch.setattr('bioenv.manager.shutil.copytree', occupy_after_copytree)

    with pytest.raises(BioenvError, match='already exists'):
        import_database(config, 'tool', 'db', **({'file': source} if is_file else {'source': source}))
    destination = Path(config['settings']['databases_root']) / 'tool' / ('db.bin' if is_file else 'db')
    if is_file:
        assert destination.read_text() == 'external'
        assert source.read_text() == 'prepared'
    else:
        assert destination.is_dir() and list(destination.iterdir()) == []
        assert (source / 'payload').read_text() == 'prepared'


def test_import_rejects_symlink_added_while_copying(tmp_path, monkeypatch):
    _, config = make_config(tmp_path)
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'data').write_text('prepared')
    outside = tmp_path / 'outside'
    outside.write_text('outside')
    real_copytree = shutil.copytree

    def add_link_then_copy(origin, staged, **kwargs):
        (Path(origin) / 'late-link').symlink_to(Path('../outside'))
        return real_copytree(origin, staged, **kwargs)

    monkeypatch.setattr('bioenv.manager.shutil.copytree', add_link_then_copy)
    with pytest.raises(BioenvError, match='Broken or cyclic'):
        import_database(config, 'tool', 'db', source=source)
    assert not (Path(config['settings']['databases_root']) / 'tool' / 'db').exists()
    assert (source / 'data').read_text() == 'prepared'
    assert (source / 'late-link').is_symlink()


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
