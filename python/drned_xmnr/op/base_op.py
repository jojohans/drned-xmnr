# -*- mode: python; python-indent: 4 -*-

import fcntl
import os
import select
import glob

import _ncs.dp as dp
import _ncs.maapi as _maapi
from ncs import maapi, maagic

from ex import ActionError


class BaseOp(object):
    def __init__(self, uinfo, dev_name, params, log_obj):
        self.uinfo = uinfo
        self.dev_name = dev_name
        self.maapi = maapi.Maapi()
        self.log = log_obj
        self.run_with_trans(self._setup_directories)
        self._init_params(params)

    def _setup_directories(self, trans):
        root = maagic.get_root(trans)
        self.xmnr_directory = root.drned_xmnr.xmnr_directory
        self.drned_run_directory = os.path.join(self.xmnr_directory, 'drned')
        self.states_dir = os.path.join(self.xmnr_directory, self.dev_name)
        try:
            os.makedirs(self.states_dir)
        except:
            pass

    def _init_params(self, params):
        # Implement in subclasses
        pass

    def param_default(self, params, name, default):
        value = getattr(params, name)
        if value is None:
            return default
        return value

    statefile_extension = '.state.cfg'

    def state_name_to_filename(self, statename):
        return os.path.join(self.states_dir, statename + self.statefile_extension)

    def state_filename_to_name(self, filename):
        return os.path.basename(filename)[:-len(self.statefile_extension)]

    def get_states(self):
        return [self.state_filename_to_name(f) for f in self.get_state_files()]

    def run_with_trans(self, callback, write=False):
        if self.uinfo.actx_thandle == -1:
            if write:
                with maapi.single_write_trans(self.uinfo.username, self.uinfo.context) as trans:
                    return callback(trans)
            else:
                with maapi.single_read_trans(self.uinfo.username, self.uinfo.context) as trans:
                    return callback(trans)
        else:
            mp = maapi.Maapi()
            return callback(mp.attach(self.uinfo.actx_thandle))

    def get_state_files(self):
        return glob.glob(self.state_name_to_filename('*'))

    def extend_timeout(self, timeout_extension):
        dp.action_set_timeout(self.uinfo, timeout_extension)

    def proc_run(self, proc, outputfun, timeout):
        fd = proc.stdout.fileno()
        fl = fcntl.fcntl(fd, fcntl.F_GETFL)
        fcntl.fcntl(fd, fcntl.F_SETFL, fl | os.O_NONBLOCK)

        state = None
        stdoutdata = ""
        while proc.poll() is None:
            rlist, wlist, xlist = select.select([fd], [], [fd], timeout)
            if rlist:
                buf = proc.stdout.read()
                if buf != "":
                    self.log.debug("run_outputfun, output len=" + str(len(buf)))
                    state = outputfun(state, buf)
                    stdoutdata += buf
            else:
                self.progress_msg("Silence timeout, terminating process\n")
                proc.kill()

        self.log.debug("run_finished, output len=" + str(len(stdoutdata)))
        return proc.wait()

    def progress_msg(self, msg):
        self.log.debug(msg)
        if self.uinfo.context == 'cli':
            _maapi.cli_write(self.maapi.msock, self.uinfo.usid, msg)

    def get_exe_path(self, exe):
        path = self.get_exe_path_from_PATH(exe)
        if not os.path.exists(path):
            msg = 'Unable to execute {0}, command no found in PATH {1}'
            raise ActionError(msg.format(exe, os.environ['PATH']))

        return path

    def get_exe_path_from_PATH(self, exe):
        parts = (os.environ['PATH'] or '/bin').split(os.path.pathsep)
        for part in parts:
            path = os.path.join(part, exe)
            if os.path.exists(path):
                return path
        return None
