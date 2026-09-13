from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from wsl_media_downloader.config import Config, ConfigError, default_config_path, load_config, parse_env
from wsl_media_downloader.core import Mode, Task
from wsl_media_downloader.runner import WslRunner, download_args, parse_progress


class ConfigTests(unittest.TestCase):
    def test_missing_configuration_is_not_a_personal_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ConfigError):
                load_config(Path(tmp) / '.env', environ={})

    def test_example_must_be_replaced(self):
        example = Path(__file__).resolve().parent.parent / '.env.example'
        with self.assertRaises(ConfigError):
            load_config(example, environ={})

    def test_file_quotes_and_environment_precedence(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / '.env'
            path.write_text('# private\nWSL_USER=testuser\nWSL_DISTRO=Ubuntu\nDOWNLOAD_DIR="/tmp/My Music & 音楽"\n', encoding='utf-8')
            config = load_config(path, environ={'WSL_DISTRO': 'Ubuntu-24.04'})
            self.assertEqual(config, Config('testuser', '/tmp/My Music & 音楽', 'Ubuntu-24.04'))

    def test_invalid_paths_and_names_fail_before_subprocess(self):
        for path in ['relative', '/', '/tmp/../etc', '//host/share', 'C:\\Music', '/tmp/evil\nname']:
            with self.subTest(path=path), self.assertRaises(ConfigError):
                Config('testuser', path)
        with self.assertRaises(ConfigError):
            Config('--root', '/tmp/media')
        with self.assertRaises(ConfigError):
            Config('testuser', '/tmp/media', '../Ubuntu')

    def test_parser_does_not_execute_or_expand(self):
        self.assertEqual(parse_env('DOWNLOAD_DIR=/tmp/$(echo example)/$HOME')['DOWNLOAD_DIR'], '/tmp/$(echo example)/$HOME')
        for text in ['WSL_USER', 'UNKNOWN=value', 'WSL_USER="test', 'WSL_USER=a\nWSL_USER=b']:
            with self.subTest(text=text), self.assertRaises(ConfigError):
                parse_env(text)

    def test_every_output_operation_uses_configured_directory(self):
        cfg = Config('testuser', '/tmp/My Music & 音楽', 'Ubuntu')
        runner = WslRunner(config=cfg)
        args = runner.command('python3', '-c', 'pass')
        self.assertEqual(args[args.index('--user') + 1], cfg.user)
        self.assertEqual(args[args.index('--cd') + 1], '~')
        self.assertEqual(runner.explorer_path(), '\\\\wsl.localhost\\Ubuntu\\tmp\\My Music & 音楽')
        task = Task('https://youtu.be/test', Mode.MP3, 'test', 'test')
        args = download_args(task, cfg.download_dir)
        self.assertEqual(args[args.index('--paths') + 1], cfg.download_dir)
        with patch.object(runner, 'execute', return_value='[]') as execute:
            runner.list_files()
            self.assertEqual(execute.call_args.args[1][-1], cfg.download_dir)
        self.assertIsNone(parse_progress('__WMD_FILE__:"/tmp/other/test.mp3"', cfg.download_dir))

    def test_packaged_config_is_next_to_exe_not_working_directory(self):
        with patch('sys.frozen', True, create=True), patch('sys.executable', str(Path.cwd() / 'portable' / 'app.exe')):
            self.assertEqual(default_config_path(), Path.cwd() / 'portable' / '.env')

    def test_missing_config_opens_gui_with_setup_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            runner = WslRunner(config_path=Path(tmp) / '.env')
            with patch.dict('os.environ', {}, clear=True), patch('subprocess.run') as execute:
                with self.assertRaises(ConfigError):
                    runner.check_environment()
                execute.assert_not_called()
