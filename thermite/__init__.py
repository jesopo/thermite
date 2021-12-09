import time
from collections import deque
from random import choice as random_choice
from string import hexdigits
from typing import Deque, Dict, Sequence

from irctokens import build, Line
from ircrobots import Bot as BaseBot
from ircrobots import Server as BaseServer

from ircstates.numerics import RPL_WELCOME, RPL_CREATIONTIME, ERR_NOSUCHCHANNEL
from ircrobots.ircv3 import Capability
from ircrobots.matching import Folded, Response, SELF

from .config import Config
from .database import Database

CAP_OPER = Capability(None, "solanum.chat/oper")


class Server(BaseServer):
    def __init__(self, bot: BaseBot, name: str, config: Config, database: Database):

        super().__init__(bot, name)
        self.desired_caps.add(CAP_OPER)

        self._config = config
        self._database = database

        self._source_map: Dict[str, str] = {}
        self._target_map: Dict[str, str] = {}
        self._source_log: Dict[str, Deque[str]] = {}

        self._last_users = self.users.copy()

    def set_throttle(self, rate: int, time: float):
        # turn off throttling
        pass

    async def _send_log(self, out: str):
        await self.send(build("NOTICE", [self._config.channel, out]))

    async def _send_source_log(self, source: str, out: str):
        target = self._source_map[source]
        target_users = {
            self.users[n] for n in self.channels[self.casefold(target)].users.keys()
        }
        for target_user in list(target_users):
            # TODO: don't hardcode services.libera.chat
            if (
                target_user.hostname == "services.libera.chat"
                or target_user.nickname == self.nickname
            ):
                target_users.remove(target_user)

        if target_users:
            await self.send(build("NOTICE", [target, out]))
        else:
            if not source in self._source_log:
                self._source_log[source] = deque()
            self._source_log[source].append(out)

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
            and self._target_map[line.params[0]] in self._source_log
        ):
            # someone joined a target channel and we've buffered up source
            # logs because the target channel was thus far empty
            source = self._target_map[line.params[0]]
            while self._source_log[source]:
                await self._send_source_log(source, self._source_log[source].popleft())
            del self._source_log[source]

        elif (
            line.command in {"PRIVMSG", "NOTICE"}
            and line.source is not None
            and line.params[0] in self._source_map
        ):
            source = line.params[0]
            message = line.params[1]
            if line.command == "NOTICE":
                who_str = f"-{line.hostmask.nickname}-"
            elif not message.startswith("\x01"):
                who_str = f"<{line.hostmask.nickname}>"
            elif message.startswith("\x01ACTION "):
                # /me
                who_str = f"* {line.hostmask.nickname}"
                message = message.strip("\x01").split(" ", 1)[1]
            else:
                who_str = f"- {line.hostmask.nickname}"
                message = message.strip("\x01")
                message = f"CTCP {message}"

            await self._send_source_log(source, f"{who_str} {message}")

        elif line.command == "MODE" and line.params[0] in self._source_map:
            source = line.params[0]
            args = " ".join(line.params[2:])
            await self._send_source_log(
                source, f"- {line.source} set mode {line.params[1]} {args}"
            )

        elif line.command in {"JOIN", "PART"} and line.params[0] in self._source_map:
            source = line.params[0]
            await self._send_source_log(
                source,
                f"- {line.source} {line.command.lower()}ed {source}",
            )

        elif line.command == "NICK" and (
            common := set(self._source_map)
            & self.users[line.hostmask.nickname].channels
        ):
            message = f"- {line.source} changed nick to {line.params[0]}"
            for chan in common:
                await self._send_source_log(chan, message)

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
                await self._send_source_log(chan, message)

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
