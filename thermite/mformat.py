from irctokens import Line
from ircstates import Server


def _status(server: Server, channel_name: str, nickname: str) -> str:
    channel = server.channels[server.casefold(channel_name)]
    cuser = channel.users[server.casefold(nickname)]

    status = ""
    # iterate isupport because that's held in order of precedence
    for i, mode in enumerate(server.isupport.prefix.modes):
        if mode in cuser.modes:
            status += server.isupport.prefix.prefixes[i]
    return status


def privmsg(server: Server, line: Line) -> str:
    nick = line.hostmask.nickname
    status = _status(server, line.params[0], nick)
    message = line.params[1]

    if message.startswith("\x01ACTION "):
        message = message.split(" ", 1)[1].rstrip("\x01")
        return f"* {status}{nick} {message}"
    elif message.startswith("\x01"):
        message = message.strip("\x01")
        return f"- {status}{nick} sent CTCP request: {message}"
    else:
        return f"<{status}{nick}> {message}"


def notice(server: Server, line: Line) -> str:
    nick = line.hostmask.nickname
    status = _status(server, line.params[0], nick)
    message = line.params[1]

    if message.startswith("\x01"):
        message = message.strip("\x01")
        return f"- {status}{nick} sent CTCP response: {message}"
    else:
        return f"-{status}{nick}- {message}"


def quit(line: Line) -> str:
    reason = (line.params[0:] or [""])[0]
    return f"- {line.hostmask.nickname} quit ({reason})"


def part(line: Line) -> str:
    reason = (line.params[1:] or [""])[0]
    return f"- {line.hostmask.nickname} parted {line.params[0]} ({reason})"


def join(line: Line) -> str:
    # TODO: handle extended-join data
    return f"- {line.hostmask.nickname} joined {line.params[0]}"


def nick(line: Line) -> str:
    return f"- {line.hostmask.nickname} changed nick to {line.params[0]}"


def mode(line: Line) -> str:
    args = " ".join(line.params[2:])
    return f"- {line.hostmask.nickname} set mode {line.params[1]} {args}"
