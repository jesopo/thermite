import time
from collections import deque
from random import choice as random_choice
from string import hexdigits
from typing import cast, Deque, Dict, Sequence, Set

from irctokens import build, Line
from ircstates import User
from ircrobots import Bot as BaseBot
from ircrobots import Server as BaseServer

from ircstates.numerics import RPL_WELCOME, RPL_CREATIONTIME, ERR_NOSUCHCHANNEL
from ircrobots.ircv3 import Capability
from ircrobots.matching import Folded, Response, SELF

from . import mformat
from .config import Config
from .database import Database

CAP_OPER = Capability(None, "solanum.chat/oper")

BACKLOG_MAX = 64
BACKLOG: Dict[str, Deque[str]] = {}

# log relaying head
class WriteServer(BaseServer):
    def __init__(self, bot: BaseBot, name: str, config: Config, database: Database):
        super().__init__(bot, name)
        self.bot: BaseBot
        self.desired_caps.add(CAP_OPER)

        self._config = config
        self._database = database

        # map logged channel to log output channel
        self._source_map: Dict[str, str] = {}
        # visa versa
        self._target_map: Dict[str, str] = {}

    def set_throttle(self, rate: int, time: float) -> None:
        # turn off throttling
        pass

    def line_preread(self, line: Line) -> None:
        print(f"w< {line.format()}")

    def line_presend(self, line: Line) -> None:
        print(f"w> {line.format()}")

    def _human_users(self, channel: str) -> Set[User]:
        cusers = self.channels[self.casefold(channel)].users
        users = {self.users[n] for n in cusers.keys()}
        for user in list(users):
            # TODO: don't hardcode services.libera.chat
            if (
                user.hostname == "services.libera.chat"
                or user.nickname == self.nickname
            ):
                users.remove(user)
        return users

    async def _print_backlog(self, target: str, out: str) -> None:
        offset = len(f":{self.hostmask()} NOTICE {target} :")
        while out:
            out_take = out[: 510 - offset]
            out = out[len(out_take) :]
            await self.send(build("NOTICE", [target, out_take]))

    # non-private variant of _print_backlog, to be used by the read head so
    # that the read head isn't responsible for translating logged channel name
    # to log output channel name
    async def print_backlog(self, source: str, out: str) -> None:
        # `source` should already be casefolded by the time we're here. if it
        # isn't then we'll just not print anything
        if source in self._source_map:
            target = self._source_map[source]
            await self._print_backlog(target, out)

    async def line_read(self, line: Line) -> None:
        if line.command == RPL_WELCOME:
            for source, target in await self._database.get_pipes():
                self._source_map[source] = target
                self._target_map[target] = source
                # we *only* want to join `target` here.
                # `source` is the logged channel (for the read head)
                # `target` is the log output channel
                await self.send(build("JOIN", [target]))

        elif line.command == "PRIVMSG" and not self.is_me(line.hostmask.nickname):

            target = self.casefold(line.params[0])
            print(target, self._target_map)
            # we don't explicitly check that this is a channel message, but if
            # `target` is in _target_map or it is _config.channel, it's a
            # channel
            if target in self._target_map or target == self._config.channel:
                me = self.nickname
                first, _, rest = line.params[1].partition(" ")
                if first in [me, f"{me}:", f"{me},"] and rest:
                    # a highlight, which we treat as a command
                    command, _, args = rest.partition(" ")
                    await self.cmd(target, command.lower(), args)

        elif line.command == "JOIN":
            target = self.casefold(line.params[0])
            if target in self._target_map and len(self._human_users(target)) == 1:
                source = self._target_map[target]
                # log output channel was empty until this join. if we have a
                # backlog, replay it for the joining user
                for out in BACKLOG.get(source, []):
                    await self._print_backlog(target, out)

    async def cmd(
        self,
        channel: str,
        command: str,
        args: str,
    ) -> None:

        attrib = f"cmd_{command}"
        if hasattr(self, attrib):
            outs = await getattr(self, attrib)(channel, args)
            for out in outs:
                await self.send(build("NOTICE", [channel, out]))

    async def cmd_names(self, channel: str, sargs: str) -> Sequence[str]:
        if not channel in self._target_map:
            return ["this isn't a pipe target channel"]

        source = self._target_map[channel]
        names = self.channels[self.casefold(source)].users.keys()
        return [self.users[n].hostmask() for n in names]

    async def cmd_backlog(self, channel: str, sargs: str) -> Sequence[str]:
        if not channel in self._target_map:
            return ["this isn't a pipe target channel"]

        source = self._target_map[channel]
        if not source in BACKLOG:
            return [f"replayed 0 lines"]

        for out in BACKLOG[source]:
            await self.print_backlog(channel, out)
        return [f"replayed {len(BACKLOG[source])} lines"]

    def _new_pipe_target(self) -> str:
        target = self._config.pipe_name
        while "?" in target:
            target = target.replace("?", random_choice(hexdigits), 1)
        return target

    async def _channel_exists(self, channel: str) -> bool:
        await self.send(build("MODE", [channel]))
        line = await self.wait_for(
            {
                Response(RPL_CREATIONTIME, [SELF, Folded(channel)]),
                Response(ERR_NOSUCHCHANNEL, [SELF, Folded(channel)]),
            }
        )
        return line.command == RPL_CREATIONTIME

    async def cmd_pipe(self, channel: str, sargs: str) -> Sequence[str]:
        args = sargs.split(None, 1)
        if len(args) < 2:
            return ["please provide target channel and reason"]

        source = self.casefold(args[0])
        if not self.is_channel(source):
            return [f"'{args[0]}' isn't a valid channel name"]

        if source in self._source_map:
            target = self._source_map[source]
            return [f"{source} is already piped to {target}"]

        read_server_ = self.bot.servers.get("read", None)
        if read_server_ is None:
            return ["read head not connected"]

        # make sure our randomly generated pipe target isn't already in use
        while True:
            target = self._new_pipe_target()
            if not await self._channel_exists(target):
                break

        await self.send(build("JOIN", [target]))
        # TODO: wait until we receive a JOIN, error if we don't
        for pipe_line in self._config.make_pipe:
            await self.send_raw(pipe_line.format(TARGET=target, HOSTNAME=self.hostname))

        read_server = cast(ReadServer, read_server_)
        await read_server.send(build("JOIN", [source]))

        await self._database.add_pipe(source, target, args[1])
        read_server.channel_map[source] = target

        self._source_map[source] = target
        self._target_map[target] = source

        return [f"piped {source} to {target}"]

    async def cmd_unpipe(self, channel: str, sargs: str) -> Sequence[str]:
        target = self.casefold(channel)
        if not target in self._target_map:
            return ["this isn't a pipe target channel"]

        source = self._target_map.pop(target)
        del self._source_map[source]

        await self._database.remove_pipe(source)

        # if we were to return output from this command, the scope calling
        # this method would try to print it to a channel that this method
        # already parted, so we go straight for `self.send()`
        await self.send(
            build(
                "NOTICE",
                [
                    target,
                    f"unpiping {source}. please destroy this channel manually",
                ],
            )
        )
        await self.send(build("PART", [target]))
        return []

    async def cmd_pipes(self, channel: str, sargs: str) -> Sequence[str]:
        pipes = list(await self._database.get_pipes())
        pipes.sort()
        colmax = max([len(s) for s, t in pipes] or [0])
        return [f"{s.rjust(colmax)} -> {t}" for s, t in pipes] or ["no pipes"]


# channel activity reading head
class ReadServer(BaseServer):
    def __init__(self, bot: BaseBot, name: str, config: Config, database: Database):
        super().__init__(bot, name)
        self.bot: BaseBot

        self._config = config
        self._database = database

        self.channel_map: Dict[str, str] = {}

        # if we're handling a QUIT, the user will be gone from self.users
        # before we have a chance to accurately log a line. hold on to
        # self.users manually so we can see what self.users was before the
        # quit we're currently handling
        self._last_users = self.users.copy()

    def set_throttle(self, rate: int, time: float) -> None:
        # turn off throttling
        pass

    def line_preread(self, line: Line) -> None:
        print(f"r< {line.format()}")

    def line_presend(self, line: Line) -> None:
        print(f"r> {line.format()}")

    async def _backlog_channel(self, source: str, out: str) -> None:
        source_fold = self.casefold(source)
        # should we be logging this channel?
        if not source_fold in self.channel_map:
            # no
            return

        if not source_fold in BACKLOG:
            BACKLOG[source_fold] = deque()

        backlog = BACKLOG[source_fold]
        backlog.append(out)
        if len(backlog) > BACKLOG_MAX:
            # max backlog reached, oldest is leftmost
            backlog.popleft()

        # while we're here, is the write head currently connected?
        write_server = self.bot.servers.get("write", None)
        if write_server is not None:
            # write head is connected. write this log line to this logged channel's
            # log output channel
            await cast(WriteServer, write_server).print_backlog(source_fold, out)

    # some events want to be logged in every channel we share with a user
    # e.g. NICK, QUIT, etc
    async def _backlog_server(self, nickname: str, out: str) -> None:
        user = self._last_users.get(self.casefold(nickname), None)
        if user is None:
            return

        # n.b. user.channels and self.channels already have their keys casefolded
        common_channels = set(self.channels) & set(user.channels)
        for channel_name in common_channels:
            await self._backlog_channel(channel_name, out)

    async def line_read(self, line: Line) -> None:
        now = time.monotonic()

        if line.command == RPL_WELCOME:
            for source, target in await self._database.get_pipes():
                self.channel_map[source] = target
                await self.send(build("JOIN", [source]))

        elif line.command == "PRIVMSG":
            formatted = mformat.privmsg(self, line)
            await self._backlog_channel(line.params[0], formatted)

        elif line.command == "MODE":
            formatted = mformat.mode(line)
            await self._backlog_channel(line.params[0], formatted)

        elif line.command == "JOIN":
            formatted = mformat.join(line)
            await self._backlog_channel(line.params[0], formatted)

        elif line.command == "PART":
            formatted = mformat.part(line)
            await self._backlog_channel(line.params[0], formatted)

        elif line.command == "NICK":
            formatted = mformat.nick(line)
            await self._backlog_server(line.hostmask.nickname, formatted)

        elif line.command == "QUIT":
            formatted = mformat.quit(line)
            await self._backlog_server(line.hostmask.nickname, formatted)

        self._last_users = self.users.copy()


class Bot(BaseBot):
    def __init__(self, config: Config, database: Database):
        super().__init__()
        self._config = config
        self._database = database

    def create_server(self, name: str) -> BaseServer:
        if name == "write":
            return WriteServer(self, name, self._config, self._database)
        else:
            return ReadServer(self, name, self._config, self._database)
