import time
from collections import deque
from random import choice as random_choice
from string import hexdigits
from typing import Deque, Dict, Sequence, Set

from irctokens import build, Line
from ircstates import User
from ircrobots import Bot as BaseBot
from ircrobots import Server as BaseServer

from ircstates.numerics import RPL_WELCOME, RPL_CREATIONTIME, ERR_NOSUCHCHANNEL
from ircrobots.ircv3 import Capability
from ircrobots.matching import Folded, Response, SELF

from .config import Config
from .database import Database

CAP_OPER = Capability(None, "solanum.chat/oper")
BACKLOG_MAX = 64


class Server(BaseServer):
    def __init__(self, bot: BaseBot, name: str, config: Config, database: Database):

        super().__init__(bot, name)
        self.desired_caps.add(CAP_OPER)

        self._config = config
        self._database = database

        self._source_map: Dict[str, str] = {}
        self._target_map: Dict[str, str] = {}
        self._backlog: Dict[str, Deque[str]] = {}

        self._last_users = self.users.copy()

    def set_throttle(self, rate: int, time: float):
        # turn off throttling
        pass

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

    async def _send_log(self, out: str):
        await self.send(build("NOTICE", [self._config.channel, out]))

    async def _log_backlog(self, target: str, out: str):
        offset = len(f":{self.hostmask()} NOTICE {target} :")
        while out:
            out_take = out[: 510 - offset]
            out = out[len(out_take) :]
            await self.send(build("NOTICE", [target, out_take]))

    async def _add_backlog(self, source: str, out: str):
        if not source in self._backlog:
            self._backlog[source] = deque()

        self._backlog[source].append(out)
        if len(self._backlog[source]) > BACKLOG_MAX:
            self._backlog[source].popleft()

        target = self._source_map[source]
        if self._human_users(target):
            await self._log_backlog(target, out)

    async def line_read(self, line: Line):
        now = time.monotonic()

        # if we're handling a QUIT, the user will be gone from self.users
        # before we have a chance to accurately log it. hold on to self.users
        # manually so we can see what self.users was before the quit we're
        # currently handling
        last_users = self._last_users
        self._last_users = self.users.copy()

        if line.command == RPL_WELCOME:
            for source, target in await self._database.get_pipes():
                self._source_map[source] = target
                self._target_map[target] = source
                await self.send(build("JOIN", [f"{target},{source}"]))
        elif (
            line.command == "PRIVMSG"
            and line.source is not None
            and not self.is_me(line.hostmask.nickname)
            and (
                line.params[0] in self._target_map
                or line.params[0] == self._config.channel
            )
        ):
            # commands

            me = self.nickname
            who = line.hostmask.nickname

            first, _, rest = line.params[1].partition(" ")
            if first in [me, f"{me}:", f"{me},"] and rest:
                # highlight in channel
                command, _, args = rest.partition(" ")
                await self.cmd(line.params[0], command.lower(), args)

        elif (
            line.command == "JOIN"
            and line.params[0] in self._target_map
            and len(self._human_users(line.params[0])) == 1
        ):
            # target channel was empty until this join and we have a backlog.
            # replay it for the joining user
            target = line.params[0]
            source = self._target_map[target]
            for out in self._backlog[source]:
                await self._log_backlog(target, out)

        elif (
            line.command in {"PRIVMSG", "NOTICE"}
            and line.source is not None
            and line.params[0] in self._source_map
        ):
            source = line.params[0]
            cuser = self.channels[self.casefold(source)].users[
                self.casefold(line.hostmask.nickname)
            ]

            status = ""
            for mode in cuser.modes:
                status += self.isupport.prefix.from_mode(mode) or ""

            message = line.params[1]
            if line.command == "NOTICE":
                who_str = f"-{status}{line.hostmask.nickname}-"
            elif not message.startswith("\x01"):
                who_str = f"<{status}{line.hostmask.nickname}>"
            elif message.startswith("\x01ACTION "):
                # /me
                who_str = f"* {status}{line.hostmask.nickname}"
                message = message.strip("\x01").split(" ", 1)[1]
            else:
                who_str = f"- {status}{line.hostmask.nickname}"
                message = message.strip("\x01")
                message = f"CTCP {message}"

            await self._add_backlog(source, f"{who_str} {message}")

        elif line.command == "MODE" and line.params[0] in self._source_map:
            source = line.params[0]
            args = " ".join(line.params[2:])
            await self._add_backlog(
                source, f"- {line.source} set mode {line.params[1]} {args}"
            )

        elif line.command in {"JOIN", "PART"} and line.params[0] in self._source_map:
            source = line.params[0]
            await self._add_backlog(
                source,
                f"- {line.source} {line.command.lower()}ed {source}",
            )

        elif line.command == "NICK" and (
            common := set(self._source_map)
            & self.users[line.hostmask.nickname].channels
        ):
            message = f"- {line.source} changed nick to {line.params[0]}"
            for chan in common:
                await self._add_backlog(chan, message)

        elif (
            line.command == "QUIT"
            and line.hostmask.nickname in last_users
            and (
                common := set(self._source_map)
                & last_users[line.hostmask.nickname].channels
            )
        ):
            message = f"- {line.source} quit"
            if len(line.params) > 0:
                message += f" ({line.params[0]})"
            for chan in sorted(common):
                await self._add_backlog(chan, message)

    async def cmd(
        self,
        channel: str,
        command: str,
        args: str,
    ):

        attrib = f"cmd_{command}"
        if hasattr(self, attrib):
            outs = await getattr(self, attrib)(channel, args)
            for out in outs:
                await self.send(build("NOTICE", [channel, out]))

    async def cmd_say(self, channel: str, sargs: str) -> Sequence[str]:
        if not sargs.strip():
            return ["please provide a message to send"]
        elif not channel in self._target_map:
            return ["this isn't a pipe target channel"]
        else:
            source = self._target_map[channel]
            await self.send(build("PRIVMSG", [source, sargs]))
            return []

    async def cmd_names(self, channel: str, sargs: str) -> Sequence[str]:
        if not channel in self._target_map:
            return ["this isn't a pipe target channel"]
        else:
            source = self._target_map[channel]
            names = self.channels[self.casefold(source)].users.keys()
            return [self.users[n].hostmask() for n in names]

    async def cmd_backlog(self, channel: str, sargs: str) -> Sequence[str]:
        if not channel in self._target_map:
            return ["this isn't a pipe target channel"]
        else:
            source = self._target_map[channel]
            i = 0
            if source in self._backlog:
                for out in self._backlog[source]:
                    i += 1
                    await self._log_backlog(channel, out)

            return [f"replayed {i} lines"]

    def _new_pipe_target(self):
        target = self._config.pipe_name
        while "?" in target:
            target = target.replace("?", random_choice(hexdigits), 1)
        return target

    async def _channel_exists(self, channel: str):
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
        elif not self.is_channel(source := args[0]):
            return [f"'{source}' isn't a valid channel name"]
        else:
            # make sure our randomly generated pipe target isn't already in use
            while await self._channel_exists(target := self._new_pipe_target()):
                pass

            await self.send(build("JOIN", [target]))
            for pipe_line in self._config.make_pipe:
                await self.send_raw(
                    pipe_line.format(TARGET=target, HOSTNAME=self.hostname)
                )
            await self.send(build("JOIN", [source]))

            await self._database.add_pipe(source, target, args[1])
            self._source_map[source] = target
            self._target_map[target] = source
            return [f"piped {source} to {target}"]

    async def cmd_unpipe(self, channel: str, sargs: str) -> Sequence[str]:
        args = sargs.split(None, 1)
        if not channel in self._target_map:
            return ["this isn't a pipe target channel"]
        else:
            target = channel
            source = self._target_map.pop(target)
            del self._source_map[source]
            await self._database.remove_pipe(source)
            await self.send(build("PART", [target]))
            await self._send_log(
                f"unpiped {source}. part it and destroy {target} manually"
            )
            return []

    async def cmd_pipes(self, channel: str, sargs: str) -> Sequence[str]:
        pipes = list(await self._database.get_pipes())
        pipes.sort()
        colmax = max([len(s) for s, t in pipes] or [0])
        return [f"{s.rjust(colmax)} -> {t}" for s, t in pipes] or ["no pipes"]

    def line_preread(self, line: Line):
        print(f"< {line.format()}")

    def line_presend(self, line: Line):
        print(f"> {line.format()}")


class Bot(BaseBot):
    def __init__(self, config: Config, database: Database):
        super().__init__()
        self._config = config
        self._database = database

    def create_server(self, name: str):
        return Server(self, name, self._config, self._database)
