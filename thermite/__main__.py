import asyncio
from argparse import ArgumentParser

from ircrobots import ConnectionParams
from ircrobots.security import TLSVerifySHA512

from . import Bot
from .config import Config, load as config_load
from .database import Database


async def main(config: Config):
    bot = Bot(
        config,
        await Database.connect(
            config.db_user, config.db_pass, config.db_host, config.db_name
        ),
    )

    write_params = ConnectionParams.from_hoststring(
        config.nickname, config.server_write
    )
    write_params.password = config.password_write
    write_params.autojoin = [config.channel]
    # this one goes to znc so nickname doesn't matter
    read_params = ConnectionParams.from_hoststring("thermite", config.server_read)
    read_params.password = config.password_read
    if config.pinned_cert is not None:
        read_params.tls = TLSVerifySHA512(config.pinned_cert)

    await bot.add_server("write", write_params)
    await bot.add_server("read", read_params)
    await bot.run()


if __name__ == "__main__":
    parser = ArgumentParser()
    parser.add_argument("config")
    args = parser.parse_args()

    config = config_load(args.config)
    asyncio.run(main(config))
