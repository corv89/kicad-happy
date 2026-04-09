"""Tests for analysis_cache.py"""

import json
import os
import shutil
import tempfile
import unittest


class TestManifestOperations(unittest.TestCase):
    """Tests for manifest create/read/update."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.analysis_dir = os.path.join(self.tmpdir, 'analysis')

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_ensure_analysis_dir_creates_directory(self):
        from analysis_cache import ensure_analysis_dir
        result = ensure_analysis_dir(self.tmpdir, project_file='test.kicad_pro')
        self.assertTrue(os.path.isdir(result))
        self.assertTrue(os.path.isfile(os.path.join(result, 'manifest.json')))

    def test_ensure_analysis_dir_creates_gitignore_by_default(self):
        from analysis_cache import ensure_analysis_dir
        result = ensure_analysis_dir(self.tmpdir, project_file='test.kicad_pro')
        gitignore_path = os.path.join(result, '.gitignore')
        self.assertTrue(os.path.isfile(gitignore_path))
        with open(gitignore_path) as f:
            contents = f.read()
        self.assertIn('!manifest.json', contents)
        self.assertIn('!.gitignore', contents)

    def test_ensure_analysis_dir_no_gitignore_when_track_in_git(self):
        from analysis_cache import ensure_analysis_dir
        result = ensure_analysis_dir(
            self.tmpdir, project_file='test.kicad_pro',
            config={'analysis': {'track_in_git': True}})
        gitignore_path = os.path.join(result, '.gitignore')
        self.assertFalse(os.path.isfile(gitignore_path))

    def test_fresh_manifest_structure(self):
        from analysis_cache import ensure_analysis_dir, load_manifest
        ensure_analysis_dir(self.tmpdir, project_file='test.kicad_pro')
        manifest = load_manifest(self.analysis_dir)
        self.assertEqual(manifest['version'], 1)
        self.assertEqual(manifest['project'], 'test.kicad_pro')
        self.assertIsNone(manifest['current'])
        self.assertEqual(manifest['runs'], {})

    def test_save_and_load_manifest_roundtrip(self):
        from analysis_cache import ensure_analysis_dir, load_manifest, save_manifest
        ensure_analysis_dir(self.tmpdir, project_file='test.kicad_pro')
        manifest = load_manifest(self.analysis_dir)
        manifest['current'] = '2026-04-08_1919'
        manifest['runs']['2026-04-08_1919'] = {
            'source_hashes': {},
            'outputs': {},
            'scripts': {},
            'generated': '2026-04-08T19:19:00Z',
            'pinned': False,
        }
        save_manifest(self.analysis_dir, manifest)
        reloaded = load_manifest(self.analysis_dir)
        self.assertEqual(reloaded['current'], '2026-04-08_1919')
        self.assertIn('2026-04-08_1919', reloaded['runs'])

    def test_ensure_analysis_dir_idempotent(self):
        from analysis_cache import ensure_analysis_dir, load_manifest, save_manifest
        ensure_analysis_dir(self.tmpdir, project_file='test.kicad_pro')
        manifest = load_manifest(self.analysis_dir)
        manifest['current'] = 'existing_run'
        save_manifest(self.analysis_dir, manifest)
        # Call again -- should not overwrite existing manifest
        ensure_analysis_dir(self.tmpdir, project_file='test.kicad_pro')
        reloaded = load_manifest(self.analysis_dir)
        self.assertEqual(reloaded['current'], 'existing_run')


class TestHashing(unittest.TestCase):
    """Tests for source file hashing."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_hash_source_file(self):
        from analysis_cache import hash_source_file
        path = os.path.join(self.tmpdir, 'test.txt')
        with open(path, 'w') as f:
            f.write('hello world')
        h = hash_source_file(path)
        self.assertTrue(h.startswith('sha256:'))
        self.assertEqual(len(h), 7 + 64)  # "sha256:" + 64 hex chars

    def test_hash_source_file_deterministic(self):
        from analysis_cache import hash_source_file
        path = os.path.join(self.tmpdir, 'test.txt')
        with open(path, 'w') as f:
            f.write('hello world')
        self.assertEqual(hash_source_file(path), hash_source_file(path))

    def test_hash_source_file_missing(self):
        from analysis_cache import hash_source_file
        h = hash_source_file('/nonexistent/file.txt')
        self.assertIsNone(h)

    def test_sources_changed_detects_modification(self):
        from analysis_cache import hash_source_file, sources_changed
        path = os.path.join(self.tmpdir, 'test.kicad_sch')
        with open(path, 'w') as f:
            f.write('version 1')
        old_hashes = {'test.kicad_sch': hash_source_file(path)}
        with open(path, 'w') as f:
            f.write('version 2')
        self.assertTrue(sources_changed(old_hashes, self.tmpdir))

    def test_sources_changed_no_change(self):
        from analysis_cache import hash_source_file, sources_changed
        path = os.path.join(self.tmpdir, 'test.kicad_sch')
        with open(path, 'w') as f:
            f.write('version 1')
        old_hashes = {'test.kicad_sch': hash_source_file(path)}
        self.assertFalse(sources_changed(old_hashes, self.tmpdir))


class TestRunCreation(unittest.TestCase):
    """Tests for creating timestamped run folders."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.project_dir = self.tmpdir
        # Create a fake .kicad_pro
        with open(os.path.join(self.project_dir, 'test.kicad_pro'), 'w') as f:
            f.write('{}')

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_generate_run_id_format(self):
        from analysis_cache import generate_run_id
        run_id = generate_run_id()
        # Format: YYYY-MM-DD_HHMM
        self.assertRegex(run_id, r'^\d{4}-\d{2}-\d{2}_\d{4}$')

    def test_generate_run_id_dedup(self):
        from analysis_cache import generate_run_id, ensure_analysis_dir
        analysis_dir = ensure_analysis_dir(self.project_dir,
                                           project_file='test.kicad_pro')
        run_id = generate_run_id()
        # Create a folder with that ID to force dedup
        os.makedirs(os.path.join(analysis_dir, run_id))
        deduped = generate_run_id(analysis_dir)
        self.assertNotEqual(run_id, deduped)
        self.assertTrue(deduped.startswith(run_id[:10]))  # same date prefix

    def test_create_run_creates_folder_and_updates_manifest(self):
        from analysis_cache import (ensure_analysis_dir, load_manifest,
                                    create_run)
        analysis_dir = ensure_analysis_dir(self.project_dir,
                                           project_file='test.kicad_pro')
        # Create a fake output file
        tmp_outputs = os.path.join(self.tmpdir, 'tmp_outputs')
        os.makedirs(tmp_outputs)
        with open(os.path.join(tmp_outputs, 'schematic.json'), 'w') as f:
            json.dump({'test': True}, f)

        run_id = create_run(
            analysis_dir=analysis_dir,
            outputs_dir=tmp_outputs,
            source_hashes={'test.kicad_sch': 'sha256:abc123'},
            scripts={'schematic': 'analyze_schematic.py'},
        )

        # Folder exists
        self.assertTrue(os.path.isdir(os.path.join(analysis_dir, run_id)))
        # Output copied
        self.assertTrue(os.path.isfile(
            os.path.join(analysis_dir, run_id, 'schematic.json')))
        # Manifest updated
        manifest = load_manifest(analysis_dir)
        self.assertEqual(manifest['current'], run_id)
        self.assertIn(run_id, manifest['runs'])
        self.assertEqual(
            manifest['runs'][run_id]['source_hashes']['test.kicad_sch'],
            'sha256:abc123')

    def test_create_run_copies_forward_from_previous(self):
        from analysis_cache import (ensure_analysis_dir, load_manifest,
                                    save_manifest, create_run)
        analysis_dir = ensure_analysis_dir(self.project_dir,
                                           project_file='test.kicad_pro')

        # Create a first run with pcb.json
        first_dir = os.path.join(analysis_dir, '2026-04-01_1000')
        os.makedirs(first_dir)
        with open(os.path.join(first_dir, 'pcb.json'), 'w') as f:
            json.dump({'pcb': True}, f)
        manifest = load_manifest(analysis_dir)
        manifest['current'] = '2026-04-01_1000'
        manifest['runs']['2026-04-01_1000'] = {
            'source_hashes': {}, 'outputs': {'pcb': 'pcb.json'},
            'scripts': {}, 'generated': '2026-04-01T10:00:00Z',
            'pinned': False,
        }
        save_manifest(analysis_dir, manifest)

        # Create a second run with only schematic.json
        tmp_outputs = os.path.join(self.tmpdir, 'new_outputs')
        os.makedirs(tmp_outputs)
        with open(os.path.join(tmp_outputs, 'schematic.json'), 'w') as f:
            json.dump({'schematic': True}, f)

        run_id = create_run(
            analysis_dir=analysis_dir,
            outputs_dir=tmp_outputs,
            source_hashes={'test.kicad_sch': 'sha256:xyz'},
            scripts={'schematic': 'analyze_schematic.py'},
        )

        run_dir = os.path.join(analysis_dir, run_id)
        # New output present
        self.assertTrue(os.path.isfile(os.path.join(run_dir, 'schematic.json')))
        # Previous output copied forward
        self.assertTrue(os.path.isfile(os.path.join(run_dir, 'pcb.json')))
        # Manifest outputs include both
        manifest = load_manifest(analysis_dir)
        self.assertIn('schematic', manifest['runs'][run_id]['outputs'])
        self.assertIn('pcb', manifest['runs'][run_id]['outputs'])

    def test_overwrite_current_updates_in_place(self):
        from analysis_cache import (ensure_analysis_dir, load_manifest,
                                    save_manifest, overwrite_current)
        analysis_dir = ensure_analysis_dir(self.project_dir,
                                           project_file='test.kicad_pro')

        # Create initial run
        run_dir = os.path.join(analysis_dir, '2026-04-01_1000')
        os.makedirs(run_dir)
        with open(os.path.join(run_dir, 'schematic.json'), 'w') as f:
            json.dump({'version': 1}, f)
        manifest = load_manifest(analysis_dir)
        manifest['current'] = '2026-04-01_1000'
        manifest['runs']['2026-04-01_1000'] = {
            'source_hashes': {'a.kicad_sch': 'sha256:old'},
            'outputs': {'schematic': 'schematic.json'},
            'scripts': {}, 'generated': '2026-04-01T10:00:00Z',
            'pinned': False,
        }
        save_manifest(analysis_dir, manifest)

        # Overwrite with new outputs and hashes
        tmp_outputs = os.path.join(self.tmpdir, 'new_outputs')
        os.makedirs(tmp_outputs)
        with open(os.path.join(tmp_outputs, 'schematic.json'), 'w') as f:
            json.dump({'version': 2}, f)

        overwrite_current(
            analysis_dir=analysis_dir,
            outputs_dir=tmp_outputs,
            source_hashes={'a.kicad_sch': 'sha256:new'},
        )

        # Same folder name, updated contents
        manifest = load_manifest(analysis_dir)
        self.assertEqual(manifest['current'], '2026-04-01_1000')
        self.assertEqual(
            manifest['runs']['2026-04-01_1000']['source_hashes']['a.kicad_sch'],
            'sha256:new')
        with open(os.path.join(run_dir, 'schematic.json')) as f:
            data = json.load(f)
        self.assertEqual(data['version'], 2)


class TestRetention(unittest.TestCase):
    """Tests for retention pruning and pinning."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.project_dir = self.tmpdir

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def _setup_runs(self, analysis_dir, count, pinned_indices=None):
        """Create N run folders with manifest entries."""
        from analysis_cache import load_manifest, save_manifest
        if pinned_indices is None:
            pinned_indices = set()
        manifest = load_manifest(analysis_dir)
        for i in range(count):
            run_id = f'2026-04-{i+1:02d}_1000'
            run_dir = os.path.join(analysis_dir, run_id)
            os.makedirs(run_dir, exist_ok=True)
            with open(os.path.join(run_dir, 'schematic.json'), 'w') as f:
                json.dump({'run': i}, f)
            manifest['runs'][run_id] = {
                'source_hashes': {}, 'outputs': {'schematic': 'schematic.json'},
                'scripts': {}, 'generated': f'2026-04-{i+1:02d}T10:00:00Z',
                'pinned': i in pinned_indices,
            }
            manifest['current'] = run_id
        save_manifest(analysis_dir, manifest)
        return manifest

    def test_prune_removes_oldest_unpinned(self):
        from analysis_cache import ensure_analysis_dir, prune_runs, load_manifest
        analysis_dir = ensure_analysis_dir(self.project_dir,
                                           project_file='test.kicad_pro')
        self._setup_runs(analysis_dir, 7)
        prune_runs(analysis_dir, retention=5)
        manifest = load_manifest(analysis_dir)
        self.assertEqual(len(manifest['runs']), 5)
        # Oldest two should be gone
        self.assertNotIn('2026-04-01_1000', manifest['runs'])
        self.assertNotIn('2026-04-02_1000', manifest['runs'])
        # Folders should be deleted
        self.assertFalse(os.path.isdir(
            os.path.join(analysis_dir, '2026-04-01_1000')))

    def test_prune_preserves_pinned(self):
        from analysis_cache import ensure_analysis_dir, prune_runs, load_manifest
        analysis_dir = ensure_analysis_dir(self.project_dir,
                                           project_file='test.kicad_pro')
        # Pin runs 0 and 1 (the oldest)
        self._setup_runs(analysis_dir, 7, pinned_indices={0, 1})
        prune_runs(analysis_dir, retention=3)
        manifest = load_manifest(analysis_dir)
        # 2 pinned + 3 retained = 5 total
        self.assertEqual(len(manifest['runs']), 5)
        # Pinned runs survive
        self.assertIn('2026-04-01_1000', manifest['runs'])
        self.assertIn('2026-04-02_1000', manifest['runs'])

    def test_prune_does_nothing_under_limit(self):
        from analysis_cache import ensure_analysis_dir, prune_runs, load_manifest
        analysis_dir = ensure_analysis_dir(self.project_dir,
                                           project_file='test.kicad_pro')
        self._setup_runs(analysis_dir, 3)
        prune_runs(analysis_dir, retention=5)
        manifest = load_manifest(analysis_dir)
        self.assertEqual(len(manifest['runs']), 3)

    def test_prune_zero_retention_keeps_all(self):
        from analysis_cache import ensure_analysis_dir, prune_runs, load_manifest
        analysis_dir = ensure_analysis_dir(self.project_dir,
                                           project_file='test.kicad_pro')
        self._setup_runs(analysis_dir, 10)
        prune_runs(analysis_dir, retention=0)
        manifest = load_manifest(analysis_dir)
        self.assertEqual(len(manifest['runs']), 10)

    def test_pin_and_unpin_run(self):
        from analysis_cache import (ensure_analysis_dir, load_manifest,
                                    pin_run, unpin_run)
        analysis_dir = ensure_analysis_dir(self.project_dir,
                                           project_file='test.kicad_pro')
        self._setup_runs(analysis_dir, 3)
        pin_run(analysis_dir, '2026-04-01_1000')
        manifest = load_manifest(analysis_dir)
        self.assertTrue(manifest['runs']['2026-04-01_1000']['pinned'])
        unpin_run(analysis_dir, '2026-04-01_1000')
        manifest = load_manifest(analysis_dir)
        self.assertFalse(manifest['runs']['2026-04-01_1000']['pinned'])

    def test_prune_never_removes_current(self):
        from analysis_cache import ensure_analysis_dir, prune_runs, load_manifest
        analysis_dir = ensure_analysis_dir(self.project_dir,
                                           project_file='test.kicad_pro')
        self._setup_runs(analysis_dir, 7)
        prune_runs(analysis_dir, retention=1)
        manifest = load_manifest(analysis_dir)
        # Current run always preserved
        self.assertIn(manifest['current'], manifest['runs'])


class TestNewRunDecision(unittest.TestCase):
    """Tests for should_create_new_run -- diff-based decision."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.project_dir = self.tmpdir

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_should_create_new_run_no_current(self):
        from analysis_cache import ensure_analysis_dir, should_create_new_run
        analysis_dir = ensure_analysis_dir(self.project_dir,
                                           project_file='test.kicad_pro')
        # No current run -- always create
        result = should_create_new_run(analysis_dir, self.tmpdir)
        self.assertTrue(result)

    def test_should_create_new_run_with_identical_outputs(self):
        from analysis_cache import (ensure_analysis_dir, load_manifest,
                                    save_manifest, should_create_new_run)
        analysis_dir = ensure_analysis_dir(self.project_dir,
                                           project_file='test.kicad_pro')

        # Create a current run with a schematic.json
        current_dir = os.path.join(analysis_dir, '2026-04-01_1000')
        os.makedirs(current_dir)
        data = {'statistics': {'total_components': 52}, 'components': []}
        with open(os.path.join(current_dir, 'schematic.json'), 'w') as f:
            json.dump(data, f)

        manifest = load_manifest(analysis_dir)
        manifest['current'] = '2026-04-01_1000'
        manifest['runs']['2026-04-01_1000'] = {
            'source_hashes': {}, 'outputs': {'schematic': 'schematic.json'},
            'scripts': {}, 'generated': '2026-04-01T10:00:00Z',
            'pinned': False,
        }
        save_manifest(analysis_dir, manifest)

        # New outputs with identical data
        new_dir = os.path.join(self.tmpdir, 'new')
        os.makedirs(new_dir)
        with open(os.path.join(new_dir, 'schematic.json'), 'w') as f:
            json.dump(data, f)

        result = should_create_new_run(analysis_dir, new_dir,
                                       diff_threshold='major')
        self.assertFalse(result)

    def test_get_current_run_returns_metadata(self):
        from analysis_cache import (ensure_analysis_dir, load_manifest,
                                    save_manifest, get_current_run)
        analysis_dir = ensure_analysis_dir(self.project_dir,
                                           project_file='test.kicad_pro')
        run_dir = os.path.join(analysis_dir, '2026-04-01_1000')
        os.makedirs(run_dir)
        manifest = load_manifest(analysis_dir)
        manifest['current'] = '2026-04-01_1000'
        manifest['runs']['2026-04-01_1000'] = {
            'source_hashes': {'a.kicad_sch': 'sha256:abc'},
            'outputs': {'schematic': 'schematic.json'},
            'scripts': {}, 'generated': '2026-04-01T10:00:00Z',
            'pinned': False,
        }
        save_manifest(analysis_dir, manifest)

        run_path, run_meta = get_current_run(analysis_dir)
        self.assertEqual(run_path, run_dir)
        self.assertEqual(run_meta['source_hashes']['a.kicad_sch'], 'sha256:abc')

    def test_get_current_run_returns_none_when_empty(self):
        from analysis_cache import ensure_analysis_dir, get_current_run
        analysis_dir = ensure_analysis_dir(self.project_dir,
                                           project_file='test.kicad_pro')
        result = get_current_run(analysis_dir)
        self.assertIsNone(result)


if __name__ == '__main__':
    unittest.main()
