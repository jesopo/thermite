import asyncio
from argparse import ArgumentParser

from ircrobots import ConnectionParams

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

    params = ConnectionParams.from_hoststring("thermite", config.server)
    params.password = config.password
    params.autojoin = [config.channel]

    await bot.add_server("irc", params)
    await bot.run()


if __name__ == "__main__":
    parser = ArgumentParser()
    parser.add_argument("config")
    args = parser.parse_args()

    config = config_load(args.config)
    asyncio.run(main(config))
