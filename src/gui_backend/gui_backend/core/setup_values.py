"""The Setup page's file, as values the backend actually uses.

The Setup page writes ``asket_setup.yaml`` (``setup_store``). For the fields
``gui_backend`` applies itself, this is what reads them back: ``RosSource``
asks here instead of trusting ``topics.yaml`` alone, so a saved value is the
value in use. The file is stat'ed at most every couple of seconds and parsed
only when it changes. A file that will not parse is reported once in the log
and the shipped defaults are used: the Setup page and the pre-flight both say
loudly that the file is broken.
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path

from asket_common.setup_profile import SetupProfile

from . import setup_store

_log = logging.getLogger(__name__)


class SetupValues:
    RECHECK_S = 2.0

    def __init__(self, path: Path | str | None = None, clock=time.monotonic) -> None:
        self._path = Path(path) if path is not None else None
        self._clock = clock
        self._checked = -float("inf")
        self._mtime: float | None = None
        self._profile = SetupProfile()
        self.error = ""

    @property
    def path(self) -> Path:
        # Resolved on use, so it follows setup_store.DEFAULT_PATH (which the
        # store itself reads on every save).
        return self._path if self._path is not None else setup_store.DEFAULT_PATH

    def refresh(self, force: bool = False) -> SetupProfile:
        now = self._clock()
        if not force and now - self._checked < self.RECHECK_S:
            return self._profile
        self._checked = now
        try:
            mtime = os.stat(self.path).st_mtime
        except OSError:
            self._mtime = None
            self._profile = SetupProfile()
            self.error = ""
            return self._profile
        if mtime == self._mtime and not force:
            return self._profile
        self._mtime = mtime
        try:
            self._profile = setup_store.load(self.path)
            self.error = ""
        except setup_store.SetupFileError as exc:
            if str(exc) != self.error:
                _log.error("setup file not used, defaults apply: %s", exc)
            self.error = str(exc)
            self._profile = SetupProfile()
        return self._profile

    def get(self, field_id: str):
        """The saved answer, or the field's shipped default."""
        return self.refresh().get(field_id)

    def answered(self, field_id: str) -> bool:
        return self.refresh().answered(field_id)
