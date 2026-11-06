import asyncio,socket,tempfile,unittest
from pathlib import Path
from unittest.mock import AsyncMock,patch
from veriworld.common.runtime_args import isolated_runtime_args
from veriworld.infra.interactive.engine import InteractiveEngine
from veriworld.infra.computational.engine import ComputationalEngine

class RuntimeIsolationTests(unittest.TestCase):
    def test_independent_launches_have_different_scratch_and_restarts_preserve_it(self):
        with patch('veriworld.common.runtime_args.tempfile.mkdtemp',side_effect=['one','two']):
            first=isolated_runtime_args([],9000);second=isolated_runtime_args([],9001)
            self.assertNotEqual(first,second)
            self.assertEqual(first,isolated_runtime_args(first,9000))
        self.assertEqual(isolated_runtime_args(['-UserDir=explicit','-ForcePython','-WebSocketAddress=127.0.0.1'],9000),['-UserDir=explicit','-ForcePython','-WebSocketAddress=127.0.0.1'])

    def test_failed_interactive_launch_cleans_owned_process(self):
        with patch.object(InteractiveEngine,'start',new=AsyncMock(side_effect=RuntimeError('connection failed'))),patch.object(InteractiveEngine,'stop',new=AsyncMock()) as stop:
            with self.assertRaisesRegex(RuntimeError,'connection failed'):asyncio.run(InteractiveEngine.launch(exe=Path('unused'),port=9000))
            stop.assert_awaited_once()

    def test_busy_computational_port_never_launches_or_connects(self):
        with tempfile.TemporaryDirectory() as folder,socket.socket() as listener:
            listener.bind(('127.0.0.1',0));listener.listen()
            exe=Path(folder)/'engine';exe.touch()
            engine=ComputationalEngine(exe,listener.getsockname()[1])
            with patch('subprocess.Popen',side_effect=AssertionError('launch forbidden')):
                with self.assertRaises(OSError):asyncio.run(engine.next_round('Untitled'))

if __name__=='__main__':unittest.main()
