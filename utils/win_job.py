"""Tie a child process's life to this one, so it cannot outlive us.

The autopilot fleet is started in its own process group, which is what lets F2
send it a Ctrl+Break for a graceful stop without also stopping the server. The
side effect is that Ctrl+C in the console no longer reaches it: Ctrl+C goes to
the console's process group, the fleet is in a different one, so the server
dies and two bots keep tapping away with nothing left to stop them. Killing the
server any other way - a crash, Task Manager - leaves the same orphans.

A Windows job object fixes it at the level where it cannot be got wrong.
Processes assigned to a job with KILL_ON_JOB_CLOSE are terminated by the kernel
when the last handle to that job closes, and the handle closes when this
process ends, however it ends. Children the fleet spawns inherit the job, so
the workers go with it.

This is a backstop, not the normal path: a clean stop should still ask the
fleet to wind down so it can finish what it is doing.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes

JobObjectExtendedLimitInformation = 9
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000


class _IO_COUNTERS(ctypes.Structure):
  _fields_ = [("ReadOperationCount", ctypes.c_ulonglong),
              ("WriteOperationCount", ctypes.c_ulonglong),
              ("OtherOperationCount", ctypes.c_ulonglong),
              ("ReadTransferCount", ctypes.c_ulonglong),
              ("WriteTransferCount", ctypes.c_ulonglong),
              ("OtherTransferCount", ctypes.c_ulonglong)]


class _JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
  _fields_ = [("PerProcessUserTimeLimit", wintypes.LARGE_INTEGER),
              ("PerJobUserTimeLimit", wintypes.LARGE_INTEGER),
              ("LimitFlags", wintypes.DWORD),
              ("MinimumWorkingSetSize", ctypes.c_size_t),
              ("MaximumWorkingSetSize", ctypes.c_size_t),
              ("ActiveProcessLimit", wintypes.DWORD),
              ("Affinity", ctypes.POINTER(ctypes.c_ulong)),
              ("PriorityClass", wintypes.DWORD),
              ("SchedulingClass", wintypes.DWORD)]


class _JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
  _fields_ = [("BasicLimitInformation", _JOBOBJECT_BASIC_LIMIT_INFORMATION),
              ("IoInfo", _IO_COUNTERS),
              ("ProcessMemoryLimit", ctypes.c_size_t),
              ("JobMemoryLimit", ctypes.c_size_t),
              ("PeakProcessMemoryUsed", ctypes.c_size_t),
              ("PeakJobMemoryUsed", ctypes.c_size_t)]


def kill_child_when_we_exit(process) -> object | None:
  """Put `process` (a subprocess.Popen) in a job that dies with this process.

  Returns the job handle, which the caller must keep referenced for as long as
  the child should live - letting it be garbage collected closes the job and
  kills the child immediately. Returns None if this is not Windows or the
  kernel refused, in which case the caller is no worse off than before.
  """
  if not hasattr(ctypes, "windll"):
    return None
  try:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    job = kernel32.CreateJobObjectW(None, None)
    if not job:
      return None

    limits = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
    limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not kernel32.SetInformationJobObject(
        job, JobObjectExtendedLimitInformation,
        ctypes.byref(limits), ctypes.sizeof(limits)):
      kernel32.CloseHandle(job)
      return None

    # Popen keeps the process handle here on Windows.
    handle = int(process._handle)
    if not kernel32.AssignProcessToJobObject(job, handle):
      kernel32.CloseHandle(job)
      return None
    return job
  except Exception:
    return None
