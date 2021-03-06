'''
Filter composition implemented as coroutine chaining.  See `David
Beazly's presentation
<http://www.dabeaz.com/coroutines/Coroutines.pdf>`_ about coroutines.
'''

import functools
import re


def coroutine(fn):
    @functools.wraps(fn)
    def start(*args, **kwargs):
        cr = fn(*args, **kwargs)
        next(cr)
        return cr
    return start


@coroutine
def drop():
    while True:
        yield


@coroutine
def filter_sink(writer):
    while True:
        item = yield
        if isinstance(item, str):
            writer(item)


class Closeable(object):
    def __init__(self, coroutine, consumer):
        self.coroutine = coroutine
        self.consumer = consumer

    def close(self):
        self.consumer.close()
        self.coroutine.close()

    def send(self, data):
        self.coroutine.send(data)


class LineProducer(Closeable):
    def __init__(self, line_processor):
        cort = line_producer(line_processor)
        super(LineProducer, self).__init__(cort, line_processor)


@coroutine
def line_producer(line_proc):
    buf = ''
    try:
        while True:
            data = yield
            lines = data.split('\n')
            lines[0] = buf + lines[0]
            for line in lines[:-1]:
                line_proc.send(line)
            buf = lines[-1]
    except GeneratorExit:
        line_proc.close()

#
# Event handling
#
# Event is an instance of `LineOutputEvent`; it is produced by
# `event_generator` and passed to a simple pushdown automaton.  The
# automaton takes care of state transitions during log processing - an
# event can cause a state transition and an output in the form of a
# line sent to the next filter in the chain.


class StateTerminatedObject(object):
    pass


TERMINATED = StateTerminatedObject()


class LineOutputEvent(object):
    indent = 3 * ' '
    state_name_regexp = re.compile(r'.*/states/([^/]*)\.state\.(cfg|xml)')

    @staticmethod
    def indent_line(line):
        return '{}{}\n'.format(LineOutputEvent.indent, line)

    def __init__(self, line):
        self.line = line
        self.complete = False

    def __str__(self):
        return 'Line event {} {}'.format(self.__class__.__name__, self.line)

    def mark_complete(self):
        self.complete = True

    def produce_line(self):
        return self.line + '\n'


class InitStates(LineOutputEvent):
    pass


class StartState(LineOutputEvent):
    pass


class Transition(LineOutputEvent):
    pass


class InitFailed(LineOutputEvent):
    pass


class TransFailed(LineOutputEvent):
    pass


class PyTest(LineOutputEvent):
    def produce_line(self):
        state = self.state_name_regexp.search(self.line).groups()[0]
        return 'Test transition to {}\n'.format(state)


class DrnedPrepare(LineOutputEvent):
    def __init__(self):
        super(DrnedPrepare, self).__init__('')

    def produce_line(self):
        return self.indent_line('prepare the device')


class DrnedEvent(LineOutputEvent):
    pass


class DrnedActionEvent(DrnedEvent):
    def __init__(self, line, action):
        super(DrnedActionEvent, self).__init__(line)
        self.action = action

    def __str__(self):
        return 'Drned event ' + self.action

    def produce_line(self):
        if self.action == 'load':
            state = self.state_name_regexp.search(self.line).groups()[0]
            line = 'load ' + state
        elif self.action == 'compare_config':
            line = 'compare config'
        else:
            line = self.action
        return self.indent_line(line)


class DrnedCommitEvent(DrnedEvent):
    pass


class DrnedEmptyCommit(DrnedCommitEvent):
    def __init__(self):
        super(DrnedEmptyCommit, self).__init__('    (no modifications)')

    def __str__(self):
        return 'Drned empty commit event'

    def produce_line(self):
        return self.indent_line(self.line)


class DrnedCommitLogEvent(DrnedCommitEvent):
    def __init__(self):
        super(DrnedCommitLogEvent, self).__init__('')

    def __str__(self):
        return 'Drned commit queue event'

    def produce_line(self):
        return self.indent_line('commit...')  # should not be used?


class DrnedCommitNNEvent(DrnedCommitEvent):
    def __init__(self):
        super(DrnedCommitNNEvent, self).__init__('')

    def __str__(self):
        return 'Drned commit no-networking event'

    def produce_line(self):
        # should not be used?
        return self.indent_line('commit no networking...')


class DrnedCommitResult(DrnedCommitEvent):
    def __init__(self, line, success):
        super(DrnedCommitResult, self).__init__(line)
        self.success = success

    def __str__(self):
        return 'Drned commit result event'

    def produce_line(self):
        line = '    succeeded' if self.success else '    failed'
        return self.indent_line(line)


class DrnedCommitComplete(DrnedCommitEvent):
    def __init__(self, line):
        super(DrnedCommitComplete, self).__init__(line)
        self.success = True

    def __str__(self):
        return 'Drned commit complete event'

    def produce_line(self):
        line = '    succeeded'
        return self.indent_line(line)


class DrnedFailureReason(DrnedCommitEvent):
    def __init__(self, reason):
        super(DrnedFailureReason, self).__init__('')
        self.reason = reason

    def __str__(self):
        return 'Drned commit failure: {}'.format(self.reason)

    def produce_line(self):
        max = 40
        if len(self.reason) > max:
            msg = self.reason[:max] + "..."
        else:
            msg = self.reason
        line = '    failed ({})'.format(msg)
        return self.indent_line(line)


class DrnedCompareEvent(DrnedEvent):
    def __init__(self, success):
        super(DrnedCompareEvent, self).__init__('')
        self.success = success

    def __str__(self):
        return 'Drned compare complete event: {}'.format(self.success)

    def produce_line(self):
        line = '    succeeded' if self.success else '    failed'
        return self.indent_line(line)


class DrnedTeardown(LineOutputEvent):
    def __init__(self):
        super(DrnedTeardown, self).__init__('')

    def __str__(self):
        return 'Drned teardown event'

    def produce_line(self):
        return 'Device cleanup\n'


class DrnedRestore(DrnedActionEvent):
    def __init__(self):
        super(DrnedRestore, self).__init__('restore', 'load before-session')


class TerminateEvent(LineOutputEvent):
    def __init__(self):
        super(TerminateEvent, self).__init__('')

    def __str__(self):
        return 'Terminate event'

    def produce_line(self):
        return ''


class EventGenerator(Closeable):
    def __init__(self, consumer):
        cort = event_generator(consumer)
        super(EventGenerator, self).__init__(cort, consumer)

    def close(self):
        try:
            # we need to let the consumer know; but it can raise
            # StopIteration
            self.consumer.send(TerminateEvent())
        except StopIteration:
            pass
        self.coroutine.close()


line_regexp = re.compile('''\
(?:\
(?P<init_states>Found [0-9]* states recorded for device .*)|\
(?P<start>Starting with state .*)|\
(?P<py_test>py.test -k test_template_set --fname=[^ ]*.state.(cfg|xml)\
(?: --op=[^ ]*)*(?: --end-op=)? --device=[^ ]*)|\
(?P<transition>Transition [0-9]*/[0-9]*: .* ==> .*)|\
(?P<init_failed>Failed to initialize state .*)|\
(?P<trans_failed>Transition failed)|\
(?P<drned_load>={30} r?load\\(.*/states/.*\\))|\
(?P<drned>={30} (?P<drned_op>commit|compare_config|rollback)\\(.*\\))|\
(?P<no_modifs>% No modifications to commit\\.)|\
(?P<commit_queue>commit-queue \\{)|\
(?P<commit_nn>commit no-networking)|\
(?P<commit_complete>Commit complete\\.)|\
(?P<commit_result> *status (?P<result>completed|failed))|\
(?P<failure_reason> *reason (?P<reason>RPC error .*))|\
(?P<teardown>### TEARDOWN, RESTORE DEVICE ###)|\
(?P<restore>={30} load\\(drned-work/before-session.xml\\))|\
(?P<diff>diff *)\
)$''')


@coroutine
def event_generator(consumer):
    '''Based on the line input, generate events and pass them to the consumer.
    '''
    try:
        while True:
            line = yield
            match = line_regexp.match(line)
            if match is None:
                continue
            if match.lastgroup == 'start':
                consumer.send(StartState(match.string))
                consumer.send(DrnedPrepare())
            elif match.lastgroup == 'init_failed':
                consumer.send(InitFailed(match.string))
            elif match.lastgroup == 'trans_failed':
                consumer.send(TransFailed(match.string))
            elif match.lastgroup == 'transition':
                consumer.send(Transition(match.string))
                consumer.send(DrnedPrepare())
            elif match.lastgroup == 'py_test':
                consumer.send(PyTest(match.string))
            elif match.lastgroup == 'init_states':
                consumer.send(InitStates(match.string))
            elif match.lastgroup == 'drned_load':
                consumer.send(DrnedActionEvent(match.string, 'load'))
            elif match.lastgroup == 'drned':
                consumer.send(DrnedActionEvent(match.string, match.groupdict()['drned_op']))
            elif match.lastgroup == 'no_modifs':
                consumer.send(DrnedEmptyCommit())
            elif match.lastgroup == 'commit_queue':
                consumer.send(DrnedCommitLogEvent())
            elif match.lastgroup == 'commit_nn':
                consumer.send(DrnedCommitNNEvent())
            elif match.lastgroup == 'commit_result':
                consumer.send(DrnedCommitResult(match.string,
                                                match.groupdict()['result'] == 'completed'))
            elif match.lastgroup == 'commit_complete':
                consumer.send(DrnedCommitComplete(match.string))
            elif match.lastgroup == 'failure_reason':
                consumer.send(DrnedFailureReason(match.groupdict()['reason']))
            elif match.lastgroup == 'teardown':
                consumer.send(DrnedTeardown())
            elif match.lastgroup == 'restore':
                consumer.send(DrnedRestore())
            elif match.lastgroup == 'diff':
                consumer.send(DrnedCompareEvent(False))
    except GeneratorExit:
        consumer.close()


class LogStateMachine(object):
    '''Simple stack state machine.  Transition between states occur based
    on `LineOutputEvent` instances.
    '''
    def __init__(self, init_state):
        self.stack = [init_state]

    def handle(self, event):
        while not event.complete and self.stack != []:
            # print('handling', event, self.stack)
            state = self.stack[-1]
            result = state.handle(event)
            if isinstance(result, tuple):
                line, newstate = result
                if newstate is TERMINATED:
                    self.stack.pop()
                    yield line
                else:
                    self.stack.append(newstate)
                    yield line
            elif isinstance(result, str):
                yield result
            elif isinstance(result, LogState):
                self.stack.append(result)
            elif result is TERMINATED:
                self.stack.pop()


class LogState(object):
    def handle(self, event):
        return event.produce_line()


class TransitionLogState(LogState):
    def __init__(self, level):
        self.level = level
        self.initialized = False

    def handle(self, event):
        if self.level == 'overview':
            event.mark_complete()
            return None
        if not self.initialized:
            self.initialized = True
            return (DrnedPrepare().produce_line(), DrnedLogState())
        event.mark_complete()
        return event.produce_line()


class ExploreLogState(LogState):
    def __init__(self, level):
        self.level = level

    def handle(self, event):
        event.mark_complete()
        if self.level == 'overview' and (isinstance(event, DrnedPrepare) or
                                         isinstance(event, DrnedEvent)):
            return None
        if isinstance(event, DrnedTeardown):
            if self.level == 'overview':
                return None
            return (event.produce_line(), DrnedLogState())
        if isinstance(event, DrnedPrepare):
            return (event.produce_line(), DrnedLogState())
        return event.produce_line()


class WalkLogState(LogState):
    def __init__(self, level):
        self.level = level

    def handle(self, event):
        event.mark_complete()
        if self.level == 'overview' and isinstance(event, DrnedEvent):
            return None
        if isinstance(event, PyTest) or isinstance(event, DrnedTeardown):
            if self.level == 'overview':
                return event.produce_line()
            return (event.produce_line(), DrnedLogState())
        if not isinstance(event, DrnedEmptyCommit) and \
           not isinstance(event, DrnedCommitComplete):
            # commits can occur on this level
            return event.produce_line()
        return None


class DrnedLogState(LogState):
    def handle(self, event):
        if isinstance(event, DrnedEmptyCommit):
            event.mark_complete()
            return None
        if not isinstance(event, DrnedActionEvent):
            return TERMINATED
        event.mark_complete()
        if event.action == 'commit':
            return (event.produce_line(), DrnedCommitState())
        elif event.action == 'compare_config':
            return (event.produce_line(), DrnedCompareState())
        else:
            return event.produce_line()


class DrnedCommitState(LogState):
    def handle(self, event):
        if isinstance(event, DrnedCommitLogEvent):
            event.mark_complete()
            return DrnedCommitResultState()
        elif isinstance(event, DrnedCommitNNEvent):
            event.mark_complete()
            return DrnedCommitNNState()
        elif isinstance(event, DrnedEmptyCommit):
            event.mark_complete()
            return (event.produce_line(), TERMINATED)
        elif isinstance(event, DrnedCommitEvent):
            event.mark_complete()
            return TERMINATED
        return TERMINATED


class DrnedCommitResultState(LogState):
    def handle(self, event):
        if isinstance(event, DrnedCommitResult):
            event.mark_complete()
            if event.success:
                return (event.produce_line(), TERMINATED)
            else:
                return DrnedFailureState()
        return TERMINATED


class DrnedCommitNNState(LogState):
    def handle(self, event):
        if isinstance(event, DrnedCommitComplete):
            event.mark_complete()
            return (event.produce_line(), TERMINATED)
        elif isinstance(event, DrnedActionEvent) and event.action == 'commit':
            event.mark_complete()
            return (DrnedCommitComplete(event.line).produce_line(), TERMINATED)
        elif isinstance(event, DrnedCommitComplete):
            event.mark_complete()
            return (event.produce_line(), TERMINATED)
        return TERMINATED


class DrnedFailureState(LogState):
    def handle(self, event):
        if isinstance(event, DrnedFailureReason):
            event.mark_complete()
            return (event.produce_line(), TERMINATED)
        return TERMINATED


class DrnedCompareState(LogState):
    def handle(self, event):
        if isinstance(event, DrnedCompareEvent):
            event.mark_complete()
            return (event.produce_line(), TERMINATED)
        else:
            # need to produce a line, but the event is not handled yet
            art_event = DrnedCompareEvent(True)
            return (art_event.produce_line(), TERMINATED)


def transition_output_filter(level, sink):
    machine = LogStateMachine(TransitionLogState(level))
    return run_event_machine(machine, sink)


def explore_output_filter(level, sink):
    machine = LogStateMachine(ExploreLogState(level))
    return run_event_machine(machine, sink)


def walk_output_filter(level, sink):
    machine = LogStateMachine(WalkLogState(level))
    handler = run_event_machine(machine, sink)
    handler.send(LineOutputEvent('Prepare the device'))
    return handler


@coroutine
def run_event_machine(machine, sink):
    try:
        while True:
            event = yield
            for line in machine.handle(event):
                sink.send(line)
    except GeneratorExit:
        sink.close()


def build_filter(op, level, write):
    sink = filter_sink(write)
    lines = op.event_processor(level, sink)
    events = EventGenerator(lines)
    return LineProducer(events)
