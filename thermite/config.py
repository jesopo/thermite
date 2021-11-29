from dataclasses import dataclass
from typing import Optional

import yaml


@dataclass
class Config(object):
    server: str
    password: str
    channel: str

    db_user: str
    db_pass: Optional[str]
    db_host: Optional[str]
    db_name: str

    pipe_name: str
    make_pipe: str


def load(filepath: str):
    with open(filepath) as file:
        config_yaml = yaml.safe_load(file.read())

    return Config(
        config_yaml["server"],
        config_yaml["password"],
        config_yaml["channel"],
        config_yaml["database"]["user"],
        config_yaml["database"].get("pass", None),
        config_yaml["database"].get("host", None),
        config_yaml["database"]["name"],
        config_yaml["pipe-name"],
        config_yaml["make-pipe"],
    )
