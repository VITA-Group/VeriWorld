"""Keep launchers' scratch files separate when sharing one installed runtime."""
from pathlib import Path
import tempfile


def isolated_runtime_args(extra_args, port):
    args=list(extra_args)
    if not any(arg.lower().startswith('-userdir=') for arg in args):
        directory=Path(tempfile.mkdtemp(prefix=f'veriworld-port{port}-')).resolve()
        args.append('-UserDir='+str(directory))
    if '-ForcePython' not in args:args.append('-ForcePython')
    if not any(arg.lower().startswith('-websocketaddress=') for arg in args):
        args.append('-WebSocketAddress=127.0.0.1')
    return args
