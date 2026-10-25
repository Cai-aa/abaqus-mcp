"""Viewport regressions using a fake Abaqus API; no Abaqus license required."""
import base64
import contextlib
import importlib.util
import io
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]


def load_module(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ViewportImageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Loading the plugin writes status/log files: never touch the user's IPC home.
        cls.home = tempfile.TemporaryDirectory()
        with patch.dict(os.environ, {'ABAQUS_MCP_HOME': cls.home.name}):
            with contextlib.redirect_stdout(io.StringIO()):
                cls.plugin = load_module('viewport_test_plugin', 'abaqus_mcp_plugin.py')
            cls.server = load_module('viewport_test_server', 'mcp_server.py')

    @classmethod
    def tearDownClass(cls):
        cls.home.cleanup()

    def setUp(self):
        self.constants = types.ModuleType('abaqusConstants')
        self.constants.PNG = object()
        self.constants.SVG = object()
        self.constants.TIFF = object()
        self.viewport = object()
        # Deliberately no PNG/SVG/TIFF attributes on session (issue #5).
        self.session = types.SimpleNamespace(
            currentViewportName='Viewport: 1',
            viewports={'Viewport: 1': self.viewport, 'Other': object()},
            printToFile=Mock(side_effect=self.write_image),
        )
        abaqus = types.ModuleType('abaqus')
        abaqus.session = self.session
        self.modules = patch.dict(sys.modules, {
            'abaqus': abaqus, 'abaqusConstants': self.constants,
        })
        self.modules.start()
        self.addCleanup(self.modules.stop)
        self.payload = b'fake image bytes\x00\xff'
        self.tiff_extension = 'tif'

    def write_image(self, fileName, format, canvasObjects):
        ext = {self.constants.PNG: 'png', self.constants.SVG: 'svg',
               self.constants.TIFF: self.tiff_extension}[format]
        Path(fileName + '.' + ext).write_bytes(self.payload)

    def assert_no_images(self):
        self.assertEqual(list(Path(self.plugin.SCREENSHOTS_DIR).iterdir()), [])

    def test_formats_and_tiff_alias(self):
        for fmt, canonical in [('PNG', 'png'), ('svg', 'svg'), ('TIFF', 'tiff'),
                               ('TIF', 'tiff'), (None, 'png')]:
            with self.subTest(fmt=fmt):
                result = self.plugin.get_viewport_image(fmt=fmt)
                self.assertTrue(result['success'], result)
                self.assertEqual(base64.b64decode(result['image_base64']), self.payload)
                self.assertEqual(result['format'], canonical)
                args = self.session.printToFile.call_args.kwargs
                self.assertIs(args['format'], getattr(self.constants, canonical.upper()))
                self.assertEqual(args['canvasObjects'], (self.viewport,))
                self.assertEqual(Path(args['fileName']).suffix, '')
                self.assert_no_images()

    def test_tiff_long_extension(self):
        self.tiff_extension = 'tiff'
        self.assertTrue(self.plugin.get_viewport_image(fmt='TIFF')['success'])
        self.assert_no_images()

    def test_named_viewport(self):
        self.assertTrue(self.plugin.get_viewport_image('Other')['success'])
        self.assertEqual(self.session.printToFile.call_args.kwargs['canvasObjects'],
                         (self.session.viewports['Other'],))

    def test_unknown_viewport(self):
        result = self.plugin.get_viewport_image('Missing')
        self.assertEqual(result, {'success': False, 'error': 'Viewport not found: Missing'})
        self.session.printToFile.assert_not_called()

    def test_unsupported_format(self):
        result = self.plugin.get_viewport_image(fmt='JPEG')
        self.assertEqual(result, {'success': False, 'error': 'Unsupported image format: JPEG'})
        self.session.printToFile.assert_not_called()

    def test_missing_output(self):
        self.session.printToFile.side_effect = None
        self.assertEqual(self.plugin.get_viewport_image(),
                         {'success': False, 'error': 'Image file not created'})

    def test_export_exception_cleans_partial_file(self):
        def fail(**kwargs):
            self.write_image(**kwargs)
            raise RuntimeError('Export failed')
        self.session.printToFile.side_effect = fail
        self.assertEqual(self.plugin.get_viewport_image(),
                         {'success': False, 'error': 'Export failed'})
        self.assert_no_images()

    def test_read_exception_cleans_output(self):
        real_open = open

        def fail_read(path, mode='r', *args, **kwargs):
            if mode == 'rb':
                raise OSError('Read failed')
            return real_open(path, mode, *args, **kwargs)

        with patch('builtins.open', side_effect=fail_read):
            self.assertEqual(self.plugin.get_viewport_image(),
                             {'success': False, 'error': 'Read failed'})
        self.assert_no_images()

    def test_unique_export_names(self):
        self.plugin.get_viewport_image()
        first = self.session.printToFile.call_args.kwargs['fileName']
        self.plugin.get_viewport_image()
        self.assertNotEqual(first, self.session.printToFile.call_args.kwargs['fileName'])

    def test_server_data_uris(self):
        for fmt, mime in [('png', 'image/png'), ('svg', 'image/svg+xml'),
                          ('tiff', 'image/tiff')]:
            with self.subTest(fmt=fmt):
                response = {'success': True, 'data': {
                    'success': True, 'format': fmt, 'image_base64': 'YWJj'}}
                with patch.object(self.server, '_send_command', return_value=response) as send:
                    self.assertEqual(self.server.get_viewport_image('Other', fmt),
                                     'data:' + mime + ';base64,YWJj')
                    send.assert_called_once_with('get_viewport_image', timeout=30.0,
                                                 viewport_name='Other', format=fmt.upper())

    def test_server_error_shapes(self):
        cases = [
            ({'success': False, 'data': {'error': 'Export failed'}}, 'Export failed'),
            ({'success': False, 'error': 'Timeout'}, 'Timeout'),
            ({'success': False, 'error': 'Outer', 'data': {'error': 'Inner'}}, 'Outer'),
            ({'success': False, 'data': None}, 'Unknown error'),
            ({'success': False}, 'Unknown error'),
            ({'success': True, 'data': {'success': False, 'error': 'Nested'}}, 'Nested'),
        ]
        for response, message in cases:
            with self.subTest(response=response):
                with patch.object(self.server, '_send_command', return_value=response):
                    self.assertEqual(self.server.get_viewport_image(), 'Error: ' + message)

    def test_dispatcher_to_server_success(self):
        def dispatch(cmd_type, timeout, **kwargs):
            return self.plugin.process_command({'id': 'test', 'type': cmd_type, **kwargs})
        with patch.object(self.server, '_send_command', side_effect=dispatch):
            result = self.server.get_viewport_image(image_format='SVG')
        self.assertEqual(result, 'data:image/svg+xml;base64,' +
                         base64.b64encode(self.payload).decode('ascii'))
        self.assert_no_images()

    def test_dispatcher_to_server_error(self):
        self.session.printToFile.side_effect = RuntimeError('Real export error')

        def dispatch(cmd_type, timeout, **kwargs):
            return self.plugin.process_command({'id': 'test', 'type': cmd_type, **kwargs})
        with patch.object(self.server, '_send_command', side_effect=dispatch):
            self.assertEqual(self.server.get_viewport_image(), 'Error: Real export error')


if __name__ == '__main__':
    unittest.main()
