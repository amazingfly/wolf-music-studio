import fcntl
import json
import multiprocessing as mp
from pathlib import Path
import subprocess
import sys
import time
import pytest

sys.path.insert(0,str(Path(__file__).parents[1]))
from local_compute import compute_lease, process_stamp
from gemma_runtime import scoped_stop
from track_registry import atomic_json


def worker(root,name,events,release=None):
    with compute_lease(name,root=root,blockers=lambda _: []):
        events.put(name)
        if release is not None:
            release.wait(10)


def wait_tickets(root,count):
    deadline=time.monotonic()+8
    while time.monotonic()<deadline:
        if len(list((root/'waiting').glob('*.json'))) >= count:
            return
        time.sleep(.03)
    pytest.fail('waiter did not enqueue')


def test_fifo_no_preemption_and_stale_ticket_cleanup(tmp_path):
    ctx=mp.get_context('fork'); events=ctx.Queue(); release=ctx.Event()
    first=ctx.Process(target=worker,args=(tmp_path,'gemma',events,release))
    second=ctx.Process(target=worker,args=(tmp_path,'visualizer',events))
    third=ctx.Process(target=worker,args=(tmp_path,'gemma',events))
    processes=[first,second,third]
    try:
        first.start(); assert events.get(timeout=8)=='gemma'
        second.start(); wait_tickets(tmp_path,1)
        third.start(); wait_tickets(tmp_path,2)
        atomic_json(tmp_path/'waiting/00000000000000000000_stale.json',{'pid':99999999,'process_stamp':'gone'})
        assert len(list((tmp_path/'waiting').glob('*'))) >= 2
        release.set()
        assert events.get(timeout=8)=='visualizer'
        assert events.get(timeout=8)=='gemma'
        for process in processes:
            process.join(8); assert process.exitcode==0
        assert not list((tmp_path/'waiting').glob('*.json'))
    finally:
        release.set()
        for process in processes:
            if process.pid and process.is_alive(): process.terminate(); process.join(5)


def test_inherited_child_keeps_hardware_locked_after_parent_context_closes(tmp_path):
    child=None
    try:
        with compute_lease('gemma',root=tmp_path,blockers=lambda _: [] ) as fd:
            child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(2)'],pass_fds=(fd,))
        with (tmp_path/'hardware.lock').open('a') as lock:
            with pytest.raises(BlockingIOError):
                fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            child.wait(timeout=5)
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    finally:
        if child and child.poll() is None: child.terminate(); child.wait()


def test_stop_refuses_unrelated_process_even_with_matching_pid_stamp(tmp_path):
    child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(10)'])
    try:
        atomic_json(tmp_path/'gemma_runtime.json',{'pid':child.pid,'process_stamp':process_stamp(child.pid)})
        scoped_stop(tmp_path)
        assert child.poll() is None
    finally:
        child.terminate(); child.wait()
