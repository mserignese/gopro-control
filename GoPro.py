import configparser
import distutils
import enum
import json
import re
import requests
import sys
import socket
import subprocess
import signal
import threading 
import time

from typing import Sequence

CONFIG_FILE = 'gpd.conf'
DEBUG = False

class GoPro:
    udp_port = 8554

    def __init__(self, config: configparser.ConfigParser) -> None:
        self.ip_address = config['gopro']['ip_address']
        self.mac_address = config['gopro']['mac_address']
        self.keepalive_period = config['gopro'].getint('keepalive_period')
        self.url = 'http://' + self.ip_address + '/gp/gpControl/'

# TODO: implement WOL as a repeatable command

class Command:
    @enum.unique
    class CommandEnum(enum.Enum):
        WAKE = 'wake'
        RECORD_START = 'record_start'
        RECORD_STOP = 'record_stop'
        VIDEO_RESOLUTION = 'video_resolution'
        ZOOM = 'zoom'
        POWER_OFF = 'power_off'
        DISPLAY_ON = 'display_on'
        DISPLAY_OFF = 'display_off'
        DEFAULT_BOOT_MODE = 'default_boot_mode'
        GET_BATTERY_LIFE = 'get_battery_life'
        BITRATE = 'bitrate' 

    definitions = {
            CommandEnum.POWER_OFF: {'arity': 0, 'template': 'command/system/sleep'},
            CommandEnum.ZOOM: {'arity' : 1,  'template': 'command/digital_zoom?range_pcnt={}'},
            CommandEnum.VIDEO_RESOLUTION: {'arity': 1, 'template': 'setting/2/{}', 'mapping': {'4k': '1', '1440p': '7', '1080p': '9', '720p': '12'}},
            CommandEnum.DEFAULT_BOOT_MODE: {'arity': 1, 'template': 'setting/53/{}', 'mapping': {'video': '0', 'photo': '1', 'multishot': '2'}},
            CommandEnum.BITRATE: {'arity': 1, 'template': 'setting/62/{}'},
            CommandEnum.RECORD_START: {'arity': 0, 'template': 'command/shutter?p=1'},
            CommandEnum.RECORD_STOP: {'arity': 0, 'template': 'command/shutter?p=0'},
            CommandEnum.DISPLAY_ON: {'arity': 0, 'template': 'setting/58/1'},
            CommandEnum.DISPLAY_OFF: {'arity': 0, 'template': 'setting/58/0'},
            CommandEnum.GET_BATTERY_LIFE: {'arity': 0, 'template': 'setting/58/0'},
    }

# TODO: implement return values for commands like GET_BATTERY_LIFE

class Message:
    def __init__(self, body: Sequence[str]) -> None:
        self.command = None
        for command, definition in Command.definitions.items():
            if command.value == body[0]:
                self.command = command
        if self.command is None:
            raise ValueError(f'Command "{body[0]}" does not exist.')

        definition = Command.definitions[self.command]
        arity = definition['arity']
        if len(body[1:]) != arity:
            raise ValueError(f'{self.get_identifier()} takes {arity} argument(s); got {len(body[1:])}.')
        self.args = body[1 : arity + 1]
        if 'mapping' in definition:
            mapping = definition['mapping'] 
            for i, arg in enumerate(self.args):
                if arg not in mapping:
                    raise ValueError(f'{self.get_identifier()}: unknown argument "{arg}".')
                self.args[i] = mapping[arg]

    def get_identifier(self) -> str:
        return Command.definitions[self.kind]['identifier'] 

    def send_to(self, gopro: GoPro) -> None:
        debug_print("GET " + self._build_url(gopro))
        requests.get(self._build_url(gopro))

    def _build_url(self, gopro: GoPro) -> str:
        return f'{gopro.url}{Command.definitions[self.command]["template"].format(*self.args)}'

    def __repr__(self):
        return f'{self.command} {self.args}' 

def main() -> int:
    config = configparser.ConfigParser()
    try:
        with open(CONFIG_FILE, "r") as config_file:
            config.read_file(config_file)
    except IOError:
        debug_print(f"{CONFIG_FILE}: configuration file not found.")
        sys.exit(1)
    if config['gpd'].getboolean('debug', fallback=False):
        enable_debug()

    gopro = GoPro(config)
    send_wake_on_lan(gopro)
    keepalive_thread = threading.Thread(target=keepalive, args=(gopro,), daemon=True)
    keepalive_thread.start()
#        if debug_on(): 
#            gopro_info = requests.get(self.control_url).json(strict=False)["info"]
#            print(gopro_info["model_name"])
#            print(gopro_info["firmware_version"])

    for line in sys.stdin:
        command_text = line.strip()
        try:
            message = Message(command_text.split(' '))
        except ValueError as e:
            debug_print(f'Error for "{command_text}": {e}')
            continue
        message.send_to(gopro)

    sys.exit(0)

def keepalive(gopro: GoPro) -> None:
    while True:
        keepalive_payload = "_GPHD_:0:0:2:0.000000\n".encode()
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.sendto(keepalive_payload, (gopro.ip_address, gopro.udp_port))
        time.sleep(gopro.keepalive_period / 1000)

def send_wake_on_lan(gopro: GoPro) -> None:
    GOPRO_WAKE_ON_LAN_PORT = 9
    hex_message = f'FFFFFFFFFFFF{gopro.mac_address * 16}'
    payload = bytes.fromhex(hex_message)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.sendto(payload, (gopro.ip_address, GOPRO_WAKE_ON_LAN_PORT))

def enable_debug():
    global DEBUG
    DEBUG = True

def debug_on():
    return DEBUG

def debug_print(message):
    if debug_on():
        print(message, file=sys.stderr)

def signal_quit(signal, frame):
    sys.exit(0)

if __name__ == '__main__':
    signal.signal(signal.SIGINT, signal_quit)
    main()
