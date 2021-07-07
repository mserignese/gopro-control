from __future__ import annotations
from typing import Sequence

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

CONFIG_FILE = 'gpc.conf'
DEBUG = False

class GoPro:
    udp_port = 8554

    def __init__(self, config: configparser.ConfigParser) -> None:
        self.ap_ssid = config['gopro']['ap_ssid']
        self.ap_password = config['gopro']['ap_password']
        self.ip_address = config['gopro']['ip_address']
        self.mac_address = config['gopro']['mac_address']
        self.keepalive_period = config['gopro'].getint('keepalive_period')

@enum.unique
class CommandEnum(enum.Enum):
    DEFAULT_BOOT_MODE = 'default_boot_mode'
    DISPLAY_ON = 'display_on'
    DISPLAY_OFF = 'display_off'
    GET_INFO = 'get_info'
    GET_STATUS = 'get_status'
    GET_BATTERY_LEVEL = 'get_battery_level'
    POWER_OFF = 'power_off'
    RECORD_START = 'record_start'
    RECORD_STOP = 'record_stop'
    STREAM = 'stream'
    STREAM_BITRATE = 'stream_bitrate' 
    STREAM_RESOLUTION = 'stream_resolution'
    VIDEO_RESOLUTION = 'video_resolution'
    WAKE = 'wake'
    ZOOM = 'zoom'

class Command:
    definitions = {
            CommandEnum.DEFAULT_BOOT_MODE: {'arity': 1, 'template': '/setting/53/{}', 'mapping': {'video': '0', 'photo': '1', 'multishot': '2'}},
            CommandEnum.DISPLAY_ON: {'arity': 0, 'template': '/setting/58/1'},
            CommandEnum.DISPLAY_OFF: {'arity': 0, 'template': '/setting/58/0'},
            CommandEnum.GET_INFO: {'arity': 0, 'template': ''},
            CommandEnum.GET_STATUS: {'arity': 0, 'template': '/status'},
            CommandEnum.GET_BATTERY_LEVEL: {'arity': 0, 'template': '/status'},
            CommandEnum.POWER_OFF: {'arity': 0, 'template': '/command/system/sleep'},
            CommandEnum.RECORD_START: {'arity': 0, 'template': '/command/shutter?p=1'},
            CommandEnum.RECORD_STOP: {'arity': 0, 'template': '/command/shutter?p=0'},
            CommandEnum.STREAM: {'arity': 0, 'template': '/execute?p1=gpStream&a1=proto_v2&c1=restart'},
            CommandEnum.STREAM_BITRATE: {'arity': 1, 'template': '/setting/62/{}'},
            CommandEnum.STREAM_RESOLUTION: {'arity': 1, 'template': '/setting/64/{}', 'mapping': {'720p': '7', '480p': '4', '240p': '1'}},
            CommandEnum.VIDEO_RESOLUTION: {'arity': 1, 'template': '/setting/2/{}', 'mapping': {'4k': '1', '1440p': '7', '1080p': '9', '720p': '12'}},
            CommandEnum.WAKE: {'arity': 0, 'template': ''},
            CommandEnum.ZOOM: {'arity' : 1,  'template': '/command/digital_zoom?range_pcnt={}'},
    }

class Message:
    def __init__(self, command: CommandEnum, args: Sequence[str] = []) -> None:
        self.command = command
        self.args = args

    @classmethod
    def from_text(cls: Message, message_text: str) -> Message:
        command = None
        for com, definition in Command.definitions.items():
            if com.value == message_text[0]:
                command = com
        if command is None:
            raise ValueError(f'Command "{message_text[0]}" does not exist.')

        definition = Command.definitions[command]
        arity = definition['arity']
        if len(message_text[1:]) != arity:
            raise ValueError(f'{self.command.value} takes {arity} argument(s); got {len(message_text[1:])}.')
        args = message_text[1 : arity + 1]
        if 'mapping' in definition:
            mapping = definition['mapping'] 
            for i, arg in enumerate(args):
                if arg not in mapping:
                    raise ValueError(f'{self.command.value}: unknown argument "{arg}".')
                args[i] = mapping[arg]
        return cls(command, args)

    def send_to(self, gopro: GoPro) -> str:
        if self.command == CommandEnum.WAKE:
            debug_print(f'WOL {gopro.mac_address}')
            send_wake_on_lan(gopro)
            return ''
        if self.command == CommandEnum.GET_BATTERY_LEVEL:
            return Message(CommandEnum.GET_STATUS).send_to(gopro).json()['status']['2']
        else:
            debug_print("GET " + self._build_url(gopro))
            reply = requests.get(self._build_url(gopro))
            if self.command == CommandEnum.STREAM:
                if reply.status_code == 200:
                    reply = '1'
                else:
                    reply = '0'
            return reply

    def _build_url(self, gopro: GoPro) -> str:
        return f'http://{gopro.ip_address}/gp/gpControl{Command.definitions[self.command]["template"].format(*self.args)}'

    def __repr__(self) -> str:
        return f'{self.command} {self.args}' 

def main() -> int:
    config = configparser.ConfigParser()
    try:
        with open(CONFIG_FILE, "r") as config_file:
            config.read_file(config_file)
    except IOError:
        debug_print(f"{CONFIG_FILE}: configuration file not found.")
        sys.exit(1)
    if config['gpc'].getboolean('debug', fallback=False):
        enable_debug()

    gopro = GoPro(config)
    send_wake_on_lan(gopro)
    keepalive_thread = threading.Thread(target=keepalive, args=(gopro,), daemon=True)
    keepalive_thread.start()
    if debug_on(): 
        gopro_info = Message(CommandEnum.GET_INFO).send_to(gopro).json(strict=False)['info']
        gopro_battery_level = Message(CommandEnum.GET_BATTERY_LEVEL).send_to(gopro)
        debug_print(f"Model:\t\t\t{gopro_info['model_name']} (model {gopro_info['model_number']})")
        debug_print(f"Firmware:\t\t{gopro_info['firmware_version']}")
        debug_print(f"Serial:\t\t\t{gopro_info['serial_number']}")
        debug_print(f"AP SSID:\t\t{gopro_info['ap_ssid']}")
        debug_print(f"AP MAC:\t\t\t{gopro_info['ap_mac']}")
        debug_print(f"Battery level:\t\t{gopro_battery_level}")

    for line in sys.stdin:
        command_text = line.strip()
        try:
            message = Message.from_text(command_text.split(' '))
        except ValueError as e:
            debug_print(f'Error for "{command_text}": {e}')
            continue
        reply = message.send_to(gopro)
        if reply:
            print(reply)

        if message.command == CommandEnum.STREAM:
            subprocess.run([f'{config["gpc"]["mpv-path"]}', '--profile=low-latency', f'udp://{gopro.ip_address}:{gopro.udp_port}'])

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

def enable_debug() -> None:
    global DEBUG
    DEBUG = True

def debug_on() -> bool:
    return DEBUG

def debug_print(message) -> None:
    if debug_on():
        print(message, file=sys.stderr)

def signal_quit(signal, frame) -> None:
    sys.exit(0)

if __name__ == '__main__':
    signal.signal(signal.SIGINT, signal_quit)
    main()
