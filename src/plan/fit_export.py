"""
Minimal FIT encoder for Garmin-style "power courses".

A power course is an ordinary FIT course file (GPS track + course points)
where each course point's *name* holds the target power in watts as a string.
Garmin head units display that name as you ride, giving a turn-by-turn power
plan. This mirrors the structure of the example file in ``tests/``.
"""

import datetime
import os
import struct

import numpy as np

# FIT timestamps count seconds since this epoch (UTC).
FIT_EPOCH = datetime.datetime(1989, 12, 31, 0, 0, 0, tzinfo=datetime.timezone.utc)

# Base type identifiers (FIT SDK).
_ENUM = 0x00
_UINT8 = 0x02
_UINT16 = 0x84
_SINT32 = 0x85
_UINT32 = 0x86
_STRING = 0x07

_BASE_SIZE = {_ENUM: 1, _UINT8: 1, _UINT16: 2, _SINT32: 4, _UINT32: 4}

# Global message numbers.
_MSG_FILE_ID = 0
_MSG_FILE_CREATOR = 49
_MSG_EVENT = 21
_MSG_RECORD = 20
_MSG_LAP = 19
_MSG_COURSE = 31
_MSG_COURSE_POINT = 32

# Semicircles <-> degrees (FIT stores lat/lon as int32 semicircles).
_DEG_TO_SEMICIRCLE = (2 ** 31) / 180.0

_COURSE_NAME_LEN = 24
_CP_NAME_LEN = 8
_MAX_RECORDS = 3000

# FIT 16-bit CRC lookup table.
_CRC_TABLE = [
    0x0000, 0xCC01, 0xD801, 0x1400, 0xF001, 0x3C00, 0x2800, 0xE401,
    0xA001, 0x6C00, 0x7800, 0xB401, 0x5000, 0x9C01, 0x8801, 0x4400,
]


def _fit_crc(data, crc=0):
    for byte in data:
        tmp = _CRC_TABLE[crc & 0xF]
        crc = (crc >> 4) & 0x0FFF
        crc = crc ^ tmp ^ _CRC_TABLE[byte & 0xF]
        tmp = _CRC_TABLE[crc & 0xF]
        crc = (crc >> 4) & 0x0FFF
        crc = crc ^ tmp ^ _CRC_TABLE[(byte >> 4) & 0xF]
    return crc & 0xFFFF


def _semicircles(deg):
    return int(round(float(deg) * _DEG_TO_SEMICIRCLE))


def _encode_value(base_type, value, size):
    if base_type == _STRING:
        raw = str(value).encode('ascii', 'replace')[: size - 1]
        return raw + b'\x00' * (size - len(raw))
    if base_type in (_ENUM, _UINT8):
        return struct.pack('<B', int(value) & 0xFF)
    if base_type == _UINT16:
        return struct.pack('<H', int(value) & 0xFFFF)
    if base_type == _SINT32:
        return struct.pack('<i', int(value))
    if base_type == _UINT32:
        return struct.pack('<I', int(value) & 0xFFFFFFFF)
    raise ValueError(f"Unsupported base type {base_type:#x}")


def _definition(local_type, global_num, fields):
    """fields: list of (field_num, base_type, size)."""
    out = bytearray()
    out.append(0x40 | local_type)   # definition message header
    out.append(0)                   # reserved
    out.append(0)                   # architecture: little-endian
    out += struct.pack('<H', global_num)
    out.append(len(fields))
    for field_num, base_type, size in fields:
        out += struct.pack('<BBB', field_num, size, base_type)
    return bytes(out)


def _data(local_type, fields, values):
    """fields aligned with values; fields: list of (field_num, base_type, size)."""
    out = bytearray()
    out.append(local_type & 0x0F)   # data message header
    for (_, base_type, size), value in zip(fields, values):
        out += _encode_value(base_type, value, size)
    return bytes(out)


def _downsample_indices(n, target):
    if n <= target:
        return list(range(n))
    return sorted(set(np.linspace(0, n - 1, target, dtype=int).tolist()))


def _course_point_plan(distances, latitudes, longitudes, power_sections):
    """Build (distance_m, lat, lon, power_w) tuples at each power-section start."""
    distances = np.asarray(distances, dtype=float)
    lats = np.asarray(latitudes, dtype=float)
    lons = np.asarray(longitudes, dtype=float)
    points = []
    last_d = None
    last_power = None
    for section in power_sections:
        power = section.get('power')
        if power is None:
            continue
        watts = int(round(float(power)))
        d = float(section.get('start_km', 0.0)) * 1000.0
        d = min(max(d, float(distances[0])), float(distances[-1]))
        if last_d is not None and d - last_d < 1.0:
            continue
        # Only mark the course where the target power actually changes.
        if watts == last_power:
            continue
        lat = float(np.interp(d, distances, lats))
        lon = float(np.interp(d, distances, lons))
        points.append((d, lat, lon, watts))
        last_d = d
        last_power = watts
    return points


def write_power_course(path, course_name, distances, latitudes, longitudes,
                       altitudes, power_sections, total_time_s=None):
    """Write a Garmin-style power course FIT file.

    Parameters
    ----------
    path : str
        Output file path.
    course_name : str
        Course name shown on the device.
    distances, latitudes, longitudes, altitudes : sequence of float
        Full-resolution route geometry (distances in metres, cumulative).
    power_sections : list of dict
        Pacing plan sections (each with ``start_km``/``end_km``/``power``).
    total_time_s : float, optional
        Estimated total ride time; defaults to the section sum.
    """
    distances = np.asarray(distances, dtype=float)
    lats = np.asarray(latitudes, dtype=float)
    lons = np.asarray(longitudes, dtype=float)
    alts = np.asarray(altitudes, dtype=float)

    n = min(len(distances), len(lats), len(lons), len(alts))
    if n < 2:
        raise ValueError("Course needs at least two GPS points to export.")
    distances, lats, lons, alts = distances[:n], lats[:n], lons[:n], alts[:n]

    if not power_sections:
        raise ValueError("No pacing plan available to export.")

    # Distance -> cumulative time map from the pacing plan (monotonic).
    sec_d = [float(distances[0])]
    sec_t = [0.0]
    acc = 0.0
    for section in power_sections:
        acc += float(section.get('time_s', 0.0) or 0.0)
        sec_d.append(float(section.get('end_km', 0.0)) * 1000.0)
        sec_t.append(acc)
    if total_time_s is None:
        total_time_s = acc
    if total_time_s <= 0:
        total_time_s = max(1.0, len(distances))

    def time_at(d):
        if sec_t[-1] <= 0:
            frac = (d - distances[0]) / max(1.0, distances[-1] - distances[0])
            return frac * total_time_s
        return float(np.interp(d, sec_d, sec_t))

    base_ts = int((datetime.datetime.now(datetime.timezone.utc) - FIT_EPOCH).total_seconds())

    # ----- field definitions -------------------------------------------------
    file_id_fields = [
        (0, _ENUM, 1),     # type = course (6)
        (1, _UINT16, 2),   # manufacturer = garmin (1)
        (2, _UINT16, 2),   # product
        (4, _UINT32, 4),   # time_created
    ]
    creator_fields = [
        (0, _UINT16, 2),   # software_version
    ]
    course_fields = [
        (5, _STRING, _COURSE_NAME_LEN),  # name
        (4, _ENUM, 1),                   # sport = cycling (2)
    ]
    lap_fields = [
        (253, _UINT32, 4),  # timestamp
        (2, _UINT32, 4),    # start_time
        (3, _SINT32, 4),    # start_position_lat
        (4, _SINT32, 4),    # start_position_long
        (5, _SINT32, 4),    # end_position_lat
        (6, _SINT32, 4),    # end_position_long
        (7, _UINT32, 4),    # total_elapsed_time (s * 1000)
        (8, _UINT32, 4),    # total_timer_time (s * 1000)
        (9, _UINT32, 4),    # total_distance (m * 100)
    ]
    event_fields = [
        (253, _UINT32, 4),  # timestamp
        (0, _ENUM, 1),      # event = timer (0)
        (1, _ENUM, 1),      # event_type
    ]
    record_fields = [
        (253, _UINT32, 4),  # timestamp
        (0, _SINT32, 4),    # position_lat
        (1, _SINT32, 4),    # position_long
        (2, _UINT16, 2),    # altitude ((m + 500) * 5)
        (5, _UINT32, 4),    # distance (m * 100)
    ]
    cp_fields = [
        (254, _UINT16, 2),  # message_index
        (1, _UINT32, 4),    # timestamp
        (2, _SINT32, 4),    # position_lat
        (3, _SINT32, 4),    # position_long
        (4, _UINT32, 4),    # distance (m * 100)
        (5, _ENUM, 1),      # type = generic (0)
        (6, _STRING, _CP_NAME_LEN),  # name = power (watts)
    ]

    body = bytearray()

    # file_id
    body += _definition(0, _MSG_FILE_ID, file_id_fields)
    body += _data(0, file_id_fields, [6, 1, 0, base_ts])
    # file_creator
    body += _definition(1, _MSG_FILE_CREATOR, creator_fields)
    body += _data(1, creator_fields, [100])
    # course
    body += _definition(2, _MSG_COURSE, course_fields)
    body += _data(2, course_fields, [course_name or "Power course", 2])

    # lap
    end_ts = base_ts + int(round(total_time_s))
    body += _definition(3, _MSG_LAP, lap_fields)
    body += _data(3, lap_fields, [
        end_ts,
        base_ts,
        _semicircles(lats[0]), _semicircles(lons[0]),
        _semicircles(lats[-1]), _semicircles(lons[-1]),
        int(round(total_time_s * 1000)),
        int(round(total_time_s * 1000)),
        int(round((distances[-1] - distances[0]) * 100)),
    ])

    # event: timer start
    body += _definition(4, _MSG_EVENT, event_fields)
    body += _data(4, event_fields, [base_ts, 0, 0])

    # records (downsampled GPS track)
    idx = _downsample_indices(n, _MAX_RECORDS)
    body += _definition(5, _MSG_RECORD, record_fields)
    prev_ts = -1
    for i in idx:
        d = float(distances[i])
        ts = base_ts + int(round(time_at(d)))
        if ts <= prev_ts:
            ts = prev_ts + 1
        prev_ts = ts
        alt_raw = int(round((float(alts[i]) + 500.0) * 5.0))
        alt_raw = min(max(alt_raw, 0), 0xFFFE)
        body += _data(5, record_fields, [
            ts,
            _semicircles(lats[i]), _semicircles(lons[i]),
            alt_raw,
            int(round(d * 100)),
        ])

    # course points (the actual power plan)
    plan = _course_point_plan(distances, lats, lons, power_sections)
    body += _definition(6, _MSG_COURSE_POINT, cp_fields)
    for mi, (d, lat, lon, power_w) in enumerate(plan):
        ts = base_ts + int(round(time_at(d)))
        body += _data(6, cp_fields, [
            mi,
            ts,
            _semicircles(lat), _semicircles(lon),
            int(round(d * 100)),
            0,                      # type = generic
            str(power_w),           # name = target watts
        ])

    # event: timer stop_all
    body += _data(4, event_fields, [end_ts, 0, 4])

    # ----- header + CRC ------------------------------------------------------
    header = bytearray(12)
    header[0] = 12                      # header size (no header CRC)
    header[1] = 0x20                     # protocol version 2.0
    struct.pack_into('<H', header, 2, 2140)   # profile version
    struct.pack_into('<I', header, 4, len(body))
    header[8:12] = b'.FIT'

    crc = _fit_crc(bytes(header))
    crc = _fit_crc(bytes(body), crc)

    out = bytes(header) + bytes(body) + struct.pack('<H', crc)

    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    with open(path, 'wb') as fh:
        fh.write(out)
    return path
