"""
Puts a finished course file on the server the bot runs on. The file goes up under
another name, its hash is compared with the local one, and only then is it renamed
over the file the bot reads. A rename is one step, so the bot sees the old file or
the new one and never half of either.
"""
import shlex
import subprocess
from pathlib import Path
from typing import Callable, List

from sync.cache import file_sha
from sync.settings import SERVER, SERVER_FILE

SSH_OPTIONS = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=20"]
UPLOAD_SECONDS = 600

Runner = Callable[..., "subprocess.CompletedProcess[str]"]


class UploadFailed(RuntimeError):
    """The file did not arrive whole. The server still has the file it had before."""


def upload(local: Path, server: str = SERVER, remote: str = SERVER_FILE, run: Runner = subprocess.run) -> None:
    if not server:
        raise UploadFailed("no server is set, see CHRONOCHUNK_SERVER in sync/settings.py")
    arriving = remote + ".arriving"

    def step(command: List[str], what: str) -> str:
        result = run(command, capture_output=True, text=True, timeout=UPLOAD_SECONDS, check=False)
        if result.returncode != 0:
            raise UploadFailed(f"{what} failed: {(result.stderr or result.stdout).strip()[:300]}")
        return result.stdout

    step(["scp", "-q", *SSH_OPTIONS, str(local), f"{server}:{arriving}"], "copying the file")
    answer = step(["ssh", *SSH_OPTIONS, server, f"sha256sum {shlex.quote(arriving)}"], "checking the copy")
    if answer.split()[:1] != [file_sha(local)]:
        step(["ssh", *SSH_OPTIONS, server, f"rm -f {shlex.quote(arriving)}"], "removing the bad copy")
        raise UploadFailed("the copy on the server differs from the local file")
    step(["ssh", *SSH_OPTIONS, server, f"mv -f {shlex.quote(arriving)} {shlex.quote(remote)}"], "swapping the file in")
