#!/usr/bin/env python2.7
from collections import defaultdict
from datetime import datetime
import json
import os
import random
import time

import dateutil.parser
import requests

from config import LOGIN_CREDS


LOGIN_URL = 'https://home.nest.com/session'
OBJECTS_URL = 'https://transport03-rts06-iad01.transport.home.nest.com/v5/web/{user}?_={epoch_time:1.0f}'
SUBSCRIBE_URL = 'https://transport03-rts06-iad01.transport.home.nest.com/v5/subscribe'

COOKIES = None
AUTH = None
USER = None
USER_ID = None

# Pulled from v.EVENT_TYPE in Nest's main.js
EVENT_TYPES = defaultdict(str, {0: 'Heat', 1: 'Cool', 2: 'Range', 3: 'Away',
                                4: 'Auto-Away', 5: 'Off', 6: 'Emergency Heat',
                                7: 'Sunblock'})

# Pulled from v.TOUCHED_BY in Nest's main.js
TOUCHED_BY = defaultdict(str, {0: 'No one.', 1: 'Learning', 2: 'Local', 3: 'Remote',
                               4: 'Web', 5: 'Android', 6: 'iOS', 7: 'winphone',
                               8: 'Tune-up', 9: 'Demand Response', 10: 'Tou',
                               11: 'Safety Shutoff', 12: 'Programmer'})

# Pulled from v.TOUCHED_WHERE in Nest's main.js
TOUCHED_WHERE = defaultdict(str, {0: 'Unknown', 1: 'Schedule', 2: 'Ad Hoc'})

# Pull from v.WHODUNIT_TYPEMAP in Nest's main.js
WHODUNIT = defaultdict(str, {0: "user", 1: "weather", 2: "away", 3: "auto",
                             4: "tuneup", 5: "auto_dehum", 6: "demand_response",
                             7: "time_of_use"})

# Cycle types pulled definition of _.m in Nest's main.js
# Lookup is the bitwise AND of FAN and the returned type.
CYCLE_TYPE_FAN = 65535
CYCLE_TYPE = {1: 'Heat (1)', 2: 'Heat (2)', 4: 'Auxiliary Heat', 8: 'Heat (3)',
              16: 'Emergency Heat', 32: 'Heat Alternate (1)', 64: 'Heat Alternate (2)',
              256: 'Cool (1)', 512: 'Cool (2)', 1024: 'Airwaive', 16384: 'Humidifier',
              32768: 'Dehumidifier', CYCLE_TYPE_FAN: 'Fan'}

# From Nest's main.js: s.sessionID=i+"."+String(Math.random()).substr(2,5)+"."+Date.now()
SESSION = '{user_id}.{rand_numb}.{epoch_time:1.0f}'

# From Nest's main.js s.SUBSCRIBE_TIMEOUT=850+Math.round(250*Math.random())
SUBSCRIBE_TIMEOUT = 850 + round(250 * random.random())

def requires_auth(func):

    def wrapper(*args, **kwargs):
        if not AUTH:
            raise UserWarning('An Authorization token is required to use this function.')
        else:
            return func(*args, **kwargs)

    return wrapper

def login():
    '''
    Log in to Nest.
    '''
    # Log in
    login_resp = requests.post(LOGIN_URL, json=LOGIN_CREDS)
    login_resp_content = json.loads(login_resp.content)

    global COOKIE, AUTH, USER, USER_ID, SESSION
    COOKIE = login_resp.cookies
    AUTH = 'Basic {}'.format(login_resp_content['access_token'])
    USER = login_resp_content['user']
    USER_ID = login_resp_content['userid']
    SESSION = SESSION.format(user_id=USER_ID,
                                    rand_numb=str(random.random())[2:7],
                                    epoch_time=time.time())

@requires_auth
def get_objects():
    '''
    '''
    objects_url = OBJECTS_URL.format(epoch_time=time.time(), user=USER)
    objects_resp = requests.get(objects_url, headers={'Authorization': AUTH})
    return json.loads(objects_resp.content)

@requires_auth
def get_energy_history(objects):
    '''
    '''
    subscribe_objects = []
    for obj in objects['objects']:
        subscribe_object = {}
        for key in ('object_key', 'object_revision', 'object_timestamp'):
            subscribe_object[key] = obj[key]

        subscribe_objects.append(subscribe_object)

    serial_numb = subscribe_objects[1]['object_key'].split('.')[1]
    subscribe_objects.append({'object_key': 'energy_latest.{serial_numb}'.format(serial_numb=serial_numb)})
    subscribe_json = {'objects': subscribe_objects,
                      'session': SESSION,
                      'timeout': SUBSCRIBE_TIMEOUT}

    subscribe_resp = requests.post(SUBSCRIBE_URL, json=subscribe_json,
                                  headers={'Authorization': AUTH},
                                  cookies=COOKIES)

    return json.loads(subscribe_resp.content)

def cycle_type(type_code):
    '''
    '''
    return CYCLE_TYPE[CYCLE_TYPE_FAN & type_code]

def process_energy_history(history):
    '''
    '''
    for day in history['objects'][0]['value']['days']:
        cycles = day['cycles']
        events = day['events']
        date = dateutil.parser.parse(day['day'])

        print("Processing cycles for {:%m/%d/%Y}".format(date))
        process_energy_history_cycles(date, cycles)

        print("Processing events for {:%m/%d/%Y}".format(date))
        process_energy_history_events(date, events)


def process_energy_history_events(date, events):
    '''
    '''
    since_epoch = time.mktime(date.timetuple())
    total_time = defaultdict(int)
    for event in events:
        time_format = '%a, %m/%d/%Y %H:%M:%S'
        tz_offset = -18000

        if 'start' in event:
            start_time = time.gmtime(since_epoch + event['start'] + tz_offset)
            end_time = time.gmtime(since_epoch + event['end'] + tz_offset)
            event_time = None
            when = "from {} - {}".format(time.strftime(time_format, start_time),
                                         time.strftime(time_format, end_time))
        else:
            start_time, end_time = None, None
            event_time = time.gmtime(event['touched_when'] + tz_offset)
            when = "at {}".format(time.strftime(time_format, event_time))

        details = {'event_type': EVENT_TYPES[event.get('type')],
                   'event_time': event_time,
                   'start_time': start_time,
                   'end_time': end_time,
                   'touched_by': TOUCHED_BY[event.get('touched_by')],
                   'touched_where': TOUCHED_WHERE[event.get('touched_where')],
                   'continuation': event.get('continuation', False),
                   'when': when,
                   'heat_temp': celsius_to_fahrenheit(event.get('heat_temp', 0)),
                   'cool_temp': celsius_to_fahrenheit(event.get('cool_temp', 0))

                  }

        print("A(n) '{event_type}' event occurred {when} due to '{touched_by}' from '{touched_where}', "
              "changing the temperature range to {heat_temp} (heat) and {cool_temp} (cool)'".format(**details))

def process_energy_history_cycles(date, cycles):
    '''
    '''
    since_epoch = time.mktime(date.timetuple())
    total_time = defaultdict(int)
    for cycle in cycles:
        time_format = '%a, %m/%d/%Y %H:%M:%S'
        tz_offset = -18000
        start_time = since_epoch + cycle['start']
        end_time = since_epoch + cycle['start'] + cycle['duration']
        details = {'cycle_type': cycle_type(cycle['type']),
                   'start_time': time.strftime(time_format, time.gmtime(start_time + tz_offset)),
                   'end_time': time.strftime(time_format, time.gmtime(end_time + tz_offset)),
                   'duration': cycle['duration']/60.0/60.0
                  }

        total_time[details['cycle_type']] += cycle['duration']

        print('{cycle_type} from {start_time} to {end_time} ({duration:.2f} hours)'.format(**details))

    for cycle, duration in total_time.items():
        duration_in_hours = duration/60.0/60.0
        print('Total time for {cycle_type}: {total_duration:.2f} hours'.format(cycle_type=cycle, total_duration=duration_in_hours))


def save_history(energy_history):
    '''
    '''
    today = datetime.now()

    with open('history.{:%m-%d-%Y}.json'.format(today), 'w+') as history_file:
        history_file.write(json.dumps(energy_history))

def todays_history_file():
    '''
    '''
    today = datetime.now()
    history_file = 'history.{:%m-%d-%Y}.json'.format(today)
    if os.path.exists(history_file):
        with open(history_file, 'r') as history_file:
            return json.load(history_file)
    else:
        return None


# {u'cool_temp': 23.897,                     # Cool to temp.
#  u'end': 31665,                            # <---- Seconds since the beginning of the day.
#  u'event_touched_by': 0,                   # ????
#  u'heat_temp': 20.0,                       # Heat to temp.
#  u'start': 31665,                          # <--- Seconds since the beginning of the day.
#  u'touched_by': 2, 	                     # <--- Who did it?
#  u'touched_timezone_offset': -18000,       # TZ offset
#  u'touched_when': 1432216065,              # Exact time of change (seconds since Epoch)
#  u'touched_where': 2,                      # ????
#  u'type': 2}                               # ??? Away, Auto Away, Home??

def celsius_to_fahrenheit(celsius):
    return celsius * 9 / 5 + 32

if __name__ == '__main__':
    energy_history = todays_history_file()
    if not energy_history:
        login()
        objects = get_objects()
        energy_history = get_energy_history(objects)

    process_energy_history(energy_history)
    save_history(energy_history)

    print('Done!')
