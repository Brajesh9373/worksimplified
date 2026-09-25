# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Chronological sequence barrier for deterministic replay ordering."""

from __future__ import annotations

import asyncio
import logging
import os

from .._errors import WorkflowInvariantError

logger = logging.getLogger(__name__)

# A timeout on the barrier means execution diverged from the recorded sequence. The
# default is to fail loudly: for a workflow that is expected to replay exactly, that
# divergence is a real bug. A host whose execution legitimately re-plans between
# passes (pausing for a human, or re-entering a stage to resolve a change request)
# cannot satisfy the guard, and set ADK_REPLAY_BARRIER_ADVISORY=1 so a missing key
# is skipped instead of killing the node.
_ADVISORY_VALUES = ("1", "true", "yes", "on")


def _advisory_from_env() -> bool:
  return os.getenv("ADK_REPLAY_BARRIER_ADVISORY", "").strip().lower() in _ADVISORY_VALUES


class ReplaySequenceBarrier:
  """Unified chronological sequence barrier to ensure deterministic replay ordering."""

  def __init__(self, sequence: list[str], timeout_sec: float = 15.0,
               advisory: bool | None = None) -> None:
    self.sequence = sequence
    self.timeout_sec = timeout_sec
    self.advisory = _advisory_from_env() if advisory is None else advisory
    self.current_index = 0
    self.events = {key: asyncio.Event() for key in sequence}
    if sequence:
      self.events[sequence[0]].set()

  async def wait(self, key: str) -> None:
    """Wait for the barrier if the key is part of the expected chronological sequence.

    Only wait if the node had a terminal event (output, route, or interrupt).
    "Silent" nodes that only yielded state updates but didn't produce
    output are not in the sequence barrier, so they fast-forward immediately.

    In advisory mode a timeout skips the missing key instead of raising, so a pass
    that will never produce it (because the caller re-planned) still completes.
    """
    if key in self.events:
      try:
        await asyncio.wait_for(
            self.events[key].wait(), timeout=self.timeout_sec
        )
      except asyncio.TimeoutError as e:
        if not self.advisory:
          raise WorkflowInvariantError(
              "Replay divergence detected: Timed out waiting for sequence key"
              f" '{key}' to be unblocked."
          ) from e
        logger.warning(
            "Replay sequence barrier timed out on key '%s' (%d of %d recorded); "
            "continuing without it - this pass will not produce that node.",
            key,
            self.current_index,
            len(self.sequence),
        )
        # Align the barrier with the pass we are actually in: step past the key we
        # gave up on and release the one that follows it, so the next wait is not
        # stuck behind the same gap.
        try:
          idx = self.sequence.index(key)
        except ValueError:
          idx = self.current_index
        self.current_index = min(len(self.sequence), idx + 1)
        if self.current_index < len(self.sequence):
          self.events[self.sequence[self.current_index]].set()

  def check_and_advance(self, key: str) -> None:
    """Advance the sequence if the key matches the current expected execution."""
    if self.current_index < len(self.sequence):
      expected_key = self.sequence[self.current_index]
      if key == expected_key:
        self.current_index += 1
        if self.current_index < len(self.sequence):
          next_key = self.sequence[self.current_index]
          self.events[next_key].set()
